from __future__ import annotations

from typing import Any

from flowgency.tickets.models import UserTicketContext


def build_workflow_nav(snapshot, team_id: str, ticket_service) -> tuple[list[dict[str, Any]], bool]:
    if snapshot.config.flowgency.workflow_library is None:
        return [], False

    actor = UserTicketContext(team_id=team_id)
    rows: list[dict[str, Any]] = []
    for workflow_id, workflow in snapshot.config.teams[team_id].workflows.items():
        row = {
            "id": workflow_id,
            "name": workflow.name,
            "count": None,
            "status": "unavailable",
        }
        if ticket_service is not None:
            try:
                row["count"] = len(ticket_service.list_tickets(actor, workflow_id))
                row["status"] = "count"
            except Exception:
                pass
        rows.append(row)
    return rows, True


def build_team_context(
    snapshot,
    team_id: str,
    *,
    theme_css: str,
    show_tips: bool,
    tips_dismissed: list[str] | tuple[str, ...],
    ticket_service=None,
) -> dict[str, Any]:
    team_cfg = snapshot.config.teams[team_id]
    workflow_nav, workflow_nav_available = build_workflow_nav(
        snapshot,
        team_id,
        ticket_service,
    )
    return {
        "team": team_id,
        "team_name": team_cfg.name,
        "teams": {key: value.name for key, value in snapshot.config.teams.items()},
        "flowgency_title": snapshot.config.flowgency.title,
        "admin_active": False,
        "workspaces": [workspace.model_dump(mode="json") for workspace in team_cfg.workspaces],
        "workspaces_available": bool(team_cfg.workspaces),
        "workflow_nav": workflow_nav,
        "workflow_nav_available": workflow_nav_available,
        "active_workflow_id": None,
        "nav_open_observations": 0,
        "nav_actionable": 0,
        "nav_actionable_proposals": 0,
        "nav_agent_count": len(team_cfg.agents),
        "nav_running_decisions": 0,
        "show_tips": show_tips,
        "tips_dismissed": list(tips_dismissed),
        "theme_css": theme_css,
    }