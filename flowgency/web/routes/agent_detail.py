from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from flowgency.clock import now as clock_now
from flowgency.configuration import (
    AgentProfilePatch,
    AgentRuntimePatch,
    ConfigConflictError,
    ConfigSnapshot,
    ResolvedTeamPaths,
    ValidationFailed,
    parse_config,
    patch_agent_runtime,
    patch_agent_profile,
    resolve_team_paths,
)
from flowgency.configuration.patches import _UNSET
from flowgency.configuration.models import MemorySelector
from flowgency.permissions.eligibility import may_write_workspace
from flowgency.fs import ResourceBusyError
from flowgency.health import (
    elapsed_coarse,
    grace_window,
    last_fired_at,
    next_occurrence,
    relative_future,
    routine_schedules,
    schedule_lateness,
)
from flowgency.integrations import get_integration
from flowgency.integrations.models import RuntimeCapabilities
from flowgency.jobs.authority import JobStore
from flowgency.memory import MemoryConflictError, resolve_memory_selector
from flowgency.prompts import PromptConflictError, PromptNotFoundError
from flowgency.prompts.catalog import effective_prompt_catalog
from flowgency.tickets.models import UserTicketContext
from flowgency.tickets.views import build_board_view
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.job_presentation import load_team_jobs
from flowgency.web.logs import collect_agent_logs, log_href, with_log_links
from flowgency.web.team_navigation import build_team_context


router = APIRouter()

