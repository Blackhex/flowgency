from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request

from flowgency.tickets.views import BoardView, TicketDetailView, WorkflowBindingView


def _presentation_module(request: Request, binding: WorkflowBindingView, detail=None):
    template = request.app.state.templates.env.get_template("_ticket_presentation.html")
    return template.make_module(
        {
            "detail": detail,
            "current_ticket": detail.ticket if detail is not None else None,
            "team": binding.team_id,
            "active_workflow_id": binding.workflow_id,
        }
    )


def ticket_snapshot_payload(
    request: Request,
    detail: TicketDetailView,
    agent_options: Sequence[str],
) -> dict[str, Any]:
    payload = detail.model_dump(mode="json")
    module = _presentation_module(request, detail.binding, detail)
    payload["presentation"] = {
        "format": 1,
        "agent_options": list(agent_options),
        "description_html": str(module.description(detail)),
        "outputs_html": str(module.outputs(detail)),
        "requirements_html": str(module.requirements(detail)),
        "history_html": str(module.history(detail)),
        "issues_html": str(module.issues(detail.issues)),
    }
    return payload


def board_snapshot_payload(
    request: Request,
    board: BoardView,
    agent_options: Sequence[str],
) -> dict[str, Any]:
    payload = board.model_dump(mode="json")
    module = _presentation_module(request, board.binding, board.selected_ticket)
    payload["presentation"] = {
        "format": 1,
        "agent_options": list(agent_options),
        "issues_html": str(module.issues(board.issues)),
    }
    if board.selected_ticket is not None:
        payload["selected_ticket"] = ticket_snapshot_payload(
            request,
            board.selected_ticket,
            agent_options,
        )
    return payload