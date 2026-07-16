from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agency.configuration import ValidationFailed
from agency.fs.locks import exclusive_lock
from agency.fs.snapshot import AssetValidationError
from agency.integrations import get_integration
from agency.web.dependencies import AgencyServices, get_services


router = APIRouter()

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    file_attributes = getattr(stat_result, "st_file_attributes", 0) or 0
    return bool(
        stat.S_ISLNK(stat_result.st_mode)
        or file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _ensure_directory(path: Path, *, label: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Missing {label} directory: {path}",
        ) from exc
    if _is_symlink_or_reparse(path):
        raise HTTPException(
            status_code=409,
            detail=f"Unsafe {label} directory: {path}",
        )
    if not stat.S_ISDIR(stat_result.st_mode):
        raise HTTPException(
            status_code=409,
            detail=f"{label} path is not a directory: {path}",
        )
    return path


def _ensure_child_directory(parent: Path, name: str, *, label: str) -> Path:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or PurePosixPath(name).is_absolute()
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Unsafe {label} segment: {name}",
        )
    candidate = _ensure_directory(parent, label=f"{label} parent") / name
    return _ensure_directory(candidate, label=label)


def _safe_key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _library_infra_root(services: AgencyServices) -> Path:
    library_root = _require_library(services).root.resolve()
    infra_root = _ensure_child_directory(
        library_root.parent,
        ".agency-agent-library",
        label="Agent Library infrastructure root",
    )
    return _ensure_child_directory(
        infra_root,
        hashlib.sha256(str(library_root).encode("utf-8")).hexdigest(),
        label="Agent Library infrastructure root",
    )


def _infra_bucket(services: AgencyServices, name: str) -> Path:
    return _ensure_child_directory(
        _library_infra_root(services),
        name,
        label=f"Agent Library {name}",
    )


def _create_verified_tempdir(parent: Path, *, prefix: str, label: str) -> Path:
    created = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
    return _ensure_directory(created, label=label)


def _base_admin_context(request: Request, snapshot) -> dict[str, Any]:
    return {
        "request": request,
        "agency_title": snapshot.config.agency.title,
        "admin_active": True,
        "active": "admin",
        "admin_page": "agent-library",
        "theme_css": _theme_css(request),
        "groups": {
            key: group.name for key, group in snapshot.config.groups.items()
        },
    }


def _issue_dicts(exc: ValidationFailed) -> list[dict[str, str]]:
    return [
        {
            "code": issue.code,
            "field": issue.field,
            "message": issue.message,
            "hint": issue.corrective_hint,
        }
        for issue in exc.issues
    ]


def _require_library(services: AgencyServices):
    if services.blueprint_library is None:
        raise HTTPException(
            status_code=409,
            detail="Blueprint library unavailable",
        )
    return services.blueprint_library


def _blueprint_root(services: AgencyServices, key: str) -> Path:
    return _require_library(services).root / key


def _load_blueprint(services: AgencyServices, key: str):
    root = _blueprint_root(services, key)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Unknown blueprint")
    return _require_library(services).inspect(key)


def _instance_users(snapshot, blueprint_key: str) -> list[dict[str, str]]:
    users: list[dict[str, str]] = []
    for group_key, group in snapshot.config.groups.items():
        for agent_key, agent in group.agents.items():
            if agent.blueprint != blueprint_key:
                continue
            display_name = agent.identity.display_name or agent_key
            users.append(
                {
                    "group": group.name,
                    "agent": display_name,
                    "href": f"/{group_key}/agents/{agent_key}/blueprint",
                }
            )
    users.sort(key=lambda item: (item["group"], item["agent"]))
    return users


