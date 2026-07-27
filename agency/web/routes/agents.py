from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agency.configuration import ConfigConflictError, ValidationFailed
from agency.configuration.models import MemorySelector
from agency.fs.snapshot import AssetValidationError
from agency.instances import AgentInstanceCreate, InstanceMoveConflict
from agency.prompts import PromptNotFoundError
from agency.web.dependencies import AgencyServices, get_services


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _group_context(request: Request, snapshot, group_id: str) -> dict:
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


def _friendly_status(status: str) -> str:
    return {
        "waiting_for_memory": "Waiting for memory",
        "queued": "Queued",
        "running": "Running",
        "complete": "Complete",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(status, status.replace("_", " ").title())


def _active_job_sort_key(record) -> tuple[str, str]:
    return (record.spec.created_at, record.spec.job_id)


def _job_badge_classes(status: str) -> str:
    return {
        "queued": (
            "bg-slate-100 text-slate-700 border border-slate-200 "
            "dark:bg-slate-800 dark:text-slate-100 dark:border-slate-700"
        ),
        "waiting_for_memory": (
            "bg-amber-100 text-amber-800 border border-amber-200 "
            "dark:bg-amber-900/50 dark:text-amber-100 dark:border-amber-700"
        ),
        "running": (
            "bg-sky-100 text-sky-700 border border-sky-200 "
            "dark:bg-sky-900/50 dark:text-sky-100 dark:border-sky-700"
        ),
    }.get(
        status,
        (
            "bg-slate-100 text-slate-700 border border-slate-200 "
            "dark:bg-slate-800 dark:text-slate-100 dark:border-slate-700"
        ),
    )


def _job_badge_title(status: str) -> str:
    return {
        "queued": "Queued job awaiting execution",
        "waiting_for_memory": "Job is waiting for memory publication",
        "running": "Job is currently executing",
    }.get(status, _friendly_status(status))


def _instance_memory_label(instance, channels) -> str:
    selector = instance.default_memory or MemorySelector(scope="agent")
    if selector.scope == "channel":
        channel = channels.get(selector.channel or "")
        display = channel.display_name if channel is not None else (selector.channel or "Channel")
        return f"Channel: {display}"
    return selector.scope.title() + " memory"


def _issue_dicts(exc: ValidationFailed | tuple) -> list[dict[str, str]]:
    issues = exc.issues if isinstance(exc, ValidationFailed) else exc
    rows: list[dict[str, str]] = []
    for issue in issues:
        rows.append(
            {
                "message": issue.message,
                "field": issue.field,
                "scope": issue.scope,
                "hint": issue.corrective_hint or "",
            }
        )
    return rows


def _validation_warning(exc: ValidationFailed) -> str:
    messages: list[str] = []
    for issue in exc.issues:
        detail = issue.message
        if issue.corrective_hint:
            detail = f"{detail} {issue.corrective_hint}"
        messages.append(detail)
    return "; ".join(messages)


def _creation_values(form: dict[str, Any] | None = None) -> dict[str, str]:
    payload = form or {}
    return {
        "name": str(payload.get("name", "") or "").strip(),
        "blueprint": str(payload.get("blueprint", "") or "").strip(),
        "integration": str(payload.get("integration", "") or "").strip(),
        "display_name": str(payload.get("display_name", "") or "").strip(),
    }


def _memory_scope_options(snapshot) -> tuple[dict[str, str], ...]:
    options = [
        {"value": "run", "label": "Run memory"},
        {"value": "agent", "label": "Agent memory"},
        {"value": "group", "label": "Group memory"},
        {"value": "channel", "label": "Channel memory"},
    ]
    return tuple(options)


def _memory_channel_options(snapshot) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "value": key,
            "label": channel.display_name,
        }
        for key, channel in snapshot.config.memory.channels.items()
    )


