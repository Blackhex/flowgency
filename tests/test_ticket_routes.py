from __future__ import annotations

import json

import flowgency.jobs.submission as submission_module
import pytest
from dataclasses import replace

from flowgency.jobs.authority import JobStore
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.tickets.models import TicketRef, TicketRecord
from flowgency.tickets.storages.local import LocalTicketStorage
from flowgency.integrations.models import RuntimeCapabilities
from tests._ticket_helpers import SEED_TIME, TicketRuntimeIntegration, storage_binding, ticket_record


def test_user_update_cannot_set_state(workflow_web_env):
    env = workflow_web_env
    ticket = env.create()

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
        data={
            "payload": json.dumps(
                {
                    "version": ticket.version.model_dump(mode="json"),
                    "operation_id": "edit-a",
                    "patch": {"state_id": "done"},
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert env.read(ticket.ref).record.state_id == "review"


def test_no_user_transition_endpoint(workflow_web_env):
    env = workflow_web_env
    ticket = env.create()

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/transition",
        json={"state": "done", "agent_name": "builder", "job_id": "run-a"},
    )

    assert response.status_code in (404, 405)


def test_assignee_post_is_revision_checked(workflow_web_env):
    env = workflow_web_env
    ticket = env.create()
    payload = {
        "version": ticket.version.model_dump(mode="json"),
        "assignee": "builder",
        "operation_id": "assign-a",
    }

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/assignee",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )
    payload["assignee"] = "observer"
    payload["operation_id"] = "assign-b"
    stale = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/assignee",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert stale.status_code == 409
    assert env.read(ticket.ref).record.assignee == "builder"


def test_assignee_post_rejects_active_reassignment(workflow_web_env):
    env = workflow_web_env
    ticket = env.create()
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-builder"))
    builder = env.agent("builder", "run-a")
    env.service.start_work(
        builder,
        env.read(ticket.ref).version,
        env.operation("start-builder", actor_name=builder.agent_name),
    )
    current = env.read(ticket.ref)

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/assignee",
        data={
            "payload": json.dumps(
                {
                    "version": current.version.model_dump(mode="json"),
                    "assignee": None,
                    "operation_id": "unassign-active",
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert env.read(ticket.ref).record.assignee == "builder"


def test_create_route_keeps_markdown_raw_and_initial_state(workflow_web_env):
    env = workflow_web_env
    description = "# Heading\n\n<script>alert(1)</script>"

    create_response = env.client.post(
        f"{env.base_path}/tickets",
        data={
            "payload": json.dumps(
                {
                    "operation_id": "create-markdown",
                    "title": "Markdown ticket",
                    "description": description,
                    "field_values": {"summary": "hello"},
                }
            )
        },
        follow_redirects=False,
    )

    assert create_response.status_code == 303
    detail = env.client.get(create_response.headers["location"])
    payload = detail.json()
    assert payload["ticket"]["description"] == description
    assert payload["ticket"]["state_id"] == "review"
    assert "body_html" not in payload["ticket"]


def test_update_rejects_same_id_stale_binding_ref(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="Old binding")
    stale_version = ticket.version
    env.set_storage_root(env.root_b)

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
        data={
            "payload": json.dumps(
                {
                    "version": stale_version.model_dump(mode="json"),
                    "operation_id": "stale-binding",
                    "patch": {"title": "Updated"},
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 422


def test_run_post_replays_existing_durable_job(workflow_web_env, monkeypatch):
    env = workflow_web_env
    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": TicketRuntimeIntegration()})
    ticket = env.create(title="Queued once")
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-builder"))
    current = env.read(ticket.ref)
    payload = {
        "version": current.version.model_dump(mode="json"),
        "operation_id": "run-request",
    }

    first = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/run",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )
    second = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/run",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert second.status_code == 303
    assert len(env.job_store.paths(env.team_id)) == 1
    assert env.read(ticket.ref).record.pending_run is not None


def test_run_post_rejects_assignee_without_live_ticket_channel(workflow_web_env, monkeypatch):
    env = workflow_web_env

    class UnsupportedTicketRuntime(TicketRuntimeIntegration):
        declared_runtime_capabilities = replace(
            TicketRuntimeIntegration.declared_runtime_capabilities,
            live_ticket_transport=None,
        )

    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": UnsupportedTicketRuntime()})
    ticket = env.create(title="No channel")
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-builder"))
    current = env.read(ticket.ref)

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/run",
        data={
            "payload": json.dumps(
                {
                    "version": current.version.model_dump(mode="json"),
                    "operation_id": "run-request",
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert len(env.job_store.paths(env.team_id)) == 0


def test_detail_and_artifact_download_use_current_namespace_only(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(values={"summary": "hello", "verdict": True})
    artifact = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"newsletter evidence",
    )
    env.publish_artifact_field_workflow()
    env.service.update(
        env.user,
        env.read(ticket.ref).version,
        env.read(ticket.ref).patch(field_values={"evidence": artifact}),
        env.operation("attach-artifact"),
    )

    support_binding = storage_binding(env.root_b, team_id="support", workflow_id="board-a")
    support_provider = LocalTicketStorage(env.root_b, clock=lambda: SEED_TIME)
    support_ref = TicketRef.from_binding(support_binding, ticket.ref.ticket_id)
    support_record = ticket_record(ticket.ref.ticket_id, state_id="review").with_ref(support_ref)
    support_provider.create(
        support_record,
        env.operation("support-seed", actor_name="support-user"),
    )
    support_provider.put_artifact(
        support_ref,
        RetainedArtifact.create("review.txt", "text/plain", b"support evidence"),
    )

    detail = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")
    download = env.client.get(f"{env.base_path}/artifacts/{artifact.value}")

    assert detail.status_code == 200
    assert detail.json()["ticket"]["artifact_ids"] == [artifact.value]
    assert download.status_code == 200
    assert download.content == b"newsletter evidence"
    assert download.headers["content-disposition"] == 'attachment; filename="review.txt"'
    assert download.headers["x-content-type-options"] == "nosniff"