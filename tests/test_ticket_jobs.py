from __future__ import annotations

import pytest

from flowgency.tickets.errors import TicketConflict

from tests._ticket_helpers import make_ticket_job_environment


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