from __future__ import annotations

from dataclasses import replace
import json

import pytest

from flowgency.tickets.access import TicketAccessRegistry
from flowgency.tickets.errors import TicketForbidden


def test_closed_session_cannot_mutate(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    grant = env.access_registry.open(authority)
    env.access_registry.close(grant.session_id)

    with pytest.raises(TicketForbidden):
        env.access_registry.authenticate(grant.token)


def test_open_persists_token_hash_only(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    grant = env.access_registry.open(authority)
    access_path = env.access_registry._access_path(env.team_id, authority.job_id)
    payload = json.loads(access_path.read_text(encoding="utf-8"))

    assert grant.token not in access_path.read_text(encoding="utf-8")
    assert payload["sessions"][grant.session_id]["token_hash"]
    assert payload["sessions"][grant.session_id]["context"]["agent_name"] == "builder"


def test_authenticate_rejects_ended_job(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    grant = env.access_registry.open(authority)
    env.job_store.write(authority, replace(env.job_store.read(authority), status="complete"))

    with pytest.raises(TicketForbidden, match="ended-session"):
        env.access_registry.authenticate(grant.token)


def test_authenticate_rejects_stale_grant_after_running_relaunch(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    grant = env.access_registry.open(authority)
    record = env.job_store.read(authority)
    env.job_store.write(
        authority,
        replace(
            record,
            worker_pid=456,
            started_at="2026-09-08T00:01:00+00:00",
            launched_at="2026-09-08T00:01:00+00:00",
            session_id="job-session-builder-run-a-relaunched",
        ),
    )

    with pytest.raises(TicketForbidden) as excinfo:
        env.access_registry.authenticate(grant.token)

    assert excinfo.value.code == "invalid-token"


def test_original_target_ledger_survives_supersession_switch_and_close(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    ticket = env.create()
    binding = env.service.list_workflows(env.user)[0]

    first = env.access_registry.open(authority)
    env.access_registry.register_target(first.context, binding, ticket.ref)

    second = env.access_registry.open(authority)
    env.access_registry.close(second.session_id)
    env.set_storage_root(env.root_b)

    reloaded = TicketAccessRegistry(env.job_store)
    targets = reloaded.read_original_targets(authority)

    with pytest.raises(TicketForbidden) as excinfo:
        reloaded.authenticate(first.token)

    assert excinfo.value.code == "invalid-token"
    assert len(targets) == 1
    assert targets[0].generation == first.session_id
    assert targets[0].ref == ticket.ref
    assert targets[0].binding == binding.storage