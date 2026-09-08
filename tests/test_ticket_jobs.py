from __future__ import annotations

from dataclasses import replace

import pytest

from flowgency.jobs.store import read_job, write_job
from flowgency.tickets.errors import TicketConflict, TicketForbidden
from flowgency.tickets.models import UserTicketContext

from tests._ticket_helpers import delivery_definition, make_ticket_job_environment


@pytest.fixture
def ticket_job_env(tmp_path, raw_config, monkeypatch):
    return make_ticket_job_environment(tmp_path, raw_config, monkeypatch)


def test_queued_ticket_job_rechecks_assignment(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    env.assign_idle(ticket.ref, "observer")

    with pytest.raises(TicketConflict):
        env.coordinator.preflight(env.jobs.read(handle))

    assert env.read(ticket.ref).record.active_run is None


def test_queued_ticket_job_rechecks_same_agent_after_intervening_reassignment(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    env.assign_idle(ticket.ref, "observer")
    env.assign_idle(ticket.ref, "builder")

    with pytest.raises(TicketConflict):
        env.coordinator.preflight(env.jobs.read(handle))


def test_duplicate_submit_returns_same_live_job(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    first = env.coordinator.submit(env.user, ticket.version, "run-request")
    second = env.coordinator.submit(
        env.user,
        env.read(ticket.ref).version,
        "run-request",
    )

    assert second.job_id == first.job_id
    assert second.path == first.path


def test_duplicate_submit_replays_same_live_job_for_original_version(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    first = env.coordinator.submit(env.user, ticket.version, "run-request")
    second = env.coordinator.submit(env.user, ticket.version, "run-request")

    assert second.job_id == first.job_id
    assert second.path == first.path
    current = env.read(ticket.ref).record
    assert sum(1 for event in current.events if event.kind == "ticket-run-reserved") == 1


def test_conflicting_duplicate_submit_is_rejected(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    env.coordinator.submit(env.user, ticket.version, "run-request")

    with pytest.raises(TicketConflict, match="queued run"):
        env.coordinator.submit(
            env.user,
            env.read(ticket.ref).version,
            "different-request",
        )


def test_submit_rejects_cross_team_user_without_side_effects(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    actor = UserTicketContext(team_id="support", actor_name="other-user")

    with pytest.raises(TicketForbidden, match="another team"):
        env.coordinator.submit(actor, ticket.version, "run-request")

    assert env.read(ticket.ref).record.pending_run is None
    assert not env.job_store.paths("newsletter")
    assert not env.job_store.paths("support")


def test_new_submit_rejects_stale_revision_without_side_effects(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    env.service.update(
        env.user,
        ticket.version,
        ticket.patch(title="Updated title"),
        env.operation("update-before-run"),
    )

    with pytest.raises(TicketConflict, match="Refresh the ticket"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    assert env.read(ticket.ref).record.pending_run is None
    assert not env.job_store.paths(env.team_id)


def test_new_submit_rejects_changed_workflow_source_without_side_effects(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    changed = delivery_definition()
    changed["description"] = "Changed source digest"
    env.write_blueprint(env.blueprint_id, changed)

    with pytest.raises(TicketConflict, match="Refresh the ticket"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    assert env.read(ticket.ref).record.pending_run is None
    assert not env.job_store.paths(env.team_id)


def test_exact_submit_replay_keeps_original_job_after_workflow_source_changes(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    first = env.coordinator.submit(env.user, ticket.version, "run-request")
    changed = delivery_definition()
    changed["description"] = "Changed source digest"
    env.write_blueprint(env.blueprint_id, changed)

    replay = env.coordinator.submit(env.user, ticket.version, "run-request")

    assert replay.job_id == first.job_id
    assert replay.path == first.path


@pytest.mark.parametrize("status", ["complete", "failed", "cancelled"])
def test_new_submit_replaces_verified_terminal_pending_run_without_losing_replay(
    ticket_job_env,
    status,
):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    first = env.coordinator.submit(env.user, ticket.version, "run-request")
    original = read_job(first.path)
    write_job(
        first.path,
        replace(
            original,
            status=status,
            completed_at="2026-09-08T00:05:00+00:00",
        ),
    )

    current = env.read(ticket.ref).version
    second = env.coordinator.submit(env.user, current, "rerun-request")
    replay = env.coordinator.submit(env.user, env.read(ticket.ref).version, "run-request")

    assert second.job_id != first.job_id
    assert env.read(ticket.ref).record.pending_run is not None
    assert env.read(ticket.ref).record.pending_run.request_id == "rerun-request"
    assert replay.job_id == first.job_id
    original_reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert original_reservation is not None
    assert original_reservation.job_id == first.job_id


def test_cleanup_retains_assignment_for_all_run_targets(ticket_job_env):
    from flowgency.jobs.processes import ProcessStopEvidence

    env = ticket_job_env
    authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")

    env.coordinator.cleanup(
        authority,
        ProcessStopEvidence(
            job_id="run-a",
            generation=env.generation,
            confirmed=True,
            reason="exited",
        ),
    )

    for ref in targets:
        record = env.read(ref).record
        assert record.assignee == "builder"
        assert record.active_run is None


def test_cleanup_rejects_mismatched_confirmed_job_identity(ticket_job_env):
    from flowgency.jobs.processes import ProcessStopEvidence

    env = ticket_job_env
    authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")

    result = env.coordinator.cleanup(
        authority,
        ProcessStopEvidence(
            job_id="other-job",
            generation=env.generation,
            confirmed=True,
            reason="exited",
        ),
    )

    assert set(result.pending_cleanup) == set(targets)
    for ref in targets:
        record = env.read(ref).record
        assert record.active_run is not None
        assert all(event.kind != "ticket-run-cleanup" for event in record.events)


def test_start_work_consumes_matching_pending_run(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    authority = env.jobs.authority_for_handle(handle)
    queued = read_job(handle.path)
    write_job(
        handle.path,
        replace(
            queued,
            status="running",
            worker_pid=123,
            started_at="2026-09-08T00:00:00+00:00",
            launched_at="2026-09-08T00:00:00+00:00",
        ),
    )

    with env.broker_session(authority) as (_, client):
        started = client.call(
            "start_work",
            {
                "version": env.read(ticket.ref).version.model_dump(mode="json"),
                "operation_id": "start-matching-pending",
            },
        )

    assert started["ok"] is True
    live = env.read(ticket.ref).record
    assert live.active_run is not None
    assert live.active_run.job_id == handle.job_id
    assert live.pending_run is None


def test_start_work_rejects_other_run_when_another_job_is_pending(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    other_authority = env.running_job("builder", "other-run")

    with env.broker_session(other_authority) as (_, client):
        started = client.call(
            "start_work",
            {
                "version": env.read(ticket.ref).version.model_dump(mode="json"),
                "operation_id": "start-other-pending",
            },
        )

    assert started["ok"] is False
    assert started["error"]["code"] == "already-queued"
    live = env.read(ticket.ref).record
    assert live.active_run is None
    assert live.pending_run is not None
    assert live.pending_run.job_id == handle.job_id