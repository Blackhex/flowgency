from __future__ import annotations

from dataclasses import replace
import json

import pytest

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