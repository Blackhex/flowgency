from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agency.configuration import (
    AgentProfilePatch,
    AgentRuntimePatch,
    ConfigConflictError,
    ToolPolicy,
    ValidationFailed,
    parse_config_canonical,
    patch_agent_profile,
    replace_agent_routines,
)
from agency.configuration.effective import resolve_effective_policy
from agency.configuration.models import MemorySelector
from agency.fs import ResourceBusyError
from agency.integrations import get_integration
from agency.jobs.authority import JobStore
from agency.memory import MemoryConflictError, resolve_memory_selector
from agency.web.dependencies import AgencyServices, get_services


router = APIRouter()

_TAB_LABELS = {
    "profile": "Profile",
    "blueprint": "Blueprint",
    "runtime": "Runtime",
    "routines": "Routines",
    "memory": "Memory",
    "activity": "Activity",
}


@dataclass(frozen=True)
class _ActivityItem:
    kind: str
    title: str
    href: str | None
    meta: str


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _group_context(request: Request, snapshot, group_id: str) -> dict[str, Any]:
    group = snapshot.config.groups[group_id]
    return {
        "group": group_id,
        "group_name": group.name,
        "groups": {key: value.name for key, value in snapshot.config.groups.items()},
        "agency_title": snapshot.config.agency.title,
        "admin_active": False,
        "workspaces": [workspace.model_dump(mode="json") for workspace in group.workspaces],
        "workspaces_available": bool(group.workspaces),
        "nav_open_observations": 0,
        "nav_actionable": 0,
        "nav_actionable_proposals": 0,
        "nav_agent_count": len(group.agents),
        "nav_running_decisions": 0,
        "show_tips": False,
        "tips_dismissed": [],
        "theme_css": _theme_css(request),
    }


