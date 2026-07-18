from __future__ import annotations

import shutil
from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agency.configuration import (
    ConfigConflictError,
    ValidationFailed,
    parse_config_canonical,
)
from agency.configuration.models import MemorySelector
from agency.fs import ResourceBusyError
from agency.jobs.authority import JobStore
from agency.jobs.store import revision_bound_group_operation
from agency.memory import MemoryConflictError, resolve_memory_selector
from agency.memory.store import (
    _ensure_infrastructure_directory,
    _is_symlink_or_reparse,
)
from agency.web.dependencies import AgencyServices, get_services


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _base_admin_context(request: Request, snapshot) -> dict[str, Any]:
    return {
        "request": request,
        "agency_title": snapshot.config.agency.title,
        "admin_active": True,
        "active": "admin",
        "admin_page": "memory-channels",
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


def _channel_references(snapshot, channel_key: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for group_key, group in snapshot.config.groups.items():
        for agent_key, agent in group.agents.items():
            display_name = agent.identity.display_name or agent_key
            if (
                agent.default_memory is not None
                and agent.default_memory.scope == "channel"
                and agent.default_memory.channel == channel_key
            ):
                refs.append(
                    {
                        "label": f"{group.name} / {display_name}",
                        "href": f"/{group_key}/agents/{agent_key}/memory",
                    }
                )
            for routine in agent.routines:
                if (
                    routine.memory is not None
                    and routine.memory.scope == "channel"
                    and routine.memory.channel == channel_key
                ):
                    refs.append(
                        {
                            "label": (
                                f"{group.name} / {display_name} / "
                                f"{routine.id}"
                            ),
                            "href": (
                                f"/{group_key}/agents/"
                                f"{agent_key}/routines"
                            ),
                        }
                    )
    refs.sort(key=lambda item: item["label"])
    return refs


def _resolve_channel_memory(
    snapshot,
    services: AgencyServices,
    channel_key: str,
):
    if services.memory_store is None:
        raise HTTPException(status_code=409, detail="Memory store unavailable")
    if channel_key not in snapshot.config.memory.channels:
        raise HTTPException(status_code=404, detail="Unknown memory channel")
    return resolve_memory_selector(
        MemorySelector(scope="channel", channel=channel_key),
        job_id="admin-memory-channel",
        group_key="admin",
        agent_name="channel",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )


def _render_channel_list(
    request: Request,
    services: AgencyServices,
    snapshot,
    *,
    warning: str = "",
    issues: list[dict[str, str]] | None = None,
    status_code: int = 200,
):
    rows = []
    for key, channel in snapshot.config.memory.channels.items():
        refs = _channel_references(snapshot, key)
        rows.append(
            {
                "key": key,
                "display_name": channel.display_name,
                "reference_count": len(refs),
            }
        )
    return _templates(request).TemplateResponse(
        request,
        "admin_memory_channels.html",
        {
            **_base_admin_context(request, snapshot),
            "channels": rows,
            "revision": snapshot.revision,
            "warning": warning,
            "issues": issues or [],
        },
        status_code=status_code,
    )


def _render_channel_detail(
    request: Request,
    services: AgencyServices,
    snapshot,
    channel_key: str,
    *,
    warning: str = "",
    issues: list[dict[str, str]] | None = None,
    content_warning: str = "",
    memory_conflict: dict[str, str] | None = None,
    filename: str = "memory.md",
    content_override: str | None = None,
    status_code: int = 200,
):
    resolved = _resolve_channel_memory(snapshot, services, channel_key)
    memory_snapshot = services.memory_store.ensure(resolved)
    selected_name = (
        filename
        if filename in memory_snapshot.files
        else next(iter(memory_snapshot.files), "memory.md")
    )
    selected_content = (
        content_override
        if content_override is not None
        else memory_snapshot.files.get(selected_name, b"").decode("utf-8")
    )
    references = _channel_references(snapshot, channel_key)
    return _templates(request).TemplateResponse(
        request,
        "admin_memory_channel.html",
        {
            **_base_admin_context(request, snapshot),
            "channel_key": channel_key,
            "channel": snapshot.config.memory.channels[channel_key],
            "revision": snapshot.revision,
            "memory_snapshot": memory_snapshot,
            "references": references,
            "warning": warning,
            "issues": issues or [],
            "content_warning": content_warning,
            "memory_conflict": memory_conflict,
            "selected_filename": selected_name,
            "selected_content": selected_content,
        },
        status_code=status_code,
    )


def _patch_channels(
    raw: dict[str, Any],
    channels: dict[str, dict[str, str]],
) -> None:
    raw.setdefault("memory", {})["channels"] = channels


def _validate_channel_key(channel_key: str) -> None:
    valid_chars = all(
        part.islower() or part.isdigit() or part == "-"
        for part in channel_key
    )
    if (
        not channel_key
        or not valid_chars
        or channel_key.startswith("-")
        or channel_key.endswith("-")
        or "--" in channel_key
    ):
        raise ValidationFailed(
            (
                type(
                    "Issue",
                    (),
                    {
                        "code": "invalid-channel-key",
                        "field": "channel_key",
                        "message": (
                            "Channel keys must be lowercase "
                            "stable slugs."
                        ),
                        "corrective_hint": (
                            "Use lowercase letters, digits, "
                            "and single hyphens."
                        ),
                    },
                )(),
            )
        )


def _remove_channel_directory(services: AgencyServices, resolved) -> None:
    if resolved.directory.exists():
        shutil.rmtree(resolved.directory, ignore_errors=True)


def _active_channel_jobs(snapshot, channel_key: str) -> list[str]:
    references: list[str] = []
    job_store = JobStore(snapshot.config.agency.memory_store)
    for group_key, group in snapshot.config.groups.items():
        for record in job_store.active(group_key):
            selector = dict(record.spec.memory.selector)
            if (
                selector.get("scope") == "channel"
                and selector.get("channel") == channel_key
            ):
                references.append(
                    f"{group.name} / {record.spec.agent_name} / "
                    f"{record.spec.job_id}"
                )
    references.sort()
    return references


def _archive_channel_directory(services: AgencyServices, resolved):
    if not resolved.directory.exists():
        return None
    if _is_symlink_or_reparse(resolved.directory):
        raise HTTPException(
            status_code=500,
            detail="Unsafe memory channel directory",
        )
    archive_root = _ensure_infrastructure_directory(
        services.memory_store.root,
        [".deleted"],
        label="deleted",
    )
    archive_name = f"{resolved.memory_hash}-{uuid4().hex}"
    archive_path = archive_root / archive_name
    resolved.directory.rename(archive_path)
    return archive_path


def _restore_channel_archive(resolved, archive_path):
    if archive_path is None or not archive_path.exists():
        return
    archive_path.rename(resolved.directory)


@router.get("/admin/memory-channels", response_class=HTMLResponse)
async def admin_memory_channels(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    return _render_channel_list(request, services, snapshot)


@router.post("/admin/memory-channels/create", response_class=HTMLResponse)
async def admin_memory_channel_create(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    channel_key = str(form.get("channel_key", "")).strip()
    display_name = str(form.get("display_name", "")).strip()
    try:
        _validate_channel_key(channel_key)
        if not revision:
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        raw = deepcopy(snapshot.raw)
        channels = dict(raw.get("memory", {}).get("channels", {}))
        if channel_key in channels:
            raise ValidationFailed(
                (
                    type(
                        "Issue",
                        (),
                        {
                            "code": "duplicate-channel-key",
                            "field": "channel_key",
                            "message": "Memory channel key already exists.",
                            "corrective_hint": (
                                "Choose a unique global channel key."
                            ),
                        },
                    )(),
                )
            )
        channels[channel_key] = {"display_name": display_name or channel_key}
        _patch_channels(raw, channels)
        parse_config_canonical(raw, snapshot.path)
        services.config_store.replace(revision, raw)
    except ValidationFailed as exc:
        return _render_channel_list(
            request,
            services,
            snapshot,
            issues=_issue_dicts(exc),
            status_code=409,
        )
    except ConfigConflictError as exc:
        return _render_channel_list(
            request,
            services,
            snapshot,
            warning=str(exc),
            status_code=409,
        )
    request.app.state.refresh_services()
    return RedirectResponse(
        f"/admin/memory-channels/{channel_key}",
        status_code=303,
    )


@router.get(
    "/admin/memory-channels/{channel_key}",
    response_class=HTMLResponse,
)
async def admin_memory_channel_detail(
    request: Request,
    channel_key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    return _render_channel_detail(request, services, snapshot, channel_key)


@router.post(
    "/admin/memory-channels/{channel_key}",
    response_class=HTMLResponse,
)
async def admin_memory_channel_save(
    request: Request,
    channel_key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    display_name = str(form.get("display_name", "")).strip()
    new_key = str(form.get("new_key", channel_key)).strip() or channel_key
    references = _channel_references(snapshot, channel_key)
    try:
        _validate_channel_key(new_key)
        if not revision:
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        raw = deepcopy(snapshot.raw)
        channels = dict(raw.get("memory", {}).get("channels", {}))
        if channel_key not in channels:
            raise HTTPException(
                status_code=404,
                detail="Unknown memory channel",
            )
        if new_key != channel_key and references:
            return _render_channel_detail(
                request,
                services,
                snapshot,
                channel_key,
                warning=(
                    "Cannot rekey a referenced channel. Remove or "
                    "migrate declared references first."
                ),
                status_code=409,
            )
        if new_key == channel_key:
            channels[channel_key] = {
                "display_name": display_name or channel_key,
            }
        else:
            if new_key in channels:
                raise ValidationFailed(
                    (
                        type(
                            "Issue",
                            (),
                            {
                                "code": "duplicate-channel-key",
                                "field": "new_key",
                                "message": (
                                    "Memory channel key already exists."
                                ),
                                "corrective_hint": (
                                    "Choose a unique global channel key."
                                ),
                            },
                        )(),
                    )
                )
            ordered = []
            for key, value in channels.items():
                if key == channel_key:
                    ordered.append(
                        (
                            new_key,
                            {
                                "display_name": display_name or new_key,
                            },
                        )
                    )
                else:
                    ordered.append((key, value))
            channels = dict(ordered)
        _patch_channels(raw, channels)
        parse_config_canonical(raw, snapshot.path)
        services.config_store.replace(revision, raw)
    except ValidationFailed as exc:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            issues=_issue_dicts(exc),
            status_code=409,
        )
    except ConfigConflictError as exc:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            warning=str(exc),
            status_code=409,
        )
    request.app.state.refresh_services()
    destination = new_key if new_key != channel_key else channel_key
    return RedirectResponse(
        f"/admin/memory-channels/{destination}",
        status_code=303,
    )


@router.post(
    "/admin/memory-channels/{channel_key}/delete",
    response_class=HTMLResponse,
)
async def admin_memory_channel_delete(
    request: Request,
    channel_key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    try:
        with revision_bound_group_operation(
            services.config_store,
            all_groups=True,
            expected_revision=revision,
        ) as refreshed:
            references = _channel_references(refreshed, channel_key)
            if references:
                return _render_channel_detail(
                    request,
                    services,
                    refreshed,
                    channel_key,
                    warning=(
                        "Cannot delete a referenced channel. Remove or "
                        "migrate declared references first."
                    ),
                    status_code=409,
                )
            active_job_refs = _active_channel_jobs(refreshed, channel_key)
            if active_job_refs:
                return _render_channel_detail(
                    request,
                    services,
                    refreshed,
                    channel_key,
                    warning=(
                        "Cannot delete a channel targeted by an active job: "
                        + "; ".join(active_job_refs)
                    ),
                    status_code=409,
                )
            if not revision or refreshed.revision != revision:
                raise ConfigConflictError(
                    "config.yaml changed; reload before saving"
                )
            resolved = _resolve_channel_memory(
                refreshed,
                services,
                channel_key,
            )
            archive_path = None
            try:
                def archive_and_patch():
                    nonlocal archive_path
                    raw = deepcopy(refreshed.raw)
                    channels = dict(raw.get("memory", {}).get("channels", {}))
                    if channel_key not in channels:
                        raise HTTPException(
                            status_code=404,
                            detail="Unknown memory channel",
                        )
                    archive_path = _archive_channel_directory(
                        services,
                        resolved,
                    )
                    del channels[channel_key]
                    _patch_channels(raw, channels)
                    parse_config_canonical(raw, refreshed.path)
                    services.config_store.replace(refreshed.revision, raw)
                services.memory_store.try_locked(resolved, archive_and_patch)
            except (ConfigConflictError, ValidationFailed) as exc:
                try:
                    _restore_channel_archive(resolved, archive_path)
                except OSError as restore_error:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "Failed to restore archived channel after config "
                            "error; archive preserved at "
                            f"{archive_path}: {restore_error}"
                        ),
                    ) from restore_error
                if isinstance(exc, ValidationFailed):
                    return _render_channel_detail(
                        request,
                        services,
                        refreshed,
                        channel_key,
                        issues=_issue_dicts(exc),
                        status_code=409,
                    )
                return _render_channel_detail(
                    request,
                    services,
                    refreshed,
                    channel_key,
                    warning=str(exc),
                    status_code=409,
                )
    except ConfigConflictError as exc:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            warning=str(exc),
            status_code=409,
        )
    except ResourceBusyError:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            warning=(
                "Memory is busy; try again after the active writer "
                "finishes."
            ),
            status_code=423,
        )
    request.app.state.refresh_services()
    return RedirectResponse("/admin/memory-channels", status_code=303)


@router.post(
    "/admin/memory-channels/{channel_key}/content",
    response_class=HTMLResponse,
)
async def admin_memory_channel_content(
    request: Request,
    channel_key: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    form = await request.form()
    filename = str(form.get("filename", "memory.md")).strip() or "memory.md"
    content_revision = str(form.get("content_revision", "")).strip()
    content = str(form.get("content", ""))
    forged_fields = [
        field
        for field in ("channel_key", "selector", "hash")
        if str(form.get(field, "")).strip()
    ]
    if forged_fields:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            content_warning=(
                "Memory content updates must target the URL channel only."
            ),
            filename=filename,
            content_override=content,
            status_code=409,
        )
    try:
        resolved = _resolve_channel_memory(snapshot, services, channel_key)
        services.memory_store.try_update(
            resolved,
            content_revision,
            lambda current: {
                **current.files,
                filename: content.encode("utf-8"),
            },
        )
    except ResourceBusyError:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            content_warning=(
                "Memory is busy; try again after the active "
                "writer finishes."
            ),
            filename=filename,
            content_override=content,
            status_code=423,
        )
    except MemoryConflictError as exc:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            content_warning=str(exc),
            memory_conflict={
                "current_revision": exc.current.revision,
                "attempted_revision": exc.expected_revision,
                "current_content": exc.current.files.get(
                    filename, b""
                ).decode("utf-8"),
                "attempted_content": content,
            },
            filename=filename,
            content_override=content,
            status_code=409,
        )
    except (ValueError, TypeError) as exc:
        return _render_channel_detail(
            request,
            services,
            snapshot,
            channel_key,
            content_warning=str(exc),
            filename=filename,
            content_override=content,
            status_code=409,
        )
    return RedirectResponse(
        f"/admin/memory-channels/{channel_key}",
        status_code=303,
    )
