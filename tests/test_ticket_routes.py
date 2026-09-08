from __future__ import annotations

import hashlib
import json
import json as json_module

import flowgency.jobs.submission as submission_module
import pytest
from dataclasses import replace

from flowgency.jobs.authority import JobStore
from flowgency.jobs.store import read_job, write_job
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.tickets.models import TicketRef, TicketRecord
from flowgency.tickets.storages.local import LocalTicketStorage
from flowgency.integrations.models import RuntimeCapabilities
from flowgency.workflows.models import Precondition
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


def test_update_route_rerenders_html_with_submitted_draft_on_conflict(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="Draft conflict", values={"summary": "Server value", "verdict": True})
    stale_version = ticket.version.model_dump(mode="json")
    env.service.update(
        env.user,
        env.read(ticket.ref).version,
        env.read(ticket.ref).patch(description="Remote change"),
        env.operation("remote-change"),
    )

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
        data={
            "payload": json.dumps(
                {
                    "version": stale_version,
                    "operation_id": "stale-form",
                    "patch": {"description": "Local draft"},
                }
            )
        },
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert "Refresh the ticket" in response.text
    assert "Local draft" in response.text


def test_create_route_rerenders_html_with_submitted_draft_on_validation_error(workflow_web_env):
    env = workflow_web_env

    response = env.client.post(
        f"{env.base_path}/tickets",
        data={
            "payload": json.dumps(
                {
                    "operation_id": "bad-create",
                    "title": "Create draft",
                    "description": "Preserve this description",
                    "field_values": [],
                }
            )
        },
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]
    assert "Create draft" in response.text
    assert "Preserve this description" in response.text
    assert "Invalid value." in response.text or "Value must be an object." in response.text


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
    detail_page = env.client.get(create_response.headers["location"])
    assert detail_page.status_code == 200
    assert "text/html" in detail_page.headers["content-type"]

    snapshot = env.client.get(f"{create_response.headers['location']}/snapshot")
    payload = snapshot.json()
    assert payload["ticket"]["description"] == description
    assert payload["ticket"]["state_id"] == "review"
    assert "body_html" not in payload["ticket"]


