from __future__ import annotations

from flowgency.tickets.models import AgentTicketContext, TicketEvent, TicketRecord, TicketReport
from flowgency.workflows.rules import EvaluatedTransition


def transition_event(
    record: TicketRecord,
    evaluated: EvaluatedTransition,
    actor: AgentTicketContext,
) -> TicketEvent:
    payload = evaluated.to_json()
    payload.update(
        {
            "transition_id": payload["transition_snapshot"]["id"],
            "transition_name": payload["transition_snapshot"]["name"],
            "source_state_id": record.state_id,
            "destination_state_id": evaluated.destination_state_id,
            "job_id": actor.job_id,
            "session_id": actor.session_id,
        }
    )
    return TicketEvent(
        kind="transitioned",
        actor=actor.agent_name,
        summary=f"Transition {payload['transition_name']} accepted",
        data=payload,
    )


def report_event(report: TicketReport, actor: AgentTicketContext) -> TicketEvent:
    return TicketEvent(
        kind="reported",
        actor=actor.agent_name,
        summary=report.message,
        data={
            "message": report.message,
            "assessments": [
                {
                    "criterion_id": assessment.criterion_id,
                    "satisfied": assessment.satisfied,
                    "reasoning": assessment.reasoning,
                    "supporting_fields": list(assessment.supporting_fields),
                }
                for assessment in report.assessments
            ],
            "job_id": actor.job_id,
            "session_id": actor.session_id,
        },
    )