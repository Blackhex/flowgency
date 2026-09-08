from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from flowgency.tickets.views import build_board_view
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.workflow_context import require_team_and_workflow, require_ticket_services


router = APIRouter()


@router.get("/{team}/workflows/{workflow}")
async def workflow_board_redirect(
    team: str,
    workflow: str,
    services: FlowgencyServices = Depends(get_services),
):
    await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    return RedirectResponse(f"/{team}/workflows/{workflow}/snapshot", status_code=303)


@router.get("/{team}/workflows/{workflow}/snapshot")
async def workflow_board_snapshot(
    request: Request,
    team: str,
    workflow: str,
    query: str = "",
    assignee: str | None = None,
    selected_ticket: str | None = None,
    services: FlowgencyServices = Depends(get_services),
) -> JSONResponse:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    view = await run_in_threadpool(
        build_board_view,
        ticket_service,
        context.actor,
        workflow,
        query=query,
        assignee=assignee,
        selected_ticket_id=selected_ticket,
        ticket_jobs=services.ticket_jobs,
    )
    return JSONResponse(view.model_dump(mode="json"))