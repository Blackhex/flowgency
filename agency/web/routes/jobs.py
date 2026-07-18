from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from agency.configuration.models import MemorySelector
from agency.jobs.authority import JobStore
from agency.jobs.store import InvalidJobTransition, cancel_job, read_job
from agency.web.dependencies import AgencyServices, get_services


router = APIRouter()


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


def _safe_job_id(job_id: str) -> str:
    if not job_id or Path(job_id).name != job_id or any(ch in job_id for ch in ("/", "\\")):
        raise HTTPException(status_code=404, detail="Job not found")
    return job_id


def _job_path(job_store: JobStore, group_id: str, job_id: str) -> Path:
    return job_store.path(group_id, _safe_job_id(job_id))


def _friendly_status(status: str) -> str:
    return {
        "waiting_for_memory": "Waiting for memory",
        "queued": "Queued",
        "running": "Running",
        "complete": "Complete",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(status, status.replace("_", " ").title())


def _status_badge_classes(status: str) -> str:
    return {
        "waiting_for_memory": (
            "bg-amber-100 text-amber-800 dark:bg-amber-900/50 "
            "dark:text-amber-100"
        ),
        "queued": (
            "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-100"
        ),
        "running": (
            "bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-100"
        ),
        "complete": (
            "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 "
            "dark:text-emerald-100"
        ),
        "failed": (
            "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-100"
        ),
        "cancelled": (
            "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-100"
        ),
    }.get(
        status,
        "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-100",
    )


def _friendly_trigger(trigger: str) -> str:
    return {
        "scheduled_prompt": "Scheduled routine",
        "manual_prompt": "Manual routine",
        "decision": "Decision",
        "decision_retry": "Decision retry",
    }.get(trigger, trigger.replace("_", " ").title())


def _routine_title(routine_id: str | None, prompt_source: dict[str, Any] | None) -> str:
    if prompt_source and isinstance(prompt_source.get("title"), str) and prompt_source.get("title"):
        return str(prompt_source["title"])
    if routine_id:
        return routine_id
    return "Ad hoc"


def _memory_label(selector_data: dict[str, object], snapshot) -> str:
    selector = MemorySelector.model_validate(selector_data)
    if selector.scope == "run":
        return "Run memory"
    if selector.scope == "routine":
        return "Routine memory"
    if selector.scope == "agent":
        return "Agent memory"
    if selector.scope == "group":
        return "Group memory"
    channel = snapshot.config.memory.channels.get(selector.channel or "")
    display = channel.display_name if channel is not None else (selector.channel or "Channel")
    return f"Channel: {display}"


def _artifact_root(job_store: JobStore, group_id: str, job_id: str) -> Path:
    return job_store.artifact_root(group_id, _safe_job_id(job_id))


def _validate_artifact_query(job_store: JobStore, group_id: str, job_id: str, artifact: str) -> Path:
    candidate = Path(artifact)
    if candidate.name != artifact or artifact in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    target = (_artifact_root(job_store, group_id, job_id) / artifact).resolve(strict=False)
    root = _artifact_root(job_store, group_id, job_id)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Artifact access denied") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return target


def _job_rows(snapshot, job_store: JobStore, group_id: str) -> list[dict[str, Any]]:
    group = snapshot.config.groups[group_id]
    rows: list[dict[str, Any]] = []
    for path in sorted(job_store.paths(group_id), key=lambda item: item.stat().st_mtime, reverse=True):
        record = read_job(path)
        instance = group.agents.get(record.spec.agent_name)
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
                "detail_href": f"/{group_id}/jobs/{record.spec.job_id}",
                "activity_href": f"/{group_id}/agents/{agent_name}/activity" if instance is not None else "",
                "instance_missing": instance is None,
            }
        )
    return rows


def _job_detail_context(snapshot, group_id: str, record) -> dict[str, Any]:
    group = snapshot.config.groups[group_id]
    instance = group.agents.get(record.spec.agent_name)
    agent_name = record.spec.agent_name
    artifact_dir = JobStore(snapshot.config.agency.memory_store).artifact_root(group_id, record.spec.job_id)
    failed_artifacts = []
    if artifact_dir.exists():
        for artifact in sorted(artifact_dir.glob("*.md")):
            label = "Failed memory snapshot" if artifact.name == "memory.md" else artifact.stem.replace("-", " ").title()
            failed_artifacts.append(
                {
                    "name": artifact.name,
                    "label": label,
                    "href": f"/{group_id}/jobs/{record.spec.job_id}?artifact={artifact.name}",
                }
            )
    publication = record.memory_publication or {}
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
        "activity_href": f"/{group_id}/agents/{agent_name}/activity" if instance is not None else "",
        "routine_href": f"/{group_id}/agents/{agent_name}/routines" if instance is not None else "",
        "profile_href": f"/{group_id}/agents/{agent_name}/profile" if instance is not None else "",
        "instance_missing": instance is None,
        "can_cancel": record.status in {"queued", "waiting_for_memory"},
        "diagnostic_memory_hash": record.spec.memory.memory_hash,
    }


@router.get("/{group}/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request, group: str, services: AgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if group not in snapshot.config.groups:
        raise HTTPException(status_code=404, detail="Unknown group")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    return _templates(request).TemplateResponse(
        request,
        "jobs.html",
        {
            "request": request,
            **_group_context(request, snapshot, group),
            "active": "jobs",
            "jobs": _job_rows(snapshot, services.job_store, group),
        },
    )


@router.get("/{group}/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, group: str, job_id: str, artifact: str = "", services: AgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if group not in snapshot.config.groups:
        raise HTTPException(status_code=404, detail="Unknown group")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    if artifact:
        target = _validate_artifact_query(services.job_store, group, job_id, artifact)
        return FileResponse(target)
    path = _job_path(services.job_store, group, job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    record = read_job(path)
    context = _job_detail_context(snapshot, group, record)
    return _templates(request).TemplateResponse(
        request,
        "job_detail.html",
        {
            "request": request,
            **_group_context(request, snapshot, group),
            "active": "jobs",
            **context,
        },
    )


@router.post("/{group}/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def job_cancel(request: Request, group: str, job_id: str, services: AgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if group not in snapshot.config.groups:
        raise HTTPException(status_code=404, detail="Unknown group")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    path = _job_path(services.job_store, group, job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        cancel_job(path)
    except InvalidJobTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/{group}/jobs/{job_id}", status_code=303)