def _get_snapshot_instance(snapshot, group_id: str, agent_id: str):
    try:
        group = snapshot.config.groups[group_id]
        instance = group.agents[agent_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    return group, instance


def _tab_links(group_id: str, agent_id: str, active_tab: str) -> list[dict[str, str | bool]]:
    links: list[dict[str, str | bool]] = []
    for key, label in _TAB_LABELS.items():
        links.append(
            {
                "key": key,
                "label": label,
                "href": f"/{group_id}/agents/{agent_id}/{key}",
                "current": key == active_tab,
            }
        )
    return links


def _issue_dicts(exc: ValidationFailed | tuple) -> list[dict[str, str]]:
    issues = exc.issues if isinstance(exc, ValidationFailed) else exc
    return [
        {
            "code": issue.code,
            "field": issue.field,
            "message": issue.message,
            "hint": issue.corrective_hint,
        }
        for issue in issues
    ]


def _memory_scope_label(selector: MemorySelector | None, channels) -> str:
    selected = selector or MemorySelector(scope="agent")
    if selected.scope == "run":
        return "Run memory"
    if selected.scope == "agent":
        return "Agent memory"
    if selected.scope == "group":
        return "Group memory"
    if selected.scope == "channel":
        channel = channels.get(selected.channel or "")
        display = channel.display_name if channel is not None else (selected.channel or "Channel")
        return f"Channel: {display}"
    return selected.scope.title()


def _preview_job_id(group_id: str, agent_id: str) -> str:
    return f"detail-{group_id}-{agent_id}"


def _resolve_tab_memory(snapshot, services: AgencyServices, group_id: str, agent_id: str, selector: MemorySelector | None):
    if services.memory_store is None:
        raise HTTPException(status_code=409, detail="Memory store unavailable")
    resolved = resolve_memory_selector(
        selector or MemorySelector(scope="agent"),
        job_id=_preview_job_id(group_id, agent_id),
        group_key=group_id,
        agent_name=agent_id,
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )
    return services.memory_store.ensure(resolved)


def _path_lines(paths: tuple[Path, ...]) -> list[str]:
    return [str(path.resolve(strict=False)).replace("\\", "/") for path in paths]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                return yaml.safe_load(parts[1]) or {}, parts[2].strip()
            except yaml.YAMLError:
                return {}, text
    return {}, text


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "**" in stripped:
            parts = stripped.split("**")
            if len(parts) >= 3 and parts[1].strip():
                return parts[1].strip().rstrip(".,;:!?")
    return fallback.replace("-", " ")


def _list_markdown_items(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        items.append(
            {
                **meta,
                "_slug": path.stem,
                "_title": _extract_title(body, path.stem),
                "_path": path,
            }
        )
    return items


def _recent_log_rows(group_id: str, group_path: Path, agent_id: str) -> list[dict[str, str]]:
    logs_root = group_path / "shared" / "logs"
    rows: list[dict[str, str]] = []
    if not logs_root.exists():
        return rows
    for day_dir in sorted((path for path in logs_root.iterdir() if path.is_dir()), reverse=True):
        for candidate in sorted(day_dir.iterdir(), reverse=True):
            if not candidate.name.startswith(f"{agent_id}-"):
                continue
            if candidate.suffix not in {".out", ".err"}:
                continue
            rows.append(
                {
                    "name": candidate.name,
                    "href": f"/{quote(group_id, safe='')}/logs/view?path={quote(str(candidate.resolve()))}",
                    "when": candidate.stat().st_mtime_ns,
                }
            )
            if len(rows) >= 8:
                return rows
    return rows


def _activity_items(group_id: str, group_path: Path, agent_id: str, job_store: JobStore | None) -> dict[str, Any]:
    observations = [
        _ActivityItem(
            kind="Observation",
            title=item.get("_title", item["_slug"]),
            href=f"/{group_id}/observations/{item['_slug']}",
            meta=str(item.get("status", "open")),
        )
        for item in _list_markdown_items(group_path / "shared" / "observations")
        if item.get("agent") == agent_id
    ]
    proposals = [
        _ActivityItem(
            kind="Proposal",
            title=item.get("_title", item["_slug"]),
            href=f"/{group_id}/proposals/{item['_slug']}",
            meta=str(item.get("status", "proposed")),
        )
        for item in _list_markdown_items(group_path / "shared" / "proposals")
        if item.get("origin_agent") == agent_id
    ]
    jobs = [
        {
            "id": record.spec.job_id,
            "status": record.status,
            "trigger": record.spec.trigger,
        }
        for record in (job_store.active(group_id, agent_id) if job_store is not None else ())
    ]
    return {
        "observations": observations[:8],
        "proposals": proposals[:8],
        "jobs": jobs[:8],
        "logs": _recent_log_rows(group_id, group_path, agent_id),
    }


def _selected_file(snapshot) -> str:
    if "memory.md" in snapshot.files:
        return "memory.md"
    return sorted(snapshot.files)[0]


def _read_selected_content(snapshot, filename: str) -> str:
    return snapshot.files.get(filename, b"").decode("utf-8")


def _memory_file_options(snapshot) -> list[str]:
    return sorted(snapshot.files)


def _parse_bool(form_value: Any) -> bool:
    return str(form_value).strip().lower() in {"1", "true", "yes", "on"}


def _split_lines(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(text or "").splitlines() if line.strip())


def _parse_tool_policy(form) -> ToolPolicy | None:
    mode = str(form.get("tool_mode", "")).strip() or "inherit"
    names = _split_lines(form.get("tool_names", ""))
    if mode == "inherit":
        return None
    return ToolPolicy(mode=mode, names=names)


def _apply_runtime_patch(raw: dict[str, Any], group_id: str, agent_id: str, patch: AgentRuntimePatch) -> None:
    groups = raw.setdefault("groups", {})
    group = groups[group_id]
    agents = group.setdefault("agents", [])
    target = None
    for entry in agents:
        if isinstance(entry, dict) and entry.get("name") == agent_id:
            target = entry
            break
    if target is None:
        raise KeyError(agent_id)
    runtime = target.setdefault("runtime", {})
    if patch.timeout is None:
        runtime.pop("timeout", None)
    else:
        runtime["timeout"] = patch.timeout
    sandbox = runtime.setdefault("sandbox", {})
    if patch.additional_roots:
        sandbox["additional_roots"] = list(patch.additional_roots)
    else:
        sandbox.pop("additional_roots", None)
    tools = runtime.setdefault("tools", {})
    if patch.tools is None:
        tools.pop("mode", None)
        tools.pop("names", None)
    else:
        tools["mode"] = patch.tools.mode
        if patch.tools.mode == "allowlist":
            tools["names"] = list(patch.tools.names)
        else:
            tools.pop("names", None)


def _patch_default_memory(raw: dict[str, Any], group_id: str, agent_id: str, selector: MemorySelector | None) -> None:
    target = None
    for entry in raw["groups"][group_id].setdefault("agents", []):
        if isinstance(entry, dict) and entry.get("name") == agent_id:
            target = entry
            break
    if target is None:
        raise KeyError(agent_id)
    if selector is None:
        target.pop("default_memory", None)
        return
    payload: dict[str, Any] = {"scope": selector.scope}
    if selector.channel:
        payload["channel"] = selector.channel
    target["default_memory"] = payload


def _memory_selector_token(selector: MemorySelector | None) -> str:
    selected = selector or MemorySelector(scope="agent")
    return selected.scope if selected.scope != "channel" else f"channel:{selected.channel or ''}"


def _parse_memory_selector_token(token: str, channels) -> MemorySelector | None:
    value = str(token or "").strip()
    if not value:
        return None
    if value.startswith("channel:"):
        channel = value.split(":", 1)[1].strip()
        selector = MemorySelector(scope="channel", channel=channel or None)
    else:
        selector = MemorySelector(scope=value, channel=None)
    if selector.scope == "channel" and selector.channel not in channels:
        raise ValidationFailed(
            (
                type("Issue", (), {
                    "code": "missing-memory-channel",
                    "field": "default_memory.channel",
                    "message": "Unknown memory channel.",
                    "corrective_hint": "Choose a declared memory channel.",
                })(),
            )
        )
    return selector


def _parse_memory_selector_from_form(form, channels) -> MemorySelector | None:
    scope = str(form.get("default_memory_scope", "")).strip() or "agent"
    channel = str(form.get("default_memory_channel", "")).strip() or None
    if scope == "inherit":
        return None
    selector = MemorySelector(scope=scope, channel=channel)
    if selector.scope == "channel" and selector.channel not in channels:
        raise ValidationFailed(
            (
                type("Issue", (), {
                    "code": "missing-memory-channel",
                    "field": "default_memory.channel",
                    "message": "Unknown memory channel.",
                    "corrective_hint": "Choose a declared memory channel.",
                })(),
            )
        )
    return selector


def _parse_routines_payload(form, blueprint_skills: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    raw = str(form.get("routines_json", "")).strip()
    if not raw:
        return [], []
    try:
        decoded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return [], [{"field": "routines_json", "message": "Routines payload must be valid YAML or JSON.", "hint": "Submit a list of routine mappings."}]
    if not isinstance(decoded, list):
        return [], [{"field": "routines_json", "message": "Routines payload must be a list.", "hint": "Submit an ordered list of routine mappings."}]
    issues: list[dict[str, str]] = []
    routines: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(decoded):
        field_prefix = f"routines[{index}]"
        if not isinstance(item, dict):
            issues.append({"field": field_prefix, "message": "Routine entry must be a mapping.", "hint": "Provide id, skill, schedule, and optional arguments."})
            continue
        routine_id = str(item.get("id", "")).strip()
        skill = str(item.get("skill", "")).strip()
        if not routine_id:
            issues.append({"field": f"{field_prefix}.id", "message": "Routine id is required.", "hint": "Provide a stable routine slug."})
        if routine_id in seen_ids:
            issues.append({"field": f"{field_prefix}.id", "message": f"Duplicate routine id: {routine_id}", "hint": "Keep each routine id unique within the agent."})
        seen_ids.add(routine_id)
        if skill not in blueprint_skills:
            issues.append({"field": f"{field_prefix}.skill", "message": "Routine skill must be selected from the blueprint.", "hint": "Choose one of the blueprint skills."})
        arguments = item.get("arguments", [])
        if not isinstance(arguments, list) or any(not isinstance(arg, str) or not arg.strip() for arg in arguments):
            issues.append({"field": f"{field_prefix}.arguments", "message": "Routine arguments must be an ordered list of non-empty strings.", "hint": "Provide each argument as its own string."})
            arguments = []
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            issues.append({"field": f"{field_prefix}.enabled", "message": "Routine enabled must be true or false.", "hint": "Use a YAML boolean."})
            enabled = True
        schedule = item.get("schedule") or {}
        if not isinstance(schedule, dict):
            issues.append({"field": f"{field_prefix}.schedule", "message": "Schedule must be a mapping with exactly one of at or every.", "hint": "Set either at or every."})
            schedule = {}
        has_at = bool(str(schedule.get("at", "")).strip())
        has_every = bool(str(schedule.get("every", "")).strip())
        if has_at == has_every:
            issues.append({"field": f"{field_prefix}.schedule", "message": "Schedule must define exactly one of at or every.", "hint": "Set one schedule mode only."})
        memory = item.get("memory")
        memory_payload = None
        if memory is not None:
            if not isinstance(memory, dict) or not str(memory.get("scope", "")).strip():
                issues.append({"field": f"{field_prefix}.memory", "message": "Memory selector must be a mapping with a scope.", "hint": "Use run, routine, agent, group, or channel."})
            else:
                memory_payload = {"scope": str(memory.get("scope", "")).strip()}
                if memory_payload["scope"] == "channel" and str(memory.get("channel", "")).strip():
                    memory_payload["channel"] = str(memory.get("channel", "")).strip()
        routines.append(
            {
                "id": routine_id,
                "skill": skill,
                "enabled": enabled,
                "arguments": [arg.strip() for arg in arguments if isinstance(arg, str) and arg.strip()],
                "schedule": {"at": str(schedule.get("at", "")).strip()} if has_at else {"every": str(schedule.get("every", "")).strip()} if has_every else {},
                **({"memory": memory_payload} if memory_payload is not None else {}),
            }
        )
    return routines, issues


def _runtime_context(snapshot, group_id: str, agent_id: str) -> dict[str, Any]:
    group, instance = _get_snapshot_instance(snapshot, group_id, agent_id)
    integration = get_integration(instance.integration)
    issues: list[dict[str, str]] = []
    effective = None
    effective_root_rows: list[dict[str, str]] = []
    try:
        effective = resolve_effective_policy(snapshot.config, group_id, agent_id)
        group_roots = tuple(group.runtime.sandbox.roots)
        additional_roots = tuple(instance.runtime.sandbox.additional_roots)
        group_keys = {str(path.resolve(strict=False)).lower(): path for path in group_roots}
        seen: set[str] = set()
        for root in effective.sandbox_roots:
            resolved = root.resolve(strict=False)
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            effective_root_rows.append(
                {
                    "path": str(resolved).replace("\\", "/"),
                    "source": "Group default" if key in group_keys else "Agent addition",
                }
            )
    except ValidationFailed as exc:
        issues = _issue_dicts(exc)
    group_tool_names = tuple(group.runtime.tools.names)
    instance_tool_names = tuple(instance.runtime.tools.names) if "tools" in instance.runtime.model_fields_set else ()
    return {
        "integration_name": instance.integration,
        "integration_display_name": integration.display_name,
        "group_timeout": group.runtime.timeout,
        "group_roots": _path_lines(tuple(group.runtime.sandbox.roots)),
        "group_tool_mode": group.runtime.tools.mode,
        "group_tool_names": "\n".join(group_tool_names),
        "agent_timeout": instance.runtime.timeout if "timeout" in instance.runtime.model_fields_set else "",
        "agent_additional_roots": "\n".join(_path_lines(tuple(instance.runtime.sandbox.additional_roots))),
        "agent_tool_mode": instance.runtime.tools.mode if "tools" in instance.runtime.model_fields_set else "inherit",
        "agent_tool_names": "\n".join(instance_tool_names),
        "effective": effective,
        "effective_root_rows": effective_root_rows,
        "issues": issues,
        "capabilities": integration.runtime_capabilities,
        "projector_capabilities": getattr(integration.projector, "capabilities", None),
    }


def _blueprint_context(services: AgencyServices, snapshot, group_id: str, agent_id: str) -> dict[str, Any]:
    _, instance = _get_snapshot_instance(snapshot, group_id, agent_id)
    if services.blueprint_library is None:
        raise HTTPException(status_code=409, detail="Blueprint library unavailable")
    inspection = services.blueprint_library.inspect(instance.blueprint)
    integration = get_integration(instance.integration)
    projector = integration.projector
    projector_capabilities = getattr(projector, "capabilities", None)
    cache_status = {"state": "unavailable", "path": "", "pins": ()}
    if services.compilation_cache is not None and projector is not None:
        artifact_path = services.compilation_cache.root / instance.integration / projector.version / inspection.snapshot.digest
        manifest_path = artifact_path / "manifest.json"
        cache_status = {
            "state": "compiled" if manifest_path.exists() else "missing",
            "path": str(artifact_path),
            "pins": (),
        }
    compatibility = {
        "instruction_target": projector_capabilities.instruction_target.as_posix() if projector_capabilities is not None else "",
        "skills_target": projector_capabilities.skills_target.as_posix() if projector_capabilities is not None else "",
        "discovers_skills": bool(getattr(projector_capabilities, "discovers_skills", False)),
        "activates_selected_skill": bool(getattr(projector_capabilities, "activates_selected_skill", False)),
    }
    return {
        "inspection": inspection,
        "compatibility": compatibility,
        "cache_status": cache_status,
        "edit_library_href": f"/admin/agent-library/blueprints/{inspection.key}",
        "edit_skills_href": f"/admin/agent-library/blueprints/{inspection.key}/skills",
    }


def _routines_context(services: AgencyServices, snapshot, group_id: str, agent_id: str) -> dict[str, Any]:
    _, instance = _get_snapshot_instance(snapshot, group_id, agent_id)
    inspection = (
        services.blueprint_library.inspect(instance.blueprint)
        if services.blueprint_library is not None
        else None
    )
    routines_yaml = yaml.safe_dump(
        [routine.model_dump(mode="json", exclude_none=True) for routine in instance.routines],
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return {
        "blueprint_skills": inspection.skills if inspection is not None else (),
        "routines_yaml": routines_yaml,
        "supports_enabled": True,
    }


def _memory_context(snapshot, services: AgencyServices, group_id: str, agent_id: str) -> dict[str, Any]:
    _, instance = _get_snapshot_instance(snapshot, group_id, agent_id)
    memory_snapshot = _resolve_tab_memory(snapshot, services, group_id, agent_id, instance.default_memory)
    selected_file = _selected_file(memory_snapshot)
    channel_options = [
        {"key": key, "label": channel.display_name}
        for key, channel in snapshot.config.memory.channels.items()
    ]
    return {
        "memory_snapshot": memory_snapshot,
        "default_memory_scope": (instance.default_memory.scope if instance.default_memory is not None else "agent"),
        "default_memory_channel": (instance.default_memory.channel if instance.default_memory is not None and instance.default_memory.channel else ""),
        "memory_scope_label": _memory_scope_label(instance.default_memory, snapshot.config.memory.channels),
        "selector_token": _memory_selector_token(instance.default_memory),
        "memory_file_options": _memory_file_options(memory_snapshot),
        "selected_memory_file": selected_file,
        "selected_memory_content": _read_selected_content(memory_snapshot, selected_file),
        "channel_options": channel_options,
    }


def _detail_context(
    request: Request,
    services: AgencyServices,
    group_id: str,
    agent_id: str,
    tab: str,
    *,
    status_code: int = 200,
    issues: list[dict[str, str]] | None = None,
    banner: str = "",
    memory_conflict: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
):
    snapshot = services.config_store.load()
    group, instance = _get_snapshot_instance(snapshot, group_id, agent_id)
    context: dict[str, Any] = {
        "request": request,
        **_group_context(request, snapshot, group_id),
        "active": "agents",
        "agent": agent_id,
        "tab": tab,
        "tab_label": _TAB_LABELS[tab],
        "tab_links": _tab_links(group_id, agent_id, tab),
        "config_revision": snapshot.revision,
        "agent_name": instance.name,
        "display_name": instance.identity.display_name or instance.name,
        "title": instance.identity.title,
        "emoji": instance.identity.emoji,
        "integration": instance.integration,
        "blueprint": instance.blueprint,
        "can_write": instance.capabilities.write,
        "issues": issues or [],
        "banner": banner,
        "memory_conflict": memory_conflict,
        "tab_template": f"agent_detail_{tab}.html",
    }
    if tab == "profile":
        context.update(
            {
                "profile_form": {
                    "display_name": instance.identity.display_name,
                    "title": instance.identity.title,
                    "emoji": instance.identity.emoji,
                    "can_write": instance.capabilities.write,
                }
            }
        )
    elif tab == "blueprint":
        context.update(_blueprint_context(services, snapshot, group_id, agent_id))
    elif tab == "runtime":
        context.update(_runtime_context(snapshot, group_id, agent_id))
    elif tab == "routines":
        context.update(_routines_context(services, snapshot, group_id, agent_id))
    elif tab == "memory":
        context.update(_memory_context(snapshot, services, group_id, agent_id))
    elif tab == "activity":
        context.update(_activity_items(group_id, group.path, agent_id, services.job_store))
    if overrides:
        context.update(overrides)
    return _templates(request).TemplateResponse(request, "agent_detail.html", context, status_code=status_code)


@router.get("/{group}/agents/{agent}", response_class=HTMLResponse)
async def agent_detail_base(group: str, agent: str):
    return RedirectResponse(f"/{group}/agents/{agent}/profile", status_code=303)


@router.get("/{group}/agents/{agent}/profile", response_class=HTMLResponse)
async def agent_detail_profile(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    return _detail_context(request, services, group, agent, "profile")


@router.post("/{group}/agents/{agent}/profile", response_class=HTMLResponse)
async def agent_detail_profile_save(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    try:
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        patch_agent_profile(
            services.config_store,
            revision,
            group,
            agent,
            AgentProfilePatch(
                display_name=str(form.get("display_name", "")).strip(),
                title=str(form.get("title", "")).strip(),
                emoji=str(form.get("emoji", "")).strip(),
                can_write=_parse_bool(form.get("can_write", "")),
            ),
        )
    except ValidationFailed as exc:
        return _detail_context(request, services, group, agent, "profile", status_code=409, issues=_issue_dicts(exc))
    except ConfigConflictError as exc:
        return _detail_context(request, services, group, agent, "profile", status_code=409, banner=str(exc))
    request.app.state.refresh_services()
    return RedirectResponse(f"/{group}/agents/{agent}/profile", status_code=303)


@router.get("/{group}/agents/{agent}/blueprint", response_class=HTMLResponse)
async def agent_detail_blueprint(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    return _detail_context(request, services, group, agent, "blueprint")


@router.get("/{group}/agents/{agent}/runtime", response_class=HTMLResponse)
async def agent_detail_runtime(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    return _detail_context(request, services, group, agent, "runtime")


@router.post("/{group}/agents/{agent}/runtime", response_class=HTMLResponse)
async def agent_detail_runtime_save(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    timeout_text = str(form.get("timeout", "")).strip()
    patch = AgentRuntimePatch(
        timeout=int(timeout_text) if timeout_text else None,
        additional_roots=_split_lines(form.get("additional_roots", "")),
        tools=_parse_tool_policy(form),
    )
    try:
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        current = services.config_store.load()
        raw = deepcopy(current.raw)
        _apply_runtime_patch(raw, group, agent, patch)
        parsed = parse_config_canonical(raw, current.path).resolved
        resolve_effective_policy(parsed, group, agent)
        services.config_store.replace(revision, raw)
    except ValueError as exc:
        issues = (
            [
                {
                    "field": "runtime.timeout",
                    "message": str(exc),
                    "hint": "Set timeout to a whole number or leave it blank to inherit.",
                }
            ]
            if "invalid literal" in str(exc)
            else [
                {
                    "field": "runtime",
                    "message": str(exc),
                    "hint": "Fix the runtime form values and try again.",
                }
            ]
        )
        return _detail_context(request, services, group, agent, "runtime", status_code=409, issues=issues)
    except ValidationFailed as exc:
        return _detail_context(request, services, group, agent, "runtime", status_code=409, issues=_issue_dicts(exc))
    except ConfigConflictError as exc:
        return _detail_context(request, services, group, agent, "runtime", status_code=409, banner=str(exc))
    request.app.state.refresh_services()
    return RedirectResponse(f"/{group}/agents/{agent}/runtime", status_code=303)


@router.get("/{group}/agents/{agent}/routines", response_class=HTMLResponse)
async def agent_detail_routines(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    return _detail_context(request, services, group, agent, "routines")


@router.post("/{group}/agents/{agent}/routines", response_class=HTMLResponse)
async def agent_detail_routines_save(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    snapshot = services.config_store.load()
    _, instance = _get_snapshot_instance(snapshot, group, agent)
    inspection = (
        services.blueprint_library.inspect(instance.blueprint)
        if services.blueprint_library is not None
        else None
    )
    skills = inspection.skills if inspection is not None else ()
    routines, parse_issues = _parse_routines_payload(form, skills)
    if parse_issues:
        return _detail_context(request, services, group, agent, "routines", status_code=409, issues=parse_issues, overrides={"routines_yaml": str(form.get("routines_json", "")).strip(), "blueprint_skills": skills, "supports_enabled": True})
    try:
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        replace_agent_routines(services.config_store, revision, group, agent, routines)
    except ValidationFailed as exc:
        return _detail_context(request, services, group, agent, "routines", status_code=409, issues=_issue_dicts(exc), overrides={"routines_yaml": str(form.get("routines_json", "")).strip(), "blueprint_skills": skills, "supports_enabled": True})
    except ConfigConflictError as exc:
        return _detail_context(request, services, group, agent, "routines", status_code=409, banner=str(exc), overrides={"routines_yaml": str(form.get("routines_json", "")).strip(), "blueprint_skills": skills, "supports_enabled": True})
    request.app.state.refresh_services()
    return RedirectResponse(f"/{group}/agents/{agent}/routines", status_code=303)


@router.get("/{group}/agents/{agent}/memory", response_class=HTMLResponse)
async def agent_detail_memory(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    return _detail_context(request, services, group, agent, "memory")


@router.post("/{group}/agents/{agent}/memory", response_class=HTMLResponse)
async def agent_detail_memory_save(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    form = await request.form()
    action = str(form.get("action", "")).strip() or "content"
    revision = str(form.get("revision", "")).strip()
    content_revision = str(form.get("content_revision", "")).strip()
    filename = str(form.get("filename", "memory.md")).strip() or "memory.md"
    content = str(form.get("content", ""))
    try:
        snapshot = services.config_store.load()
        if action == "selector":
            selector = _parse_memory_selector_from_form(form, snapshot.config.memory.channels)
            if not revision:
                raise ConfigConflictError("config.yaml changed; reload before saving")
            raw = deepcopy(snapshot.raw)
            _patch_default_memory(raw, group, agent, selector)
            parse_config_canonical(raw, snapshot.path)
            services.config_store.replace(revision, raw)
        elif action == "content":
            _, instance = _get_snapshot_instance(snapshot, group, agent)
            selector_token = str(form.get("selector_token", "")).strip()
            selector = _parse_memory_selector_token(selector_token, snapshot.config.memory.channels)
            effective_selector = instance.default_memory
            if selector is not None:
                current_token = _memory_selector_token(instance.default_memory)
                if selector_token != current_token:
                    raise ConfigConflictError("memory selector changed; reload before saving")
                effective_selector = selector
            resolved = resolve_memory_selector(
                effective_selector or MemorySelector(scope="agent"),
                job_id=_preview_job_id(group, agent),
                group_key=group,
                agent_name=agent,
                routine_id=None,
                channels=snapshot.config.memory.channels,
                store_root=services.memory_store.root,
            )
            services.memory_store.try_update(
                resolved,
                content_revision,
                lambda current: {
                    **current.files,
                    filename: content.encode("utf-8"),
                },
            )
        else:
            raise ValidationFailed(
                (
                    type("Issue", (), {
                        "code": "invalid-memory-action",
                        "field": "action",
                        "message": "Unknown memory action.",
                        "corrective_hint": "Submit either selector or content.",
                    })(),
                )
            )
    except ResourceBusyError:
        return _detail_context(
            request,
            services,
            group,
            agent,
            "memory",
            status_code=423,
            banner="Memory is busy; try again after the active writer finishes.",
            overrides={
                "selector_token": str(form.get("selector_token", "")).strip(),
                "selected_memory_file": filename,
                "selected_memory_content": content,
            },
        )
    except MemoryConflictError as exc:
        return _detail_context(
            request,
            services,
            group,
            agent,
            "memory",
            status_code=409,
            banner=str(exc),
            memory_conflict={
                "current_revision": exc.current.revision,
                "attempted_revision": exc.expected_revision,
                "current_content": _read_selected_content(exc.current, filename),
                "attempted_content": content,
            },
            overrides={
                "selector_token": str(form.get("selector_token", "")).strip(),
                "selected_memory_file": filename,
                "selected_memory_content": content,
            },
        )
    except ValidationFailed as exc:
        return _detail_context(request, services, group, agent, "memory", status_code=409, issues=_issue_dicts(exc), overrides={"selector_token": str(form.get("selector_token", "")).strip(), "selected_memory_file": filename, "selected_memory_content": content})
    except ConfigConflictError as exc:
        return _detail_context(request, services, group, agent, "memory", status_code=409, banner=str(exc), overrides={"selector_token": str(form.get("selector_token", "")).strip(), "selected_memory_file": filename, "selected_memory_content": content})
    request.app.state.refresh_services()
    return RedirectResponse(f"/{group}/agents/{agent}/memory", status_code=303)


@router.get("/{group}/agents/{agent}/activity", response_class=HTMLResponse)
async def agent_detail_activity(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    return _detail_context(request, services, group, agent, "activity")