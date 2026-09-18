"""Read-only routes for retained Git evidence.

Both routes serve bytes that were retained when the evidence was captured.
Neither runs Git, contacts a network, accepts a path, or offers a write.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from flowgency.tickets.errors import TicketStorageError
from flowgency.tickets.models import TicketRef
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.git_evidence import (
    GitDiffPreview,
    load_ticket_git_evidence,
    parse_git_diff,
    ticket_href,
)
from flowgency.web.team_navigation import build_team_context
from flowgency.web.workflow_context import require_team_and_workflow, require_ticket_services

router = APIRouter()

PATCH_FILENAME = "committed-changes.patch"
_ALLOWED_SOURCES = ("ticket", "job")


def _bound_ref(ticket_service, team: str, workflow: str, ticket: str) -> TicketRef:
    binding = ticket_service._resolve_current_binding(team, workflow)
    return TicketRef.from_binding(binding.storage, ticket)


def _load(ticket_service, actor, team: str, workflow: str, ticket: str, artifact_id: str):
    ref = _bound_ref(ticket_service, team, workflow, ticket)
    artifact, manifest = load_ticket_git_evidence(ticket_service, actor, ref, artifact_id)
    return ref, artifact, manifest


def _read_evidence(ticket_service, actor, team: str, workflow: str, ticket: str, artifact_id: str):
    try:
        return _load(ticket_service, actor, team, workflow, ticket, artifact_id)
    except TicketStorageError as error:
        # Every ticket error already carries a fixed public message and status.
        raise HTTPException(status_code=error.http_status, detail=error.message) from error


def _job_exists(services: FlowgencyServices, team: str, job_id: str) -> bool:
    if services.job_store is None or not job_id:
        return False
    try:
        return services.job_store.path(team, job_id).exists()
    except (OSError, ValueError):
        return False


@router.get(
    "/{team}/workflows/{workflow}/tickets/{ticket}/artifacts/{artifact_id}/diff",
    response_class=HTMLResponse,
)
async def git_evidence_diff(
    request: Request,
    team: str,
    workflow: str,
    ticket: str,
    artifact_id: str,
    source: str = "ticket",
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    if source not in _ALLOWED_SOURCES:
        raise HTTPException(status_code=422, detail="Unknown navigation source")
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    ref, _, manifest = await run_in_threadpool(
        _read_evidence, ticket_service, context.actor, team, workflow, ticket, artifact_id
    )
    preview: GitDiffPreview = await run_in_threadpool(parse_git_diff, manifest)
    back_href = ticket_href(ref)
    back_label = "Back to ticket"
    if source == "job" and _job_exists(services, team, manifest.job_id):
        back_href = f"/{team}/jobs/{manifest.job_id}"
        back_label = "Back to job"
    snapshot = services.config_store.load()
    template_context = build_team_context(
        snapshot,
        team,
        theme_css=request.app.state.theme_css_getter(),
        show_tips=False,
        tips_dismissed=[],
        ticket_service=ticket_service,
    )
    template_context.update(
        {
            "active": "workflow-board",
            "active_workflow_id": workflow,
            "manifest": manifest,
            "preview": preview,
            "artifact_id": artifact_id,
            "ticket_id": ticket,
            "back_href": back_href,
            "back_label": back_label,
            "patch_href": (
                f"/{team}/workflows/{workflow}/tickets/{ticket}"
                f"/artifacts/{artifact_id}/patch"
            ),
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "git_evidence.html", template_context
    )


@router.get("/{team}/workflows/{workflow}/tickets/{ticket}/artifacts/{artifact_id}/patch")
async def git_evidence_patch(
    team: str,
    workflow: str,
    ticket: str,
    artifact_id: str,
    services: FlowgencyServices = Depends(get_services),
) -> Response:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    _, _, manifest = await run_in_threadpool(
        _read_evidence, ticket_service, context.actor, team, workflow, ticket, artifact_id
    )
    patch = manifest.patch
    return Response(
        content=patch,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{PATCH_FILENAME}"',
            "Content-Length": str(len(patch)),
            "X-Content-Type-Options": "nosniff",
        },
    )