_TAB_LABELS = {
    "profile": "Profile",
    "blueprint": "Blueprint",
    "runtime": "Runtime",
    "permissions": "Permissions",
    "prompts": "Prompts",
    "routines": "Routines",
    "memory": "Memory",
    "activity": "Activity",
    "logs": "Logs",
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


def _team_context(request: Request, snapshot, team_id: str) -> dict[str, Any]:
    return build_team_context(
        snapshot,
        team_id,
        theme_css=_theme_css(request),
        show_tips=False,
        tips_dismissed=[],
        ticket_service=request.app.state.services.tickets,
    )


def _get_snapshot_instance(snapshot, team_id: str, agent_id: str):
    try:
        team_cfg = snapshot.config.teams[team_id]
        instance = team_cfg.agents[agent_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    return team_cfg, instance


def _tab_links(team_id: str, agent_id: str, active_tab: str) -> list[dict[str, str | bool]]:
    links: list[dict[str, str | bool]] = []
    for key, label in _TAB_LABELS.items():
        links.append(
            {
                "key": key,
                "label": label,
                "href": f"/{team_id}/agents/{agent_id}/{key}",
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


def _single_issue(field: str, message: str, hint: str = "") -> list[dict[str, str]]:
    return [{"field": field, "message": message, "hint": hint}]


def _memory_scope_label(selector: MemorySelector | None, channels) -> str:
    selected = selector or MemorySelector(scope="agent")
    if selected.scope == "run":
        return "Run memory"
    if selected.scope == "agent":
        return "Agent memory"
    if selected.scope == "team":
        return "Team memory"
    if selected.scope == "channel":
        channel = channels.get(selected.channel or "")
        display = channel.display_name if channel is not None else (selected.channel or "Channel")
        return f"Channel: {display}"
    return selected.scope.title()


def _preview_job_id(team_id: str, agent_id: str) -> str:
    return f"detail-{team_id}-{agent_id}"


def _resolve_tab_memory(snapshot, services: FlowgencyServices, team_id: str, agent_id: str, selector: MemorySelector | None):
    if services.memory_store is None:
        raise HTTPException(status_code=409, detail="Memory store unavailable")
    resolved = resolve_memory_selector(
        selector or MemorySelector(scope="agent"),
        job_id=_preview_job_id(team_id, agent_id),
        team_key=team_id,
        agent_name=agent_id,
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )
    return services.memory_store.ensure(resolved)


def _path_lines(paths: tuple[Path, ...]) -> list[str]:
    return [str(path.resolve(strict=False)).replace("\\", "/") for path in paths]


def _recent_log_rows(
    team_id: str,
    paths: ResolvedTeamPaths,
    agent_id: str,
) -> list[dict[str, str]]:
    logs_root = paths.logs
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
                    "href": log_href(
                        team_id,
                        str(candidate.resolve()),
                        agent_id=agent_id,
                        source="activity",
                    ),
                    "when": candidate.stat().st_mtime_ns,
                }
            )
            if len(rows) >= 8:
                return rows
    return rows


def _activity_items(
    team_id: str,
    paths: ResolvedTeamPaths,
    agent_id: str,
    job_store: JobStore | None,
    services: FlowgencyServices,
) -> dict[str, Any]:
    ticket_events: list[dict[str, Any]] = []
    if services.tickets is not None:
        actor = UserTicketContext(team_id=team_id)
        snapshot = services.config_store.load()
        workflow_names = snapshot.config.teams[team_id].workflows
        for binding in services.tickets.list_workflows(actor):
            board = build_board_view(
                services.tickets,
                actor,
                binding.workflow_id,
                ticket_jobs=services.ticket_jobs,
            )
            workflow_name = workflow_names[binding.workflow_id].name
            for column in board.columns:
                for ticket in column.tickets:
                    for event in ticket.history:
                        if event.actor != agent_id:
                            continue
                        ticket_events.append(
                            {
                                "kind": "Ticket",
                                "title": ticket.title,
                                "href": f"/{team_id}/workflows/{binding.workflow_id}?ticket={ticket.ref.ticket_id}",
                                "meta": f"{workflow_name} · {event.summary}",
                                "at": event.at,
                            }
                        )
    ticket_events.sort(key=lambda item: item["at"] or clock_now(), reverse=True)
    jobs = [
        {
            "id": record.spec.job_id,
            "status": record.status,
            "trigger": record.spec.trigger,
        }
        for record in (job_store.active(team_id, agent_id) if job_store is not None else ())
    ]
    return {
        "ticket_activity": ticket_events[:8],
        "jobs": jobs[:8],
        "logs": _recent_log_rows(team_id, paths, agent_id),
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


def _patch_default_memory(raw: dict[str, Any], team_id: str, agent_id: str, selector: MemorySelector | None) -> None:
    target = None
    for entry in raw["teams"][team_id].setdefault("agents", []):
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


def _available_prompts(
    services: FlowgencyServices, snapshot, team_id: str, agent_id: str
) -> tuple[tuple[tuple[str, str], ...], list[dict[str, str]]]:
    if services.blueprint_library is None or services.prompt_store is None:
        return (), []
    try:
        return (
            tuple(
                (item.scope, item.document.name)
                for item in effective_prompt_catalog(
                    snapshot,
                    services.blueprint_library,
                    services.prompt_store,
                    team_id,
                    agent_id,
                )
            ),
            [],
        )
    except ValidationFailed as exc:
        return (), _issue_dicts(exc)


def _prompts_context(
    services: FlowgencyServices,
    snapshot,
    team_id: str,
    agent_id: str,
    *,
    create_name: str = "",
    create_source: str = "",
    source_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    _team_cfg, instance = _get_snapshot_instance(snapshot, team_id, agent_id)
    if services.prompt_service is None:
        raise HTTPException(status_code=409, detail="Prompt service unavailable")
    overrides = source_overrides or {}
    try:
        catalog = services.prompt_service.catalog(snapshot, team_id, agent_id)
    except ValidationFailed as exc:
        return {
            "shared_prompts": (),
            "private_prompts": (),
            "prompt_issues": _issue_dicts(exc),
            "create_prompt_name": create_name,
            "create_prompt_source": create_source,
        }
    shared: list[dict[str, str]] = []
    private: list[dict[str, str]] = []
    for item in catalog:
        row = {
            "scope": item.scope,
            "name": item.document.name,
            "description": item.document.description,
            "argument_hint": item.document.argument_hint or "",
            "digest": item.document.digest,
            "source_path": item.source_path,
        }
        if item.scope == "blueprint":
            row["library_href"] = (
                f"/admin/agent-library/blueprints/{instance.blueprint}/prompts"
                f"?path={quote(item.source_path, safe='')}"
            )
            shared.append(row)
            continue
        source_text = item.document.source.decode("utf-8")
        row["source"] = overrides.get(item.document.name, source_text)
        private.append(row)
    return {
        "shared_prompts": tuple(shared),
        "private_prompts": tuple(private),
        "create_prompt_name": create_name,
        "create_prompt_source": create_source,
        "prompt_issues": [],
    }


def _resolve_integration(services: FlowgencyServices, name: str):
    integration = services.integrations.get(name)
    if integration is not None:
        return integration
    try:
        return get_integration(name)
    except KeyError:
        return SimpleNamespace(
            display_name=name,
            runtime_capabilities=RuntimeCapabilities(),
            projector=None,
        )


def _runtime_context(services: FlowgencyServices, snapshot, team_id: str, agent_id: str) -> dict[str, Any]:
    team_cfg, instance = _get_snapshot_instance(snapshot, team_id, agent_id)
    integration = _resolve_integration(services, instance.integration)
    allow_local_network = instance.integration_config.get("allow_local_network") is True
    return {
        "integration_name": instance.integration,
        "integration_display_name": integration.display_name,
        "team_timeout": team_cfg.runtime.timeout,
        "agent_timeout": instance.runtime.timeout if "timeout" in instance.runtime.model_fields_set else "",
        "show_allow_local_network": instance.integration == "copilot",
        "allow_local_network": allow_local_network,
        "capabilities": integration.runtime_capabilities,
        "projector_capabilities": getattr(integration.projector, "capabilities", None),
    }


def _runtime_form_integration_config(form, integration_name: str) -> dict[str, object] | object:
    if integration_name != "copilot":
        return _UNSET
    if "integration_config.allow_local_network__present" not in form:
        return _UNSET
    return {
        "allow_local_network": "integration_config.allow_local_network" in form,
    }


def _blueprint_context(services: FlowgencyServices, snapshot, team_id: str, agent_id: str) -> dict[str, Any]:
    _, instance = _get_snapshot_instance(snapshot, team_id, agent_id)
    if services.blueprint_library is None:
        raise HTTPException(status_code=409, detail="Blueprint library unavailable")
    try:
        inspection = services.blueprint_library.inspect(instance.blueprint)
    except ValidationFailed as exc:
        return {
            "issues": _issue_dicts(exc),
            "inspection": None,
            "compatibility": {},
            "cache_status": {"state": "unavailable", "path": "", "pins": ()},
            "edit_library_href": f"/admin/agent-library/blueprints/{instance.blueprint}",
            "edit_skills_href": f"/admin/agent-library/blueprints/{instance.blueprint}/skills",
        }
    integration = _resolve_integration(services, instance.integration)
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


def _memory_context(snapshot, services: FlowgencyServices, team_id: str, agent_id: str) -> dict[str, Any]:
    _, instance = _get_snapshot_instance(snapshot, team_id, agent_id)
    memory_snapshot = _resolve_tab_memory(snapshot, services, team_id, agent_id, instance.default_memory)
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
    services: FlowgencyServices,
    team_id: str,
    agent_id: str,
    tab: str,
    *,
    snapshot: ConfigSnapshot | None = None,
    status_code: int = 200,
    issues: list[dict[str, str]] | None = None,
    banner: str = "",
    memory_conflict: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
):
    snapshot = snapshot or services.config_store.load()
    team_cfg, instance = _get_snapshot_instance(snapshot, team_id, agent_id)
    handler_issues = issues or []
    context: dict[str, Any] = {
        "request": request,
        **_team_context(request, snapshot, team_id),
        "active": "agents",
        "agent": agent_id,
        "tab": tab,
        "tab_label": _TAB_LABELS[tab],
        "tab_links": _tab_links(team_id, agent_id, tab),
        "config_revision": snapshot.revision,
        "agent_name": instance.name,
        "display_name": instance.identity.display_name or instance.name,
        "title": instance.identity.title,
        "emoji": instance.identity.emoji,
        "integration": instance.integration,
        "blueprint": instance.blueprint,
        "can_write": may_write_workspace(snapshot.config, team_id, agent_id),
        "issues": handler_issues,
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
                }
            }
        )
    elif tab == "blueprint":
        context.update(_blueprint_context(services, snapshot, team_id, agent_id))
    elif tab == "runtime":
        context.update(_runtime_context(services, snapshot, team_id, agent_id))
    elif tab == "prompts":
        context.update(_prompts_context(services, snapshot, team_id, agent_id))
    elif tab == "memory":
        context.update(_memory_context(snapshot, services, team_id, agent_id))
    elif tab == "activity":
        context.update(
            _activity_items(
                team_id,
                resolve_team_paths(team_cfg),
                agent_id,
                services.job_store,
                services,
            )
        )
    elif tab == "logs":
        records, _warnings = load_team_jobs(services.job_store, team_id)
        groups = collect_agent_logs(
            resolve_team_paths(team_cfg).logs,
            team_id,
            agent_id,
            records,
            tuple(team_cfg.agents.keys()),
        )
        log_count = sum(len(entries) for entries in groups.values())
        context.update(
            {
                "logs": with_log_links(groups, team_id, agent_id=agent_id, source="logs"),
                "log_count": log_count,
            }
        )
    # Merge handler-supplied issues with any tab-supplied issues, dedup on (code, field, message).
    tab_issues = context["issues"]
    seen_keys: set[tuple[str, str, str]] = set()
    merged: list[dict[str, str]] = []
    for item in handler_issues + tab_issues:
        k = (item.get("code", ""), item.get("field", ""), item.get("message", ""))
        if k not in seen_keys:
            seen_keys.add(k)
            merged.append(item)
    context["issues"] = merged
    if overrides:
        context.update(overrides)
    return _templates(request).TemplateResponse(request, "agent_detail.html", context, status_code=status_code)


@router.get("/{team}/agents/{agent}", response_class=HTMLResponse)
async def agent_detail_base(team: str, agent: str):
    return RedirectResponse(f"/{team}/agents/{agent}/profile", status_code=303)


@router.get("/{team}/agents/{agent}/profile", response_class=HTMLResponse)
async def agent_detail_profile(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "profile")


@router.post("/{team}/agents/{agent}/profile", response_class=HTMLResponse)
async def agent_detail_profile_save(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    try:
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        patch_agent_profile(
            services.config_store,
            revision,
            team,
            agent,
            AgentProfilePatch(
                display_name=str(form.get("display_name", "")).strip(),
                title=str(form.get("title", "")).strip(),
                emoji=str(form.get("emoji", "")).strip(),
            ),
        )
    except ValidationFailed as exc:
        return _detail_context(request, services, team, agent, "profile", status_code=409, issues=_issue_dicts(exc))
    except ConfigConflictError as exc:
        return _detail_context(request, services, team, agent, "profile", status_code=409, banner=str(exc))
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/profile", status_code=303)


@router.get("/{team}/agents/{agent}/blueprint", response_class=HTMLResponse)
async def agent_detail_blueprint(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "blueprint")


@router.get("/{team}/agents/{agent}/runtime", response_class=HTMLResponse)
async def agent_detail_runtime(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "runtime")


@router.post("/{team}/agents/{agent}/runtime", response_class=HTMLResponse)
async def agent_detail_runtime_save(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    form = await request.form()
    snapshot = services.config_store.load()
    _, instance = _get_snapshot_instance(snapshot, team, agent)
    revision = str(form.get("revision", "")).strip()
    timeout_text = str(form.get("timeout", "")).strip()
    show_allow_local_network = instance.integration == "copilot"
    allow_local_network = (
        "integration_config.allow_local_network" in form
        if show_allow_local_network and "integration_config.allow_local_network__present" in form
        else instance.integration_config.get("allow_local_network") is True
    )
    runtime_overrides = {
        "agent_timeout": timeout_text,
        "show_allow_local_network": show_allow_local_network,
        "allow_local_network": allow_local_network,
    }
    if "permission_rules_yaml" in form:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "runtime",
            status_code=409,
            overrides={
                **runtime_overrides,
                "permission_editor_href": f"/{team}/agents/{agent}/permissions",
                "permission_move_message": "Permission rules moved to the dedicated Permissions tab.",
            },
        )
    try:
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        patch_agent_runtime(
            services.config_store,
            revision,
            team,
            agent,
            AgentRuntimePatch(
                timeout=int(timeout_text) if timeout_text else None,
                integration_config=_runtime_form_integration_config(form, instance.integration),
            ),
        )
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
        return _detail_context(
            request,
            services,
            team,
            agent,
            "runtime",
            status_code=409,
            issues=issues,
            overrides=runtime_overrides,
        )
    except ValidationFailed as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "runtime",
            status_code=409,
            issues=_issue_dicts(exc),
            overrides=runtime_overrides,
        )
    except ConfigConflictError as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "runtime",
            status_code=409,
            banner=str(exc),
            overrides=runtime_overrides,
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/runtime", status_code=303)


@router.get("/{team}/agents/{agent}/prompts", response_class=HTMLResponse)
async def agent_detail_prompts(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "prompts")


@router.get("/{team}/agents/{agent}/logs", response_class=HTMLResponse)
async def agent_detail_logs(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "logs")


@router.post("/{team}/agents/{agent}/prompts/create", response_class=HTMLResponse)
async def agent_detail_prompts_create(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    name = str(form.get("name", "")).strip()
    source = str(form.get("source", ""))
    try:
        if services.prompt_service is None:
            raise HTTPException(status_code=409, detail="Prompt service unavailable")
        _get_snapshot_instance(services.config_store.load(), team, agent)
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        services.prompt_service.create_private(
            team,
            agent,
            name,
            source.encode("utf-8"),
            expected_revision=revision,
        )
    except ValidationFailed as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_issue_dicts(exc),
            overrides=_prompts_context(
                services,
                services.config_store.load(),
                team,
                agent,
                create_name=name,
                create_source=source,
            ),
        )
    except ConfigConflictError as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_single_issue("revision", str(exc), "Reload and resubmit the prompt."),
            overrides=_prompts_context(
                services,
                services.config_store.load(),
                team,
                agent,
                create_name=name,
                create_source=source,
            ),
        )
    except PromptConflictError as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_single_issue("name", str(exc), "Choose a different prompt name or reload and retry."),
            overrides=_prompts_context(
                services,
                services.config_store.load(),
                team,
                agent,
                create_name=name,
                create_source=source,
            ),
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/prompts", status_code=303)