def test_create_json_success_redirects_to_ticket_snapshot(workflow_web_env):
    env = workflow_web_env

    response = env.client.post(
        f"{env.base_path}/tickets",
        data={
            "payload": json.dumps(
                {
                    "operation_id": "create-json-redirect",
                    "title": "JSON create",
                    "description": "Redirect to the canonical snapshot",
                    "field_values": {"summary": "hello"},
                }
            )
        },
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/snapshot")


def test_ticket_detail_route_renders_html_page(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="HTML detail")

    response = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Assigned agent" in response.text
    assert "Overview" in response.text


def test_ticket_detail_snapshot_uses_deterministic_etag(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="ETag detail")

    response = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    expected = hashlib.sha256(
        json_module.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert response.headers["etag"] == f'W/"{expected}"'

    not_modified = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot",
        headers={"If-None-Match": response.headers["etag"]},
    )

    assert not_modified.status_code == 304


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


def test_update_json_success_redirects_to_ticket_snapshot(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="JSON update")

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
        data={
            "payload": json.dumps(
                {
                    "version": ticket.version.model_dump(mode="json"),
                    "operation_id": "json-update",
                    "patch": {"description": "Updated by JSON client"},
                }
            )
        },
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"


def test_assignee_json_success_redirects_to_ticket_snapshot(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="JSON assignee")

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/assignee",
        data={
            "payload": json.dumps(
                {
                    "version": ticket.version.model_dump(mode="json"),
                    "assignee": "builder",
                    "operation_id": "json-assignee",
                }
            )
        },
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"


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


def test_run_json_success_redirects_to_ticket_snapshot(workflow_web_env, monkeypatch):
    env = workflow_web_env
    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": TicketRuntimeIntegration()})
    ticket = env.create(title="JSON run")
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-builder"))
    current = env.read(ticket.ref)

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/run",
        data={
            "payload": json.dumps(
                {
                    "version": current.version.model_dump(mode="json"),
                    "operation_id": "json-run",
                }
            )
        },
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"


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


def test_detail_snapshot_exposes_current_definition_fields_and_retained_audit_snapshots(
    workflow_web_env,
):
    env = workflow_web_env
    env.publish_artifact_field_workflow()
    env.publish_criteria_workflow()
    ticket = env.create(values={"verdict": True, "summary": "Initial summary", "evidence": None})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    artifact = env.service.publish_artifact(
        actor,
        env.read(ticket.ref).version,
        "evidence.txt",
        "text/plain",
        b"artifact-bytes",
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(
            outputs={"summary": "Verified existing work", "evidence": artifact},
            assessments=(
                {
                    "criterion_id": "evidence-reviewed",
                    "satisfied": True,
                    "reasoning": "Checked the attached evidence.",
                    "supporting_fields": ("summary", "evidence"),
                },
            ),
        ),
        env.operation("complete", actor_name=actor.agent_name),
    )

    source = env.library.inspect(env.blueprint_id)
    fields = tuple(
        field.model_copy(update={"label": "Review result"})
        if field.id == "verdict"
        else field
        for field in source.definition.fields
    )
    transitions = tuple(
        transition.model_copy(
            update={
                "criteria": tuple(
                    criterion.model_copy(update={"description": "Evidence was checked"})
                    if criterion.id == "evidence-reviewed"
                    else criterion
                    for criterion in transition.criteria
                )
            }
        )
        if transition.id == "complete"
        else transition
        for transition in source.definition.transitions
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(update={"fields": fields, "transitions": transitions}),
    )

    response = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket"]["state_id"] == "done"
    assert payload["ticket"]["active_run_job_id"] == "run-a"
    assert payload["fields"] == [
        {
            "id": "verdict",
            "label": "Review result",
            "type": "boolean",
            "value": True,
            "provenance": {
                "actor_kind": "user",
                "actor_name": "local-user",
                "job_id": None,
                "event_id": payload["fields"][0]["provenance"]["event_id"],
                "recorded_at": payload["fields"][0]["provenance"]["recorded_at"],
            },
        },
        {
            "id": "summary",
            "label": "Review summary",
            "type": "text",
            "value": "Verified existing work",
            "provenance": {
                "actor_kind": "agent",
                "actor_name": "builder",
                "job_id": "run-a",
                "event_id": payload["fields"][1]["provenance"]["event_id"],
                "recorded_at": payload["fields"][1]["provenance"]["recorded_at"],
            },
        },
        {
            "id": "evidence",
            "label": "Evidence",
            "type": "artifact",
            "value": {"kind": "id", "value": artifact.value},
            "provenance": {
                "actor_kind": "agent",
                "actor_name": "builder",
                "job_id": "run-a",
                "event_id": payload["fields"][2]["provenance"]["event_id"],
                "recorded_at": payload["fields"][2]["provenance"]["recorded_at"],
            },
        },
    ]
    assert payload["current_definition"]["fields"][0]["label"] == "Review result"
    assert payload["current_definition"]["transitions"][0]["criteria"][0]["description"] == "Evidence was checked"
    assert payload["history"][-1]["data"]["transition_snapshot"]["field_defs"]["verdict"]["label"] == "Review verdict"
    assert payload["history"][-1]["data"]["transition_snapshot"]["criteria"][0]["description"] == "Evidence was reviewed"
    assert payload["history"][-1]["data"]["effective_outputs"]["evidence"] == {
        "kind": "id",
        "value": artifact.value,
    }
    serialized = json.dumps(payload)
    assert str(env.root_a) not in serialized
    assert "receipts" not in serialized


def test_detail_snapshot_exposes_current_preconditions_without_overwriting_history(
    workflow_web_env,
):
    env = workflow_web_env
    ticket = env.create(values={"verdict": True, "summary": "Initial summary"})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Verified existing work"}),
        env.operation("complete", actor_name=actor.agent_name),
    )

    source = env.library.inspect(env.blueprint_id)
    fields = source.definition.fields + (
        source.definition.field("summary").model_copy(
            update={"id": "attempts", "label": "Attempt count", "type": "number"}
        ),
    )
    transitions = tuple(
        transition.model_copy(
            update={
                "preconditions": (
                    Precondition(
                        field_id="verdict",
                        operator="not_equals",
                        value=False,
                    ),
                    Precondition(
                        field_id="attempts",
                        operator="equals",
                        value=0,
                    ),
                )
            }
        )
        if transition.id == "complete"
        else transition
        for transition in source.definition.transitions
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(update={"fields": fields, "transitions": transitions}),
    )

    response = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_definition"]["transitions"][0]["preconditions"] == [
        {
            "field_id": "verdict",
            "field_label": "Review verdict",
            "field_type": "boolean",
            "operator": "not_equals",
            "comparison": False,
        },
        {
            "field_id": "attempts",
            "field_label": "Attempt count",
            "field_type": "number",
            "operator": "equals",
            "comparison": 0,
        },
    ]
    assert payload["history"][-1]["data"]["transition_snapshot"]["preconditions"] == [
        {
            "field_id": "verdict",
            "operator": "equals",
            "value": True,
        }
    ]
    serialized = json.dumps(payload)
    assert str(env.root_a) not in serialized
    assert "receipts" not in serialized


