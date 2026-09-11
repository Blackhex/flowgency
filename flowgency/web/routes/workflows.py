from __future__ import annotations

import hashlib
import json
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from flowgency.tickets.views import build_board_view
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.team_navigation import build_team_context
from flowgency.web.workflow_context import require_team_and_workflow, require_ticket_services


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _team_context(request: Request, snapshot, team_id: str) -> dict[str, Any]:
    context = build_team_context(
        snapshot,
        team_id,
        theme_css=_theme_css(request),
        show_tips=False,
        tips_dismissed=[],
        ticket_service=request.app.state.services.tickets,
    )
    context["team_agents"] = tuple(snapshot.config.teams[team_id].agents.keys())
    return context


def _form_operation_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _workflow_page_context(request: Request, services: FlowgencyServices, team_id: str, workflow_id: str, board, *, ticket_id: str | None) -> dict[str, Any]:
    snapshot = services.config_store.load()
    ticket_service = require_ticket_services(services)
    actor = require_team_and_workflow(services, team_id, workflow_id).actor
    context = _team_context(request, snapshot, team_id)
    context.update(
        {
            "active": "workflow-board",
            "active_workflow_id": workflow_id,
            "board": board,
            "selected_ticket_id": ticket_id,
            "new_ticket_operation_id": _form_operation_id("ticket-create"),
            "ticket_form_operation_ids": {
                "assignee": _form_operation_id("ticket-assignee"),
                "run": _form_operation_id("ticket-run"),
                "update": _form_operation_id("ticket-update"),
            },
            "workflow_initial": {
                "board": board.model_dump(mode="json"),
                "urls": {
                    "board": f"/{team_id}/workflows/{workflow_id}",
                    "snapshot": f"/{team_id}/workflows/{workflow_id}/snapshot",
                    "detail": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__",
                    "detailSnapshot": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/snapshot",
                    "assignee": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/assignee",
                    "update": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/update",
                    "run": f"/{team_id}/workflows/{workflow_id}/tickets/__ticket__/run",
                    "create": f"/{team_id}/workflows/{workflow_id}/tickets",
                },
            },
        }
    )
    return context


def _etag(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f'W/"{digest}"'


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