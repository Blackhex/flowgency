from __future__ import annotations

import pytest
from pydantic import ValidationError

from flowgency.tickets.errors import TicketConflict
from flowgency.tickets.models import TicketReport, TransitionRequest
from flowgency.workflows.models import CriterionAssessment
from flowgency.workflows.models import ContractError


def test_rejected_transition_leaves_record_unchanged_and_report_is_separate(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": False})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )

    before = env.read(ticket.ref)
    with pytest.raises(ContractError):
        env.service.transition(
            actor,
            before.version,
            env.transition_request(outputs={"summary": "Reviewed"}),
            env.operation("reject", actor_name=actor.agent_name),
        )

    unchanged = env.read(ticket.ref).record
    assert unchanged == before.record

    reported = env.service.report(
        actor,
        before.version,
        env.ticket_report("Review declined"),
        env.operation("report", actor_name=actor.agent_name),
    )
    assert reported.ticket.state_id == before.record.state_id
    assert len(reported.ticket.events) == len(before.record.events) + 1
    assert reported.ticket.events[-1].kind == "reported"


def test_transition_commits_state_outputs_and_audit_snapshot(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )

    accepted = env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Verified existing work"}),
        env.operation("complete", actor_name=actor.agent_name),
    )

    assert accepted.ticket.state_id == "done"
    assert accepted.ticket.assignee == "builder"
    assert accepted.ticket.active_run is not None
    assert accepted.ticket.active_run.job_id == "run-a"
    assert accepted.event_id == accepted.ticket.events[-1].id

    event = accepted.ticket.events[-1]
    assert event.kind == "transitioned"
    assert event.summary == "Transition Complete accepted"
    snapshot = event.data
    assert snapshot["transition_id"] == "complete"
    assert snapshot["transition_name"] == "Complete"
    assert snapshot["source_state_id"] == "review"
    assert snapshot["destination_state_id"] == "done"
    assert snapshot["source_state_name"] == "Review"
    assert snapshot["destination_state_name"] == "Done"
    assert snapshot["effective_inputs"] == {"verdict": True}
    assert snapshot["effective_outputs"] == {"summary": "Verified existing work"}
    assert snapshot["workflow_digest"] == env.read(ticket.ref).version.workflow_digest
    assert snapshot["context_digest"] == env.read(ticket.ref).version.context_digest
    assert snapshot["job_id"] == "run-a"
    assert snapshot["operation_id"] == f"op-{actor.agent_name}-complete"


def test_transition_rejects_stale_workflow_without_appending_event(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    stale_version = env.read(ticket.ref).version
    env.rename_transition("complete", "Finish review")

    before = env.read(ticket.ref).record
    with pytest.raises(TicketConflict):
        env.service.transition(
            actor,
            stale_version,
            env.transition_request(outputs={"summary": "Verified existing work"}),
            env.operation("complete", actor_name=actor.agent_name),
        )
    after = env.read(ticket.ref).record
    assert after == before


def test_transition_rejects_missing_and_negative_assessments_atomically(workflow_env):
    env = workflow_env
    env.publish_criteria_workflow()
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    version = env.read(ticket.ref).version
    before = env.read(ticket.ref).record

    with pytest.raises(ContractError):
        env.service.transition(
            actor,
            version,
            env.transition_request(outputs={"summary": "Verified existing work"}),
            env.operation("missing-assessment", actor_name=actor.agent_name),
        )

    with pytest.raises(ContractError):
        env.service.transition(
            actor,
            version,
            env.transition_request(
                outputs={"summary": "Verified existing work"},
                assessments=(
                    CriterionAssessment(
                        criterion_id="evidence-reviewed",
                        satisfied=False,
                        reasoning="Evidence is incomplete",
                        supporting_fields=("verdict",),
                    ),
                ),
            ),
            env.operation("negative-assessment", actor_name=actor.agent_name),
        )

    assert env.read(ticket.ref).record == before


def test_transition_rejects_stale_ticket_revision_after_user_update(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    stale_version = env.read(ticket.ref).version
    env.service.update(
        env.user,
        stale_version,
        env.read(ticket.ref).patch(description="User note"),
        env.operation("user-edit"),
    )

    before = env.read(ticket.ref).record
    with pytest.raises(TicketConflict):
        env.service.transition(
            actor,
            stale_version,
            env.transition_request(outputs={"summary": "Verified existing work"}),
            env.operation("stale-revision", actor_name=actor.agent_name),
        )
    assert env.read(ticket.ref).record == before


def test_transition_replays_original_receipt_after_later_ticket_changes(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    transition_version = env.read(ticket.ref).version
    operation = env.operation("complete", actor_name=actor.agent_name)
    first = env.service.transition(
        actor,
        transition_version,
        env.transition_request(outputs={"summary": "Verified existing work"}),
        operation,
    )

    current = env.read(ticket.ref)
    env.service.update(
        env.user,
        current.version,
        current.patch(description="Follow-up note"),
        env.operation("post-transition-edit"),
    )

    replayed = env.service.transition(
        actor,
        transition_version,
        env.transition_request(outputs={"summary": "Verified existing work"}),
        operation,
    )
    assert replayed.replayed is True
    assert replayed.ticket == first.ticket
    assert replayed.event_id == first.event_id


def test_transition_audit_snapshot_is_immutable_after_definition_rename(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    accepted = env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Verified existing work"}),
        env.operation("complete", actor_name=actor.agent_name),
    )

    before_event = accepted.ticket.events[-1]
    env.rename_transition("complete", "Finish review")
    assert env.read(ticket.ref).record.events[-1] == before_event


def test_transition_request_and_report_reject_forged_actor_or_state_fields():
    with pytest.raises(ValidationError):
        TransitionRequest.model_validate(
            {"transition_id": "complete", "state_id": "done", "outputs": {}}
        )
    with pytest.raises(ValidationError):
        TransitionRequest.model_validate(
            {"transition_id": "complete", "actor_name": "spoofed", "outputs": {}}
        )
    with pytest.raises(ValidationError):
        TicketReport.model_validate({"message": "note", "state_id": "done"})