@router.post("/{team}/agents/{agent}/prompts/{name}/save", response_class=HTMLResponse)
async def agent_detail_prompts_save(
    request: Request,
    team: str,
    agent: str,
    name: str,
    services: FlowgencyServices = Depends(get_services),
):
    form = await request.form()
    digest = str(form.get("digest", "")).strip()
    source = str(form.get("source", ""))
    try:
        if services.prompt_service is None:
            raise HTTPException(status_code=409, detail="Prompt service unavailable")
        _get_snapshot_instance(services.config_store.load(), team, agent)
        services.prompt_service.update_private(
            team,
            agent,
            name,
            source.encode("utf-8"),
            expected_digest=digest,
        )
    except PromptNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Unknown prompt") from exc
    except ValidationFailed as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_issue_dicts(exc),
            overrides=_prompts_context(
                services,
                services.config_store.load(),
                team,
                agent,
                source_overrides={name: source},
            ),
        )
    except PromptConflictError as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_single_issue("digest", str(exc), "Reload the latest prompt source before saving."),
            overrides=_prompts_context(
                services,
                services.config_store.load(),
                team,
                agent,
                source_overrides={name: source},
            ),
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/prompts", status_code=303)


@router.post("/{team}/agents/{agent}/prompts/{name}/delete", response_class=HTMLResponse)
async def agent_detail_prompts_delete(
    request: Request,
    team: str,
    agent: str,
    name: str,
    services: FlowgencyServices = Depends(get_services),
):
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    digest = str(form.get("digest", "")).strip()
    try:
        if services.prompt_service is None:
            raise HTTPException(status_code=409, detail="Prompt service unavailable")
        _get_snapshot_instance(services.config_store.load(), team, agent)
        if not revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")
        services.prompt_service.delete_private(
            team,
            agent,
            name,
            expected_revision=revision,
            expected_digest=digest,
        )
    except PromptNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Unknown prompt") from exc
    except ValidationFailed as exc:
        return _detail_context(request, services, team, agent, "prompts", status_code=409, issues=_issue_dicts(exc))
    except ConfigConflictError as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_single_issue("revision", str(exc), "Reload and retry deletion."),
        )
    except PromptConflictError as exc:
        return _detail_context(
            request,
            services,
            team,
            agent,
            "prompts",
            status_code=409,
            issues=_single_issue("digest", str(exc), "Reload and confirm the prompt digest before deleting."),
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/prompts", status_code=303)


@router.get("/{team}/agents/{agent}/memory", response_class=HTMLResponse)
async def agent_detail_memory(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "memory")


@router.post("/{team}/agents/{agent}/memory", response_class=HTMLResponse)
async def agent_detail_memory_save(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
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
            _patch_default_memory(raw, team, agent, selector)
            parse_config(raw, snapshot.path)
            services.config_store.replace(revision, raw)
        elif action == "content":
            _, instance = _get_snapshot_instance(snapshot, team, agent)
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
                job_id=_preview_job_id(team, agent),
                team_key=team,
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
            team,
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
            team,
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
        return _detail_context(request, services, team, agent, "memory", status_code=409, issues=_issue_dicts(exc), overrides={"selector_token": str(form.get("selector_token", "")).strip(), "selected_memory_file": filename, "selected_memory_content": content})
    except ConfigConflictError as exc:
        return _detail_context(request, services, team, agent, "memory", status_code=409, banner=str(exc), overrides={"selector_token": str(form.get("selector_token", "")).strip(), "selected_memory_file": filename, "selected_memory_content": content})
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/memory", status_code=303)


@router.get("/{team}/agents/{agent}/activity", response_class=HTMLResponse)
async def agent_detail_activity(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
    return _detail_context(request, services, team, agent, "activity")