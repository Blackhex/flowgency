from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from flowgency.tickets.views import build_board_view
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.workflow_context import require_team_and_workflow, require_ticket_services


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _team_context(request: Request, snapshot, team_id: str) -> dict[str, Any]:
    team_cfg = snapshot.config.teams[team_id]
    return {
        "team": team_id,
        "team_name": team_cfg.name,
        "teams": {key: value.name for key, value in snapshot.config.teams.items()},
        "flowgency_title": snapshot.config.flowgency.title,
        "admin_active": False,
        "workspaces": [workspace.model_dump(mode="json") for workspace in team_cfg.workspaces],
        "workspaces_available": bool(team_cfg.workspaces),
        "nav_open_observations": 0,
        "nav_actionable": 0,
        "nav_actionable_proposals": 0,
        "nav_agent_count": len(team_cfg.agents),
        "nav_running_decisions": 0,
        "show_tips": False,
        "tips_dismissed": [],
        "theme_css": _theme_css(request),
    }


def _workflow_nav(ticket_service, actor, snapshot, team_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    workflows = snapshot.config.teams[team_id].workflows
    for workflow_id, workflow in workflows.items():
        try:
            count = len(ticket_service.list_tickets(actor, workflow_id))
        except Exception:
            count = 0
        rows.append({"id": workflow_id, "name": workflow.name, "count": count})
    return rows


def _workflow_page_context(request: Request, services: FlowgencyServices, team_id: str, workflow_id: str, board, *, ticket_id: str | None) -> dict[str, Any]:
    snapshot = services.config_store.load()
    ticket_service = require_ticket_services(services)
    actor = require_team_and_workflow(services, team_id, workflow_id).actor
    context = _team_context(request, snapshot, team_id)
    context.update(
        {
            "active": "workflow-board",
            "workflow_nav": _workflow_nav(ticket_service, actor, snapshot, team_id),
            "active_workflow_id": workflow_id,
            "board": board,
            "selected_ticket_id": ticket_id,
            "workflow_initial": {
                "board": board.model_dump(mode="json"),
                "urls": {
                    "board": f"/{team_id}/workflows/{workflow_id}",
                    "snapshot": f"/{team_id}/workflows/{workflow_id}/snapshot",
                    "detail": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__",
                    "detailSnapshot": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/snapshot",
                    "assignee": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/assignee",
                    "update": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/update",
                    "create": f"/{team_id}/workflows/{workflow_id}/tickets",
                },
            },
        }
    )
    return context


def _etag(payload: dict[str, Any]) -> str:
    return f'W/"{hash(json.dumps(payload, sort_keys=True, default=str))}"'


def _json_with_etag(request: Request, payload: dict[str, Any]) -> Response:
    etag = _etag(payload)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


@router.get("/{team}/workflows/{workflow}")
async def workflow_board_page(
    request: Request,
    team: str,
    workflow: str,
    query: str = "",
    assignee: str | None = None,
    ticket: str | None = None,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    board = await run_in_threadpool(
        build_board_view,
        ticket_service,
        context.actor,
        workflow,
        query=query,
        assignee=assignee,
        selected_ticket_id=ticket,
        ticket_jobs=services.ticket_jobs,
    )
    template_context = _workflow_page_context(request, services, team, workflow, board, ticket_id=ticket)
    return _templates(request).TemplateResponse(request, "workflow_board.html", template_context)


@router.get("/{team}/workflows/{workflow}/snapshot")
async def workflow_board_snapshot(
    request: Request,
    team: str,
    workflow: str,
    query: str = "",
    assignee: str | None = None,
    selected_ticket: str | None = None,
    ticket: str | None = None,
    services: FlowgencyServices = Depends(get_services),
) -> Response:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    view = await run_in_threadpool(
        build_board_view,
        ticket_service,
        context.actor,
        workflow,
        query=query,
        assignee=assignee,
        selected_ticket_id=selected_ticket or ticket,
        ticket_jobs=services.ticket_jobs,
    )
    return _json_with_etag(request, view.model_dump(mode="json"))