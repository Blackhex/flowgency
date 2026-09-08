from __future__ import annotations

import pytest

from flowgency.tickets.errors import TicketConflict, TicketForbidden


def test_another_run_cannot_take_assigned_ticket(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign"))
    version = env.read(ticket.ref).version
    with pytest.raises(TicketForbidden):
        env.service.start_work(
            env.agent("observer", "run-b"),
            version,
            env.operation("steal", actor_name="observer"),
        )
    assert env.read(ticket.ref).record.active_run is None


def test_one_run_works_two_tickets_and_sign_off_is_optional(workflow_env):
    env = workflow_env
    first, second = env.create("First"), env.create("Second")
    actor = env.agent("builder", "run-a")
    for ticket in (first, second):
        env.service.start_work(
            actor,
            ticket.version,
            env.operation(ticket.ref.ticket_id, actor_name=actor.agent_name),
        )
    env.service.end_work(
        actor,
        env.read(first.ref).version,
        env.operation("end-first", actor_name=actor.agent_name),
    )
    assert env.read(first.ref).record.assignee == "builder"
    assert env.read(first.ref).record.active_run is None
    assert env.read(second.ref).record.active_run.job_id == "run-a"


def test_user_cannot_reassign_active_ticket(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    for assignee in (None, "observer"):
        with pytest.raises(TicketConflict):
            env.service.assign(
                env.user,
                active.version,
                assignee,
                env.operation(f"assign-{assignee}"),
            )
    assert env.read(ticket.ref).record == active.record