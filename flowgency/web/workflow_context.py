from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from flowgency.tickets.models import UserTicketContext
from flowgency.web.dependencies import FlowgencyServices


@dataclass(frozen=True)
class UserWorkflowRouteContext:
    team_id: str
    workflow_id: str
    actor: UserTicketContext


def user_context(team_id: str) -> UserTicketContext:
    return UserTicketContext(team_id=team_id)


def require_ticket_services(services: FlowgencyServices):
    if services.tickets is None:
        raise HTTPException(status_code=503, detail="Ticket service unavailable")
    return services.tickets


def require_ticket_jobs(services: FlowgencyServices):
    if services.ticket_jobs is None:
        raise HTTPException(status_code=503, detail="Ticket job service unavailable")
    return services.ticket_jobs


def require_team_and_workflow(services: FlowgencyServices, team_id: str, workflow_id: str) -> UserWorkflowRouteContext:
    snapshot = services.config_store.load()
    if team_id not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail="Unknown team")
    if workflow_id not in snapshot.config.teams[team_id].workflows:
        raise HTTPException(status_code=404, detail="Unknown workflow")
    return UserWorkflowRouteContext(
        team_id=team_id,
        workflow_id=workflow_id,
        actor=user_context(team_id),
    )