def _launcher_prompts(
    services: AgencyServices, snapshot, group_id: str, agent_id: str
) -> tuple[tuple[dict[str, str], ...], tuple[dict[str, str], ...]]:
    if services.prompt_service is None:
        raise HTTPException(status_code=409, detail="Prompt service unavailable")
    try:
        catalog = services.prompt_service.catalog(snapshot, group_id, agent_id)
    except ValidationFailed as exc:
        return (), tuple(
            {
                "code": issue.code,
                "field": issue.field,
                "message": issue.message,
                "hint": issue.corrective_hint,
            }
            for issue in exc.issues
        )
    except (OSError, PromptNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return (
        tuple(
            {
                "scope": item.scope,
                "name": item.document.name,
                "description": item.document.description,
                "argument_hint": item.document.argument_hint or "",
                "source_path": item.source_path,
            }
            for item in catalog
        ),
        (),
    )


def _base_instance_row(snapshot, group_id: str, instance) -> dict[str, Any]:
    return {
        "name": instance.name,
        "display_name": instance.identity.display_name or instance.name,
        "title": instance.identity.title,
        "emoji": instance.identity.emoji,
        "blueprint": instance.blueprint,
        "integration": instance.integration,
        "memory_label": _instance_memory_label(instance, snapshot.config.memory.channels),
        "profile_href": f"/{group_id}/agents/{instance.name}/profile",
        "activity_href": f"/{group_id}/agents/{instance.name}/activity",
        "remove_href": f"/{group_id}/agents/{instance.name}/remove",
        "move_href": f"/{group_id}/agents/{instance.name}/move",
        "run_href": f"/{group_id}/agents/{instance.name}/run",
        "active_jobs": tuple(),
        "active_job_count": 0,
        "shared_prompts": tuple(),
        "private_prompts": tuple(),
        "has_saved_prompts": False,
        "default_mode": "one-off",
        "default_prompt_scope": "",
        "default_prompt_name": "",
    }


def _fallback_instance_rows(snapshot, group_id: str) -> list[dict[str, Any]]:
    group = snapshot.config.groups[group_id]
    return [_base_instance_row(snapshot, group_id, instance) for instance in group.agents.values()]


def _instance_rows(snapshot, services: AgencyServices, group_id: str) -> list[dict]:
    group = snapshot.config.groups[group_id]
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    rows = []
    for instance in group.agents.values():
        prompt_rows, prompt_issues = _launcher_prompts(services, snapshot, group_id, instance.name)
        shared_prompts = tuple(item for item in prompt_rows if item["scope"] == "blueprint")
        private_prompts = tuple(item for item in prompt_rows if item["scope"] == "instance")
        selected_prompt = prompt_rows[0] if prompt_rows else None
        current_jobs = sorted(
            services.job_store.active(group_id, instance.name),
            key=_active_job_sort_key,
            reverse=True,
        )
        row = _base_instance_row(snapshot, group_id, instance)
        row.update(
            {
                "active_jobs": tuple(
                    {
                        "status": _friendly_status(record.status),
                        "status_key": record.status,
                        "href": f"/{group_id}/jobs/{record.spec.job_id}",
                        "classes": _job_badge_classes(record.status),
                        "title": _job_badge_title(record.status),
                        "job_id": record.spec.job_id,
                    }
                    for record in current_jobs
                ),
                "active_job_count": len(current_jobs),
                "shared_prompts": shared_prompts,
                "private_prompts": private_prompts,
                "has_saved_prompts": bool(prompt_rows),
                "default_mode": "saved" if prompt_rows else "one-off",
                "default_prompt_scope": selected_prompt["scope"] if selected_prompt is not None else "",
                "default_prompt_name": selected_prompt["name"] if selected_prompt is not None else "",
                "prompt_issues": prompt_issues,
            }
        )
        rows.append(row)
    return rows


def _available_blueprint_keys(services: AgencyServices) -> list[str]:
    root = Path(services.blueprint_library.root)
    if not root.exists():
        raise FileNotFoundError(f"Agent Library root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Agent Library root is not a directory: {root}")
    return sorted(item.key for item in services.blueprint_library.list())


def _render_roster(
    request: Request,
    services: AgencyServices,
    group_id: str,
    *,
    warning: str = "",
    status_code: int = 200,
    creation_open: bool = False,
    creation_values: dict[str, str] | None = None,
    creation_issues: list[dict[str, str]] | None = None,
):
    snapshot = services.config_store.load()
    if group_id not in snapshot.config.groups:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_id}")
    available_blueprints: list[str] = []
    if not warning:
        try:
            available_blueprints = _available_blueprint_keys(services)
        except AssetValidationError:
            pass  # prompt catalog issue; surfaced per-agent in the roster
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            warning = str(exc)
            status_code = 409
    try:
        instances = _instance_rows(snapshot, services, group_id)
    except HTTPException as exc:
        if not warning:
            warning = str(exc.detail)
        status_code = exc.status_code
        instances = _fallback_instance_rows(snapshot, group_id)
    return _templates(request).TemplateResponse(
        request,
        "agents.html",
        {
            "request": request,
            **_group_context(request, snapshot, group_id),
            "active": "agents",
            "instances": instances,
            "config_revision": snapshot.revision,
            "available_blueprints": available_blueprints,
            "available_integrations": sorted(services.integrations.keys()),
            "warning": warning,
            "creation_open": creation_open,
            "creation_values": creation_values or _creation_values(),
            "creation_issues": creation_issues or [],
            "memory_scope_options": _memory_scope_options(snapshot),
            "memory_channel_options": _memory_channel_options(snapshot),
        },
        status_code=status_code,
    )


@router.get("/{group}/agents", response_class=HTMLResponse)
async def agents_roster(request: Request, group: str, services: AgencyServices = Depends(get_services)):
    if services.instances is None:
        if isinstance(services.startup_error, ValidationFailed):
            return _render_roster(
                request,
                services,
                group,
                warning=_validation_warning(services.startup_error),
                status_code=409,
            )
        raise HTTPException(status_code=409, detail="Instance services unavailable")
    return _render_roster(request, services, group)


