from __future__ import annotations

from datetime import timezone

import pytest
from pydantic import ValidationError

from flowgency.tickets.errors import TicketConflict, TicketForbidden
from flowgency.tickets.models import TicketReport, TransitionRequest
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.models import CriterionAssessment
from flowgency.workflows.models import ContractError
from tests._git_evidence_helpers import requires_git
from tests._ticket_helpers import storage_binding, ticket_record


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

    assessment = CriterionAssessment(
        criterion_id="evidence-reviewed",
        satisfied=True,
        reasoning="Evidence was inspected and confirmed complete",
        supporting_fields=("verdict",),
    )

    reported = env.service.report(
        actor,
        before.version,
        env.ticket_report("Review declined", assessments=(assessment,)),
        env.operation("report", actor_name=actor.agent_name),
    )
    persisted = env.read(ticket.ref).record
    event = persisted.events[-1]
    assert reported.ticket.state_id == before.record.state_id
    assert len(reported.ticket.events) == len(before.record.events) + 1
    assert event.kind == "reported"
    assert event.data["job_id"] == actor.job_id
    assert event.data["session_id"] == actor.session_id
    assert event.data["message"] == "Review declined"
    assert event.data["assessments"] == [
        {
            "criterion_id": "evidence-reviewed",
            "satisfied": True,
            "reasoning": "Evidence was inspected and confirmed complete",
            "supporting_fields": ["verdict"],
        }
    ]


def test_transition_rejects_type_invalid_output_and_leaves_record_unchanged(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )

    before = env.read(ticket.ref).record
    with pytest.raises(ContractError) as failure:
        env.service.transition(
            actor,
            env.read(ticket.ref).version,
            env.transition_request(outputs={"summary": 5}),
            env.operation("complete", actor_name=actor.agent_name),
        )
    assert failure.value.code == "invalid-type"
    assert failure.value.field_id == "summary"

    after = env.read(ticket.ref).record
    assert after == before
    assert after.state_id == before.state_id
    assert after.field_values == before.field_values
    assert after.field_provenance == before.field_provenance
    assert after.revision == before.revision
    assert after.events == before.events
    assert after.receipts == before.receipts


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


def test_transition_audit_snapshot_retains_canonical_binding_digest_and_field_catalog(workflow_env):
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

    event = accepted.ticket.events[-1]
    snapshot = event.data
    assert snapshot["binding_id"] == accepted.ticket.ref.binding_id
    assert snapshot["binding_id"] == env.binding.binding_id
    assert event.actor == actor.agent_name
    assert event.at is not None
    assert event.at.tzinfo == timezone.utc
    assert snapshot["blueprint_id"] == env.blueprint_id
    assert snapshot["blueprint_name"] == "Delivery"
    assert snapshot["transition_snapshot"] == {
        "id": "complete",
        "name": "Complete",
        "from_state": "review",
        "to_state": "done",
        "inputs": [{"field_id": "verdict", "required": True}],
        "outputs": [{"field_id": "summary", "required": True}],
        "preconditions": [
            {"field_id": "verdict", "operator": "equals", "value": True}
        ],
        "criteria": [],
        "field_defs": {
            "summary": {"id": "summary", "label": "Review summary", "type": "text"},
            "verdict": {"id": "verdict", "label": "Review verdict", "type": "boolean"},
        },
    }


