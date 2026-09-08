from __future__ import annotations

import pytest
from pydantic import ValidationError

from flowgency.tickets.models import TicketPatch, UserTicketContext
from flowgency.tickets.errors import TicketConflict, TicketForbidden, WorkflowUnavailable
from flowgency.tickets.storages.local import LocalTicketStorage
from tests._ticket_helpers import storage_binding, ticket_record


def test_create_assigns_initial_state_and_version(workflow_env):
    env = workflow_env
    ticket = env.create("Created", values={"summary": "hello"})
    assert ticket.record.state_id == "review"
    assert ticket.version.ref == ticket.ref
    assert ticket.definition is not None
    assert ticket.issues == ()


def test_stale_revision_is_rejected(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.service.update(
        env.user,
        ticket.version,
        ticket.patch(description="Changed once"),
        env.operation("first-update"),
    )
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Stale update"),
            env.operation("stale-update"),
        )


def test_binding_change_invalidates_old_version(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.set_storage_root(env.root_b)
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Wrong board"),
            env.operation("moved-binding"),
        )


def test_missing_blueprint_returns_readable_unavailable_view(workflow_env):
    env = workflow_env
    ticket = env.create()
    workflow_file = env.library.root / env.blueprint_id / "workflow.yaml"
    workflow_file.unlink()
    view = env.read(ticket.ref)
    assert view.definition is None
    assert view.version is None
    assert view.issues
    with pytest.raises(WorkflowUnavailable):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="No blueprint"),
            env.operation("missing-blueprint"),
        )


def test_unknown_agent_session_is_rejected(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.revoke_session(actor.session_id)
    with pytest.raises(TicketForbidden):
        env.service.start_work(
            actor,
            ticket.version,
            env.operation("untrusted", actor_name=actor.agent_name),
        )


def test_patch_rejects_state_and_actor_injection():
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"state_id": "done"})
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"actor_name": "spoofed"})


def test_agent_cannot_update_without_matching_active_run(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    with pytest.raises(TicketForbidden):
        env.service.update(
            actor,
            ticket.version,
            ticket.patch(description="Agent edit"),
            env.operation("agent-update", actor_name=actor.agent_name),
        )


def test_user_may_edit_during_active_work_and_provenance_is_trusted(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before"})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    updated = env.service.update(
        env.user,
        active.version,
        active.patch(field_values={"summary": "after"}),
        env.operation("user-edit"),
    )
    assert updated.ticket.assignee == "builder"
    assert updated.ticket.active_run is not None
    provenance = updated.ticket.field_provenance["summary"]
    assert provenance.actor_kind == "user"
    assert provenance.actor_name == env.user.actor_name
    assert provenance.event_id == updated.event_id


def test_transition_ready_update_retains_assignment(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before", "verdict": False})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start-transition", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    updated = env.service.update(
        env.user,
        active.version,
        active.patch(field_values={"verdict": True}),
        env.operation("make-ready"),
    )
    assert updated.ticket.assignee == "builder"
    assert updated.ticket.active_run is not None
    assert updated.ticket.field_values["verdict"] is True


def test_two_runs_of_same_agent_conflict(workflow_env):
    env = workflow_env
    ticket = env.create()
    first = env.agent("builder", "run-a")
    second = env.agent("builder", "run-b")
    env.service.start_work(
        first,
        ticket.version,
        env.operation("first-start", actor_name=first.agent_name),
    )
    with pytest.raises(TicketConflict):
        env.service.start_work(
            second,
            env.read(ticket.ref).version,
            env.operation("second-start", actor_name=second.agent_name),
        )


def test_later_session_of_same_job_conflicts(workflow_env):
    env = workflow_env
    ticket = env.create()
    first = env.agent("builder", "run-a")
    second = env.agent("builder", "run-a")
    env.service.start_work(
        first,
        ticket.version,
        env.operation("first-session", actor_name=first.agent_name),
    )
    with pytest.raises(TicketConflict):
        env.service.start_work(
            second,
            env.read(ticket.ref).version,
            env.operation("second-session", actor_name=second.agent_name),
        )


def test_older_generation_cannot_clear_later_marker(workflow_env):
    env = workflow_env
    ticket = env.create()
    first = env.agent("builder", "run-a")
    second = env.agent("builder", "run-b")
    env.service.start_work(
        first,
        ticket.version,
        env.operation("start-a", actor_name=first.agent_name),
    )
    env.service.end_work(
        first,
        env.read(ticket.ref).version,
        env.operation("end-a", actor_name=first.agent_name),
    )
    env.service.start_work(
        second,
        env.read(ticket.ref).version,
        env.operation("start-b", actor_name=second.agent_name),
    )
    latest = env.read(ticket.ref).version
    with pytest.raises(TicketForbidden):
        env.service.end_work(
            first,
            latest,
            env.operation("old-end", actor_name=first.agent_name),
        )
    with pytest.raises(TicketForbidden):
        env.service.sign_off(
            first,
            latest,
            env.operation("old-signoff", actor_name=first.agent_name),
        )


def test_deleted_configured_agent_is_rejected_before_receipt_replay(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    operation = env.operation("start", actor_name=actor.agent_name)
    env.service.start_work(actor, ticket.version, operation)
    snapshot = env.store.load()

    def patch(raw):
        raw["teams"][env.team_id]["agents"] = [
            agent
            for agent in raw["teams"][env.team_id]["agents"]
            if agent["name"] != actor.agent_name
        ]

    env.store.patch(snapshot.revision, patch)
    with pytest.raises(TicketForbidden):
        env.service.start_work(actor, ticket.version, operation)


def test_same_ticket_id_in_another_team_is_distinct(workflow_env):
    env = workflow_env
    support_provider = LocalTicketStorage(env.root_b, clock=env._clock)
    ref = support_provider._ref_for("support", env.workflow_id, "ticket-shared")
    support_provider.create(
        ticket_record(ticket_id="ticket-shared").with_ref(ref),
        env.operation("support-seed", actor_name="support"),
    )
    view = env.service.inspect(UserTicketContext(team_id="support"), ref)
    assert view.ref.team_id == "support"
    assert view.ref.ticket_id == "ticket-shared"
    with pytest.raises(TicketForbidden):
        env.service.inspect(env.user, ref)