@router.post("/{group}/agents/create", response_class=HTMLResponse)
async def agent_create(request: Request, group: str, services: AgencyServices = Depends(get_services)):
    if services.instances is None:
        raise HTTPException(status_code=409, detail="Instance services unavailable")
    form = await request.form()
    expected_revision = str(form.get("revision", "")).strip()
    values = _creation_values(form)
    try:
        if not expected_revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        services.instances.create(
            group,
            AgentInstanceCreate(
                name=str(form.get("name", "")).strip(),
                blueprint=str(form.get("blueprint", "")).strip(),
                integration=str(form.get("integration", "")).strip(),
                display_name=str(form.get("display_name", "")).strip(),
            ),
            expected_revision,
        )
    except ValidationFailed as exc:
        return _render_roster(
            request,
            services,
            group,
            status_code=409,
            creation_open=True,
            creation_values=values,
            creation_issues=_issue_dicts(exc),
        )
    except ConfigConflictError as exc:
        return _render_roster(
            request,
            services,
            group,
            status_code=409,
            creation_open=True,
            creation_values=values,
            creation_issues=[{"message": str(exc), "field": "revision", "scope": "config", "hint": ""}],
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{group}/agents", status_code=303)


@router.post("/{group}/agents/{agent}/remove", response_class=HTMLResponse)
async def agent_remove(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    if services.instances is None:
        raise HTTPException(status_code=409, detail="Instance services unavailable")
    form = await request.form()
    expected_revision = str(form.get("revision", "")).strip()
    try:
        if not expected_revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        result = services.instances.remove(group, agent, expected_revision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigConflictError as exc:
        return _render_roster(request, services, group, warning=str(exc), status_code=409)
    request.app.state.refresh_services()
    if result.orphaned_prompt_namespace is not None:
        return _render_roster(
            request,
            request.app.state.services,
            group,
            warning=(
                f"Orphaned prompt namespace remains at {result.orphaned_prompt_namespace}"
            ),
            status_code=409,
        )
    return RedirectResponse(f"/{group}/agents", status_code=303)


@router.post("/{group}/agents/{agent}/move", response_class=HTMLResponse)
async def agent_move_preview(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    if services.instances is None:
        raise HTTPException(status_code=409, detail="Instance services unavailable")
    form = await request.form()
    expected_revision = str(form.get("revision", "")).strip()
    target_group = str(form.get("target_group", "")).strip()
    memory_mode = str(form.get("memory_mode", "empty")).strip() or "empty"
    snapshot = services.config_store.load()
    if not expected_revision or expected_revision != snapshot.revision:
        return _render_roster(
            request,
            services,
            group,
            warning="config.yaml changed; reload before previewing move",
            status_code=409,
        )
    preview = services.instances.preview_move(
        group,
        agent,
        target_group,
        memory_mode,
        expected_revision,
    )
    return _templates(request).TemplateResponse(
        request,
        "agent_move.html",
        {
            "request": request,
            **_group_context(request, snapshot, group),
            "active": "agents",
            "preview": asdict(preview),
        },
    )


@router.post("/{group}/agents/{agent}/move/apply", response_class=HTMLResponse)
async def agent_move_apply(request: Request, group: str, agent: str, services: AgencyServices = Depends(get_services)):
    if services.instances is None:
        raise HTTPException(status_code=409, detail="Instance services unavailable")
    form = await request.form()
    preview_revision = str(form.get("preview_revision", "")).strip()
    target_group = str(form.get("target_group", "")).strip()
    memory_mode = str(form.get("memory_mode", "empty")).strip() or "empty"
    if not preview_revision:
        return _render_roster(
            request,
            services,
            group,
            warning="move preview is stale; regenerate it before applying",
            status_code=409,
        )
    try:
        preview = services.instances.preview_move(
            group,
            agent,
            target_group,
            memory_mode,
            preview_revision,
        )
    except ConfigConflictError as exc:
        return _render_roster(request, services, group, warning=str(exc), status_code=409)
    try:
        result = services.instances.move(preview)
    except (ConfigConflictError, InstanceMoveConflict) as exc:
        return _render_roster(request, services, group, warning=str(exc), status_code=409)
    request.app.state.refresh_services()
    if result.orphaned_prompt_namespace is not None:
        return _render_roster(
            request,
            request.app.state.services,
            preview.target_group,
            warning=(
                f"Orphaned prompt namespace remains at {result.orphaned_prompt_namespace}"
            ),
            status_code=409,
        )
    return RedirectResponse(f"/{preview.target_group}/agents", status_code=303)


@router.get("/admin/orgs/{group}/agents/{agent}", response_class=HTMLResponse)
async def old_admin_agent_get(group: str, agent: str):
    return RedirectResponse(f"/{group}/agents/{agent}/profile", status_code=303)