def test_transition_rejects_stale_binding_when_destination_has_same_ticket_id(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    stale_version = env.read(ticket.ref).version
    other_provider = resolve_storage(
        storage_binding(env.root_b, team_id=env.team_id, workflow_id=env.workflow_id),
        clock=env._clock,
    )
    other_ref = ticket.ref.model_copy(
        update={"binding_id": storage_binding(env.root_b, team_id=env.team_id, workflow_id=env.workflow_id).binding_id}
    )
    other_provider.create(
        ticket_record(ticket_id=ticket.ref.ticket_id).with_ref(other_ref),
        env.operation("seed-other-binding"),
    )
    env.set_storage_root(env.root_b)

    with pytest.raises(TicketConflict):
        env.service.transition(
            actor,
            stale_version,
            env.transition_request(outputs={"summary": "Verified existing work"}),
            env.operation("stale-binding", actor_name=actor.agent_name),
        )

    assert other_provider.read(other_ref).state_id == "review"


def test_report_rejects_wrong_run_and_revoked_session_without_appending_event(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    version = env.read(ticket.ref).version
    before = env.read(ticket.ref).record

    with pytest.raises(TicketForbidden):
        env.service.report(
            env.agent("builder", "run-b"),
            version,
            env.ticket_report("wrong run"),
            env.operation("wrong-run", actor_name="builder"),
        )

    env.revoke_session(actor.session_id)
    with pytest.raises(TicketForbidden):
        env.service.report(
            actor,
            version,
            env.ticket_report("revoked"),
            env.operation("revoked-session", actor_name=actor.agent_name),
        )

    assert env.read(ticket.ref).record == before


def test_transition_rejects_user_actor_without_mutating_ticket(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    before = env.read(ticket.ref).record

    with pytest.raises(TicketForbidden):
        env.service.transition(
            env.user,
            ticket.version,
            env.transition_request(outputs={"summary": "Verified existing work"}),
            env.operation("user-transition"),
        )

    assert env.read(ticket.ref).record == before


def test_transition_audit_snapshot_persists_session_id_and_nonempty_assessment(workflow_env):
    env = workflow_env
    env.publish_criteria_workflow()
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    assessment = CriterionAssessment(
        criterion_id="evidence-reviewed",
        satisfied=True,
        reasoning="Evidence was inspected and confirmed complete",
        supporting_fields=("verdict",),
    )

    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(
            outputs={"summary": "Reviewed with evidence"},
            assessments=(assessment,),
        ),
        env.operation("complete", actor_name=actor.agent_name),
    )

    # Reread from the real provider to verify on-disk persistence
    persisted = env.read(ticket.ref).record
    event = persisted.events[-1]
    snapshot = event.data

    assert event.actor == actor.agent_name
    assert snapshot["job_id"] == actor.job_id
    assert snapshot["session_id"] == actor.session_id
    assert snapshot["assessments"] == [
        {
            "criterion_id": "evidence-reviewed",
            "satisfied": True,
            "reasoning": "Evidence was inspected and confirmed complete",
            "supporting_fields": ["verdict"],
        }
    ]
    assert snapshot["transition_id"] == "complete"
    assert snapshot["blueprint_id"] == env.blueprint_id
    assert snapshot["effective_inputs"] == {"verdict": True}
    assert snapshot["effective_outputs"] == {"summary": "Reviewed with evidence"}


def test_attempt_only_inputs_do_not_update_current_fields_or_provenance(workflow_env):
    env = workflow_env
    # Ticket starts with verdict=False stored; the agent supplies True only as an attempt input
    ticket = env.create(values={"verdict": False, "summary": "initial"})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )

    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        TransitionRequest(
            transition_id="complete",
            inputs={"verdict": True},  # attempt-only; not a declared output
            outputs={"summary": "Completed via input override"},
        ),
        env.operation("complete", actor_name=actor.agent_name),
    )

    # Reread from real provider to verify on-disk persistence
    persisted = env.read(ticket.ref).record

    # Attempt-only input does not overwrite the stored field value or its provenance
    assert persisted.field_values["verdict"] is False
    assert persisted.field_provenance["verdict"].actor_kind == "user"

    # Declared output persists with agent provenance
    assert persisted.field_values["summary"] == "Completed via input override"
    prov = persisted.field_provenance["summary"]
    assert prov.actor_kind == "agent"
    assert prov.job_id == actor.job_id

    # The accepted attempt's effective input is retained in the event snapshot
    event = persisted.events[-1]
    assert event.data["effective_inputs"] == {"verdict": True}


# -- trusted Git evidence at the transition boundary ------------------------