def _cache_status(
    services: AgencyServices,
    inspection,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in sorted(services.integrations):
        integration = get_integration(name)
        projector = integration.projector
        if projector is None:
            continue
        capabilities = projector.capabilities
        state = "missing"
        if services.compilation_cache is not None:
            manifest_path = (
                services.compilation_cache.root
                / name
                / projector.version
                / inspection.snapshot.digest
                / "manifest.json"
            )
            if manifest_path.exists():
                state = "compiled"
        routine_compatibility = "Instructions only"
        if (
            capabilities.discovers_skills
            and capabilities.activates_selected_skill
        ):
            routine_compatibility = "Full"
        elif (
            capabilities.discovers_skills
            or capabilities.activates_selected_skill
        ):
            routine_compatibility = "Partial"
        rows.append(
            {
                "integration": name,
                "display_name": integration.display_name,
                "projector_version": projector.version,
                "instruction_target": (
                    capabilities.instruction_target.as_posix()
                ),
                "skills_target": capabilities.skills_target.as_posix(),
                "discovers_skills": (
                    "Yes" if capabilities.discovers_skills else "No"
                ),
                "activates_selected_skill": (
                    "Yes"
                    if capabilities.activates_selected_skill
                    else "No"
                ),
                "routine_compatibility": routine_compatibility,
                "cache_state": state,
            }
        )
    return rows


def _skill_files(inspection, skill_name: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    prefix = PurePosixPath(".agents", "skills", skill_name)
    for item in inspection.snapshot.files:
        if item.path.parts[:3] != prefix.parts:
            continue
        files.append(
            {
                "path": item.path.as_posix(),
                "name": item.path.name,
                "content": item.content.decode("utf-8"),
            }
        )
    files.sort(key=lambda item: (item["name"] != "SKILL.md", item["path"]))
    return files


def _skill_resources(inspection) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for skill_name in inspection.skills:
        files = _skill_files(inspection, skill_name)
        rows.append(
            {
                "skill": skill_name,
                "files": files,
            }
        )
    return rows


def _selected_skill_file(
    skill_files: list[dict[str, str]],
    requested: str | None,
) -> dict[str, str] | None:
    if not skill_files:
        return None
    if requested:
        for item in skill_files:
            if item["path"] == requested or item["name"] == requested:
                return item
    return skill_files[0]


def _render_library_list(
    request: Request,
    services: AgencyServices,
    snapshot,
    *,
    warning: str = "",
    status_code: int = 200,
):
    blueprints = ()
    if not warning:
        library = _require_library(services)
        root = library.root
        try:
            if not root.exists():
                raise FileNotFoundError(
                    f"Agent Library root does not exist: {root}"
                )
            if not root.is_dir():
                raise NotADirectoryError(
                    f"Agent Library root is not a directory: {root}"
                )
            blueprints = library.list()
        except (
            AssetValidationError,
            FileNotFoundError,
            NotADirectoryError,
            OSError,
        ) as exc:
            warning = str(exc)
            status_code = 409
    rows = []
    for inspection in blueprints:
        users = _instance_users(snapshot, inspection.key)
        rows.append(
            {
                "key": inspection.key,
                "title": inspection.title,
                "skills": inspection.skills,
                "digest": inspection.snapshot.digest,
                "user_count": len(users),
            }
        )
    return _templates(request).TemplateResponse(
        request,
        "admin_agent_library.html",
        {
            **_base_admin_context(request, snapshot),
            "blueprints": rows,
            "warning": warning,
        },
        status_code=status_code,
    )


def _render_blueprint_detail(
    request: Request,
    services: AgencyServices,
    snapshot,
    key: str,
    *,
    warning: str = "",
    issues: list[dict[str, str]] | None = None,
    form_path: str = "AGENTS.md",
    form_content: str | None = None,
    status_code: int = 200,
):
    inspection = _load_blueprint(services, key)
    agents_file = inspection.snapshot.file("AGENTS.md")
    users = _instance_users(snapshot, key)
    return _templates(request).TemplateResponse(
        request,
        "admin_blueprint_detail.html",
        {
            **_base_admin_context(request, snapshot),
            "blueprint": inspection,
            "users": users,
            "skill_resources": _skill_resources(inspection),
            "compatibility_rows": _cache_status(services, inspection),
            "warning": warning,
            "issues": issues or [],
            "form_path": form_path,
            "form_content": (
                form_content
                if form_content is not None
                else agents_file.content.decode("utf-8")
            ),
            "user_summary": (
                f"Used by {len(users)} instance"
                + ("s" if len(users) != 1 else "")
            ),
        },
        status_code=status_code,
    )


def _render_blueprint_skill(
    request: Request,
    services: AgencyServices,
    snapshot,
    key: str,
    skill_name: str | None,
    *,
    selected_path: str | None = None,
    warning: str = "",
    issues: list[dict[str, str]] | None = None,
    form_content: str | None = None,
    status_code: int = 200,
):
    inspection = _load_blueprint(services, key)
    active_skill = (
        skill_name
        or (inspection.skills[0] if inspection.skills else None)
    )
    if active_skill is None:
        raise HTTPException(status_code=404, detail="Blueprint has no skills")
    if active_skill not in inspection.skills:
        raise HTTPException(status_code=404, detail="Unknown skill")
    files = _skill_files(inspection, active_skill)
    selected = _selected_skill_file(files, selected_path)
    if selected is None:
        raise HTTPException(status_code=404, detail="Unknown skill file")
    return _templates(request).TemplateResponse(
        request,
        "admin_blueprint_skill.html",
        {
            **_base_admin_context(request, snapshot),
            "blueprint": inspection,
            "active_skill": active_skill,
            "skill_files": files,
            "selected_file": selected,
            "warning": warning,
            "issues": issues or [],
            "form_path": selected["path"],
            "form_content": (
                form_content
                if form_content is not None
                else selected["content"]
            ),
        },
        status_code=status_code,
    )


def _lock_path(services: AgencyServices, key: str) -> Path:
    return _infra_bucket(services, "locks") / f"{_safe_key_hash(key)}.lock"


def _validate_source_path(path_value: str) -> str:
    candidate = PurePosixPath(path_value.replace("\\", "/"))
    if (
        not path_value
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValidationFailed(
            (
                type(
                    "Issue",
                    (),
                    {
                        "code": "invalid-blueprint-path",
                        "field": "path",
                        "message": (
                            "Only AGENTS.md and files inside one "
                            "skill tree can be edited."
                        ),
                        "corrective_hint": (
                            "Pick AGENTS.md or a file under "
                            ".agents/skills/<name>/ ."
                        ),
                    },
                )(),
            )
        )
    if candidate.as_posix() == "AGENTS.md":
        return candidate.as_posix()
    if (
        len(candidate.parts) < 4
        or candidate.parts[:2] != (".agents", "skills")
    ):
        raise ValidationFailed(
            (
                type(
                    "Issue",
                    (),
                    {
                        "code": "invalid-blueprint-path",
                        "field": "path",
                        "message": (
                            "Only AGENTS.md and files inside one "
                            "skill tree can be edited."
                        ),
                        "corrective_hint": (
                            "Pick AGENTS.md or a file under "
                            ".agents/skills/<name>/ ."
                        ),
                    },
                )(),
            )
        )
    if not _IDENTIFIER_PATTERN.fullmatch(candidate.parts[2]):
        raise ValidationFailed(
            (
                type(
                    "Issue",
                    (),
                    {
                        "code": "invalid-skill-name",
                        "field": "path",
                        "message": (
                            "Skill directory must be a lowercase "
                            "stable slug."
                        ),
                        "corrective_hint": (
                            "Use .agents/skills/<lowercase-slug>/ "
                            "for editable skill files."
                        ),
                    },
                )(),
            )
        )
    return candidate.as_posix()


def _stage_blueprint(
    inspection,
    target_path: str,
    content: bytes,
    stage_root: Path,
) -> None:
    for item in inspection.snapshot.files:
        destination = stage_root / Path(*item.path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            content
            if item.path.as_posix() == target_path
            else item.content
        )
        destination.write_bytes(payload)
    if target_path not in {
        item.path.as_posix() for item in inspection.snapshot.files
    }:
        destination = stage_root / Path(*PurePosixPath(target_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _publish_stage(
    target_root: Path,
    stage_root: Path,
    backup_root: Path,
) -> None:
    backup_target = backup_root / target_root.name
    try:
        if target_root.exists():
            os.replace(target_root, backup_target)
        os.replace(stage_root, target_root)
    except Exception:
        if not target_root.exists() and backup_target.exists():
            os.replace(backup_target, target_root)
        raise
    finally:
        if backup_target.exists():
            shutil.rmtree(backup_target, ignore_errors=True)


def _redirect_after_save(key: str, edited_path: str) -> str:
    candidate = PurePosixPath(edited_path)
    if candidate.as_posix() == "AGENTS.md":
        return f"/admin/agent-library/blueprints/{key}"
    return (
        f"/admin/agent-library/blueprints/{key}/skills/"
        f"{candidate.parts[2]}?path={candidate.as_posix()}"
    )


@router.get("/admin/agent-library", response_class=HTMLResponse)
async def admin_agent_library(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    return _render_library_list(request, services, snapshot)


@router.get(
    "/admin/agent-library/blueprints/{key}",
    response_class=HTMLResponse,
)
async def admin_blueprint_detail(
    request: Request,
    key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    return _render_blueprint_detail(request, services, snapshot, key)


@router.get(
    "/admin/agent-library/blueprints/{key}/skills",
    response_class=HTMLResponse,
)
async def admin_blueprint_skills(
    request: Request,
    key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    selected_path = request.query_params.get("path")
    return _render_blueprint_skill(
        request,
        services,
        snapshot,
        key,
        None,
        selected_path=selected_path,
    )


@router.get(
    "/admin/agent-library/blueprints/{key}/skills/{skill}",
    response_class=HTMLResponse,
)
async def admin_blueprint_skill_detail(
    request: Request,
    key: str,
    skill: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    selected_path = request.query_params.get("path")
    return _render_blueprint_skill(
        request,
        services,
        snapshot,
        key,
        skill,
        selected_path=selected_path,
    )


@router.post(
    "/admin/agent-library/blueprints/{key}/source",
    response_class=HTMLResponse,
)
async def admin_blueprint_source_save(
    request: Request,
    key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    form = await request.form()
    expected_digest = str(form.get("expected_digest", "")).strip()
    raw_path = str(form.get("path", "")).strip()
    content = str(form.get("content", "")).encode("utf-8")
    try:
        target_path = _validate_source_path(raw_path)
        with exclusive_lock(_lock_path(services, key), wait=True):
            inspection = _load_blueprint(services, key)
            if inspection.snapshot.digest != expected_digest:
                raise HTTPException(
                    status_code=409,
                    detail="Blueprint source changed; reload before saving",
                )
            stage_parent = _create_verified_tempdir(
                _infra_bucket(services, "staging"),
                prefix=f".{_safe_key_hash(key)}.stage-",
                label="Agent Library staging",
            )
            stage_root = stage_parent / key
            stage_root.mkdir(parents=True, exist_ok=True)
            backup_parent = _create_verified_tempdir(
                _infra_bucket(services, "backups"),
                prefix=f".{_safe_key_hash(key)}.backup-",
                label="Agent Library backup",
            )
            try:
                _stage_blueprint(inspection, target_path, content, stage_root)
                _require_library(services).inspect(key)
                from agency.blueprints.library import inspect_blueprint

                inspect_blueprint(stage_parent, key)
                _publish_stage(
                    _blueprint_root(services, key),
                    stage_root,
                    backup_parent,
                )
            finally:
                if stage_parent.exists():
                    shutil.rmtree(stage_parent, ignore_errors=True)
                if backup_parent.exists():
                    shutil.rmtree(backup_parent, ignore_errors=True)
    except HTTPException as exc:
        if raw_path.startswith(".agents/skills/"):
            parts = PurePosixPath(raw_path.replace("\\", "/")).parts
            skill_name = parts[2] if len(parts) >= 3 else None
            return _render_blueprint_skill(
                request,
                services,
                snapshot,
                key,
                skill_name,
                selected_path=raw_path,
                warning=str(exc.detail),
                form_content=str(form.get("content", "")),
                status_code=exc.status_code,
            )
        return _render_blueprint_detail(
            request,
            services,
            snapshot,
            key,
            warning=str(exc.detail),
            form_path=raw_path or "AGENTS.md",
            form_content=str(form.get("content", "")),
            status_code=exc.status_code,
        )
    except ValidationFailed as exc:
        if raw_path.startswith(".agents/skills/"):
            parts = PurePosixPath(raw_path.replace("\\", "/")).parts
            skill_name = parts[2] if len(parts) >= 3 else None
            return _render_blueprint_skill(
                request,
                services,
                snapshot,
                key,
                skill_name,
                selected_path=raw_path,
                issues=_issue_dicts(exc),
                form_content=str(form.get("content", "")),
                status_code=409,
            )
        return _render_blueprint_detail(
            request,
            services,
            snapshot,
            key,
            issues=_issue_dicts(exc),
            form_path=raw_path or "AGENTS.md",
            form_content=str(form.get("content", "")),
            status_code=409,
        )
    return RedirectResponse(
        _redirect_after_save(key, raw_path),
        status_code=303,
    )
