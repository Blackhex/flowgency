from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from flowgency.configuration.models import MemorySelector
from flowgency.integrations import (
    IntegrationError,
    format_command_with_environment,
    get_integration,
    spawn_interactive_terminal,
)
from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import JobRecord
from flowgency.jobs.queue import queue_snapshot
from flowgency.jobs.store import InvalidJobTransition, cancel_job, read_job
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.git_evidence import job_git_evidence_links
from flowgency.web.job_presentation import friendly_status as _friendly_status
from flowgency.web.job_presentation import friendly_trigger as _friendly_trigger
from flowgency.web.job_presentation import routine_title as _routine_title
from flowgency.web.job_presentation import status_badge_classes as _status_badge_classes
from flowgency.web.logs import log_href as _shared_log_href
from flowgency.web.team_navigation import build_team_context
from flowgency.web.workflow_context import user_context


router = APIRouter()


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


def _safe_job_id(job_id: str) -> str:
    if not job_id or Path(job_id).name != job_id or any(ch in job_id for ch in ("/", "\\")):
        raise HTTPException(status_code=404, detail="Job not found")
    return job_id


def _job_path(job_store: JobStore, team_id: str, job_id: str) -> Path:
    return job_store.path(team_id, _safe_job_id(job_id))


def _log_href(team_id: str, log_path: str | None) -> str:
    if not log_path:
        return ""
    return _shared_log_href(team_id, log_path)


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _resume_argv(record: JobRecord) -> tuple[str, ...] | None:
    session_id = record.session_id
    if not session_id or not _SAFE_SESSION_ID.match(session_id):
        return None
    try:
        integration = get_integration(record.spec.integration_name)
    except KeyError:
        return None
    return integration.resume_command(session_id)


def _format_resume(
    argv: tuple[str, ...] | None,
    copilot_home: str | None,
) -> str:
    if argv is None:
        return ""
    return format_command_with_environment(
        argv,
        {"COPILOT_HOME": copilot_home} if copilot_home else {},
    )


def _integration_display_name(record: JobRecord) -> str:
    try:
        integration = get_integration(record.spec.integration_name)
    except KeyError:
        return record.spec.integration_name
    return integration.display_name or record.spec.integration_name


def _memory_label(selector_data: dict[str, object], snapshot) -> str:
    selector = MemorySelector.model_validate(selector_data)
    if selector.scope == "run":
        return "Run memory"
    if selector.scope == "routine":
        return "Routine memory"
    if selector.scope == "agent":
        return "Agent memory"
    if selector.scope == "team":
        return "Team memory"
    channel = snapshot.config.memory.channels.get(selector.channel or "")
    display = channel.display_name if channel is not None else (selector.channel or "Channel")
    return f"Channel: {display}"


def _artifact_root(job_store: JobStore, team_id: str, job_id: str) -> Path:
    return job_store.artifact_root(team_id, _safe_job_id(job_id))


def _validate_artifact_query(job_store: JobStore, team_id: str, job_id: str, artifact: str) -> Path:
    candidate = Path(artifact)
    if candidate.name != artifact or artifact in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    target = (_artifact_root(job_store, team_id, job_id) / artifact).resolve(strict=False)
    root = _artifact_root(job_store, team_id, job_id)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Artifact access denied") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return target


def _job_rows(snapshot, job_store: JobStore, team_id: str) -> list[dict[str, Any]]:
    team_cfg = snapshot.config.teams[team_id]
    view = queue_snapshot(snapshot.config, memory_store=job_store.memory_store)
    positions = {
        entry.record.spec.job_id: index + 1
        for index, entry in enumerate(view.waiting)
    }
    rows: list[dict[str, Any]] = []
    for path in sorted(job_store.paths(team_id), key=lambda item: item.stat().st_mtime, reverse=True):
        record = read_job(path)
        instance = team_cfg.agents.get(record.spec.agent_name)
        agent_name = record.spec.agent_name
        rows.append(
            {
                "job_id": record.spec.job_id,
                "status": record.status,
                "status_label": _friendly_status(record.status),
                "status_classes": _status_badge_classes(record.status),
                "trigger_label": _friendly_trigger(record.spec.trigger),
                "display_name": (instance.identity.display_name or instance.name) if instance is not None else agent_name,
                "agent_name": agent_name,
                "blueprint": record.spec.blueprint.key,
                "integration": record.spec.integration_name,
                "routine_title": _routine_title(record.spec.routine_id, record.spec.prompt_source),
                "memory_label": _memory_label(record.spec.memory.selector, snapshot),
                "detail_href": f"/{team_id}/jobs/{record.spec.job_id}",
                "activity_href": f"/{team_id}/agents/{agent_name}/activity" if instance is not None else "",
                "instance_missing": instance is None,
                "queue_position": positions.get(record.spec.job_id),
                "queue_length": len(view.waiting),
                "due_at": record.due_at,
                "can_cancel": record.status in {"queued", "waiting_for_memory"},
            }
        )
    return rows