def test_artifact_download_uses_current_namespace_without_live_ticket_reference(
    workflow_web_env,
):
    env = workflow_web_env
    ticket = env.create(values={"summary": "hello", "verdict": True})
    retained = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"retained evidence",
    )
    replacement = env.service.publish_artifact(
        env.user,
        env.read(ticket.ref).version,
        "replacement.txt",
        "text/plain",
        b"replacement evidence",
    )
    env.publish_artifact_field_workflow()
    env.service.update(
        env.user,
        env.read(ticket.ref).version,
        env.read(ticket.ref).patch(field_values={"evidence": replacement}),
        env.operation("replace-live-artifact"),
    )

    response = env.client.get(f"{env.base_path}/artifacts/{retained.value}")

    assert response.status_code == 200
    assert response.content == b"retained evidence"
    assert response.headers["content-disposition"] == 'attachment; filename="review.txt"'
    assert response.headers["x-content-type-options"] == "nosniff"


def test_artifact_download_reads_retained_namespace_bytes_for_empty_board(workflow_web_env):
    env = workflow_web_env
    artifact = RetainedArtifact.create("review.txt", "text/plain", b"orphaned evidence")
    namespace = TicketRef.from_binding(env.binding, "artifact-namespace")
    env.current_provider().put_artifact(namespace, artifact)

    response = env.client.get(f"{env.base_path}/artifacts/{artifact.digest}")

    assert response.status_code == 200
    assert response.content == b"orphaned evidence"


def test_detail_snapshot_reports_retryable_reservation_without_fabricating_queued_job(
    workflow_web_env,
    monkeypatch,
):
    import flowgency.jobs.submission as submission_module

    env = workflow_web_env
    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": TicketRuntimeIntegration()})
    monkeypatch.setattr(
        submission_module,
        "_submit_resolved",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")),
    )
    ticket = env.create(title="Retryable queue")
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-builder"))
    current = env.read(ticket.ref)

    with pytest.raises(RuntimeError, match="submit failed"):
        env.client.post(
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

    detail = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")

    assert detail.status_code == 200
    payload = detail.json()["ticket"]
    assert payload["pending_run_job_id"] is None
    assert payload["pending_run_status"] is None
    assert payload["pending_run_issue"]["code"] == "pending-run-retryable"


def test_detail_snapshot_reports_verified_durable_queued_job(workflow_web_env, monkeypatch):
    import flowgency.jobs.submission as submission_module

    env = workflow_web_env
    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": TicketRuntimeIntegration()})
    ticket = env.create(title="Queued once")
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

    detail = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")

    assert response.status_code == 303
    payload = detail.json()["ticket"]
    assert payload["pending_run_job_id"] is not None
    assert payload["pending_run_status"] == "queued"
    assert payload["pending_run_issue"] is None


def test_detail_snapshot_hides_terminal_or_stale_pending_jobs(workflow_web_env, monkeypatch):
    import flowgency.jobs.submission as submission_module

    env = workflow_web_env
    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": TicketRuntimeIntegration()})
    ticket = env.create(title="Queued once")
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-builder"))
    current = env.read(ticket.ref)

    env.client.post(
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
    queued = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot").json()["ticket"]
    job_path = env.job_store.path(env.team_id, queued["pending_run_job_id"])
    write_job(
        job_path,
        replace(
            read_job(job_path),
            status="complete",
            completed_at="2026-09-08T00:05:00+00:00",
        ),
    )

    terminal = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")
    env.service.assign(env.user, env.read(ticket.ref).version, "observer", env.operation("assign-observer"))
    stale = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot")

    terminal_payload = terminal.json()["ticket"]
    assert terminal_payload["pending_run_job_id"] is None
    assert terminal_payload["pending_run_status"] is None
    assert terminal_payload["pending_run_issue"]["code"] == "pending-run-terminal"
    stale_payload = stale.json()["ticket"]
    assert stale_payload["pending_run_job_id"] is None
    assert stale_payload["pending_run_status"] is None
    assert stale_payload["pending_run_issue"]["code"] == "pending-run-stale"