def _captured_evidence(env, name: str = "source"):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from tests._git_evidence_helpers import (
        capture_request,
        configure_git_ticket,
        create_git_repository,
    )

    fixture = create_git_repository(env.tmp_path / name)
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    result = env.service.capture_git_evidence(
        actor,
        ticket.version,
        capture_request(fixture),
        env.operation("capture", actor_name="builder"),
    )
    return actor, ticket, result


def _complete_with(env, actor, ticket, artifact, label: str):
    return env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Done", "evidence": artifact}),
        env.operation(label, actor_name="builder"),
    )


@requires_git
def test_transition_rejects_evidence_after_the_publication_policy_changed(workflow_env):
    from flowgency.git_evidence.models import GitPublicationPolicy

    env = workflow_env
    actor, ticket, result = _captured_evidence(env)
    snapshot = env.store.load()
    env.store.patch(
        snapshot.revision,
        lambda raw: raw["teams"][env.team_id].__setitem__(
            "git_publication",
            GitPublicationPolicy(
                mode="local", allowed_refs=("refs/heads/main",)
            ).model_dump(mode="json"),
        ),
    )
    before = env.read(ticket.ref).record

    with pytest.raises(TicketConflict) as failure:
        _complete_with(env, actor, ticket, result.artifact, "policy-changed")

    assert failure.value.code == "git-evidence-policy-changed"
    assert env.read(ticket.ref).record == before


@requires_git
def test_transition_rejects_evidence_after_the_workspace_changed(workflow_env):
    from tests._git_evidence_helpers import create_git_repository

    env = workflow_env
    actor, ticket, result = _captured_evidence(env)
    moved = create_git_repository(env.tmp_path / "moved")
    snapshot = env.store.load()
    env.store.patch(
        snapshot.revision,
        lambda raw: raw["teams"][env.team_id].__setitem__(
            "workspace_path", str(moved.root)
        ),
    )
    before = env.read(ticket.ref).record

    with pytest.raises(TicketConflict) as failure:
        _complete_with(env, actor, ticket, result.artifact, "workspace-changed")

    assert failure.value.code == "git-evidence-workspace-changed"
    assert env.read(ticket.ref).record == before


@requires_git
def test_transition_rejects_a_corrupt_or_absent_evidence_blob(workflow_env):
    from flowgency.tickets.errors import TicketCorrupt, TicketNotFound
    from flowgency.workflows.models import ArtifactRef

    env = workflow_env
    actor, ticket, result = _captured_evidence(env)
    before = env.read(ticket.ref).record

    with pytest.raises(TicketNotFound):
        _complete_with(
            env, actor, ticket, ArtifactRef(kind="id", value="b" * 64), "absent-blob"
        )
    assert env.read(ticket.ref).record == before

    provider = env.current_provider()
    provider._artifact_path(ticket.ref, result.artifact.value).write_bytes(b"{")
    with pytest.raises(TicketCorrupt):
        _complete_with(env, actor, ticket, result.artifact, "corrupt-blob")
    assert env.read(ticket.ref).record == before


@requires_git
def test_evidence_validation_checks_receipt_binding_and_survives_history(workflow_env):
    from flowgency.tickets.errors import TicketEvidenceInvalid
    from flowgency.tickets.git_evidence import validate_git_artifact

    env = workflow_env
    _, ticket, result = _captured_evidence(env)
    record = env.read(ticket.ref).record
    artifact = env.current_provider().read_artifact(ticket.ref, result.artifact.value)

    # Historical read-only display proves the binding without today's policy.
    manifest = validate_git_artifact(record, artifact)
    assert manifest.ticket_id == record.id

    captured = record.events[-1]
    forged = record.model_copy(
        update={
            "events": record.events[:-1]
            + (
                captured.model_copy(
                    update={
                        "data": {
                            **captured.data,
                            "capture": {
                                **captured.data["capture"],
                                "repository_id": "f" * 64,
                            },
                        }
                    }
                ),
            )
        }
    )
    with pytest.raises(TicketEvidenceInvalid):
        validate_git_artifact(forged, artifact)

    with pytest.raises(TicketForbidden):
        validate_git_artifact(record.model_copy(update={"id": "ticket-other"}), artifact)