def _job_detail_context(snapshot, team_id: str, record, ticket_service=None) -> dict[str, Any]:
    team_cfg = snapshot.config.teams[team_id]
    instance = team_cfg.agents.get(record.spec.agent_name)
    agent_name = record.spec.agent_name
    artifact_dir = JobStore(snapshot.config.flowgency.memory_store).artifact_root(team_id, record.spec.job_id)
    failed_artifacts = []
    if artifact_dir.exists():
        for artifact in sorted(artifact_dir.glob("*.md")):
            label = "Failed memory snapshot" if artifact.name == "memory.md" else artifact.stem.replace("-", " ").title()
            failed_artifacts.append(
                {
                    "name": artifact.name,
                    "label": label,
                    "href": f"/{team_id}/jobs/{record.spec.job_id}?artifact={artifact.name}",
                }
            )
    publication = record.memory_publication or {}
    resume_argv = _resume_argv(record)
    git_evidence_links: tuple[dict[str, str], ...] = ()
    git_evidence_issues: tuple[Any, ...] = ()
    if ticket_service is not None:
        git_evidence_links, git_evidence_issues = job_git_evidence_links(
            ticket_service, user_context(team_id), record
        )
    return {
        "job": record,
        "job_status_label": _friendly_status(record.status),
        "job_status_classes": _status_badge_classes(record.status),
        "trigger_label": _friendly_trigger(record.spec.trigger),
        "display_name": (instance.identity.display_name or instance.name) if instance is not None else agent_name,
        "title": instance.identity.title if instance is not None else None,
        "agent_name": agent_name,
        "blueprint": record.spec.blueprint.key,
        "integration": record.spec.integration_name,
        "routine_title": _routine_title(record.spec.routine_id, record.spec.prompt_source),
        "memory_label": _memory_label(record.spec.memory.selector, snapshot),
        "failed_artifacts": failed_artifacts,
        "publication_receipt": publication,
        "git_evidence_links": git_evidence_links,
        "git_evidence_issues": git_evidence_issues,
        "activity_href": f"/{team_id}/agents/{agent_name}/activity" if instance is not None else "",
        "routine_href": f"/{team_id}/agents/{agent_name}/routines" if instance is not None else "",
        "profile_href": f"/{team_id}/agents/{agent_name}/profile" if instance is not None else "",
        "instance_missing": instance is None,
        "can_cancel": record.status in {"queued", "waiting_for_memory"},
        "stdout_href": _log_href(team_id, record.stdout_path),
        "stdout_name": Path(record.stdout_path).name if record.stdout_path else "",
        "stderr_href": _log_href(team_id, record.stderr_path),
        "stderr_name": Path(record.stderr_path).name if record.stderr_path else "",
        "diagnostic_memory_hash": record.spec.memory.memory_hash,
        "resume_command": _format_resume(resume_argv, getattr(record, "copilot_home", None)),
        "resume_available": resume_argv is not None,
        "resume_label": f"Resume in {_integration_display_name(record)}",
    }


@router.get("/{team}/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request, team: str, services: FlowgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if team not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail="Unknown team")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    return _templates(request).TemplateResponse(
        request,
        "jobs.html",
        {
            "request": request,
            **_team_context(request, snapshot, team),
            "active": "jobs",
            "jobs": _job_rows(snapshot, services.job_store, team),
        },
    )


@router.get("/{team}/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, team: str, job_id: str, artifact: str = "", resume: str = "", services: FlowgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if team not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail="Unknown team")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    if artifact:
        target = _validate_artifact_query(services.job_store, team, job_id, artifact)
        return FileResponse(target)
    path = _job_path(services.job_store, team, job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    record = read_job(path)
    # The context build scans the team's tickets and reads retained artifacts.
    context = await run_in_threadpool(
        _job_detail_context, snapshot, team, record, services.tickets
    )
    return _templates(request).TemplateResponse(
        request,
        "job_detail.html",
        {
            "request": request,
            **_team_context(request, snapshot, team),
            "active": "jobs",
            **context,
            "resume_notice": {
                "launched": "Opening the session in a new terminal.",
                "failed": "Could not open a terminal. Copy the command below and run it yourself.",
            }.get(resume, ""),
        },
    )


@router.post("/{team}/jobs/{job_id}/resume")
async def job_resume(request: Request, team: str, job_id: str, services: FlowgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if team not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail="Unknown team")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    path = _job_path(services.job_store, team, job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    record = read_job(path)
    argv = _resume_argv(record)
    if argv is None:
        raise HTTPException(status_code=400, detail="Job cannot be resumed")
    outcome = "launched"
    try:
        integration = get_integration(record.spec.integration_name)
        command = (integration.require_executable(), *argv[1:])
        copilot_home = record.copilot_home
        job_env = {**os.environ, "COPILOT_HOME": copilot_home} if copilot_home else None
        await run_in_threadpool(
            spawn_interactive_terminal,
            command,
            record.spec.resolved_workspace_root,
            env=job_env,
        )
    except (IntegrationError, OSError):
        outcome = "failed"
    return RedirectResponse(f"/{team}/jobs/{job_id}?resume={outcome}", status_code=303)


@router.post("/{team}/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def job_cancel(request: Request, team: str, job_id: str, services: FlowgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if team not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail="Unknown team")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    path = _job_path(services.job_store, team, job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        cancel_job(path)
    except InvalidJobTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/{team}/jobs/{job_id}", status_code=303)