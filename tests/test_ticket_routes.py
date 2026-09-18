from __future__ import annotations

import base64
import hashlib
import json
import json as json_module

import flowgency.jobs.submission as submission_module
import pytest
from dataclasses import replace

from flowgency.jobs.authority import JobStore
from flowgency.jobs.store import read_job, write_job
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.tickets.models import TicketEvent, TicketRef, TicketRecord
from flowgency.tickets.storages.local import LocalTicketStorage
from flowgency.integrations.models import RuntimeCapabilities
from flowgency.workflows.models import FieldUse, Precondition
from tests._git_evidence_helpers import requires_git
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


def test_create_route_accepts_progressive_form_fields_without_json_payload(workflow_web_env):
    env = workflow_web_env

    response = env.client.post(
        f"{env.base_path}/tickets",
        data={
            "operation_id": "create-form-post",
            "title": "Create from HTML form",
            "description": "Use the strict create route without client-side JSON.",
        },
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    created = env.client.get(f"{response.headers['location']}/snapshot")
    assert created.status_code == 200
    payload = created.json()
    assert payload["ticket"]["title"] == "Create from HTML form"
    assert payload["ticket"]["description"] == "Use the strict create route without client-side JSON."


def test_update_route_accepts_structured_form_fields_without_json_payload(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="HTML form update", values={"summary": "Server value", "verdict": True})

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
        data={
            "version.ref.binding_id": ticket.version.ref.binding_id,
            "version.ref.team_id": ticket.version.ref.team_id,
            "version.ref.workflow_id": ticket.version.ref.workflow_id,
            "version.ref.ticket_id": ticket.version.ref.ticket_id,
            "version.revision": str(ticket.version.revision),
            "version.workflow_digest": ticket.version.workflow_digest,
            "version.context_digest": ticket.version.context_digest,
            "operation_id": "structured-update",
            "patch.field_values.summary": "Updated from structured HTML",
            "patch.field_values.verdict": "false",
        },
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    refreshed = env.read(ticket.ref)
    assert refreshed.record.field_values["summary"] == "Updated from structured HTML"
    assert refreshed.record.field_values["verdict"] is False


def test_update_route_rejects_unknown_progressive_form_fields(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="Reject unknown field")

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
        data={
            "version.ref.binding_id": ticket.version.ref.binding_id,
            "version.ref.team_id": ticket.version.ref.team_id,
            "version.ref.workflow_id": ticket.version.ref.workflow_id,
            "version.ref.ticket_id": ticket.version.ref.ticket_id,
            "version.revision": str(ticket.version.revision),
            "version.workflow_digest": ticket.version.workflow_digest,
            "version.context_digest": ticket.version.context_digest,
            "operation_id": "structured-update-invalid",
            "patch.description": "Keep this draft visible",
            "patch.actor_name": "override-attempt",
        },
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Unexpected field." in response.text
    assert "Keep this draft visible" in response.text


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


def test_ticket_detail_route_offers_configured_team_agents(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="Detail agent options")

    response = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}")

    assert response.status_code == 200
    assert 'option value="builder"' in response.text
    assert 'option value="advisor"' not in response.text
    assert 'option value="researcher"' not in response.text


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
            "artifact_format": None,
            "value": True,
            "provenance": {
                "actor_kind": "user",
                "actor_name": "local-user",
                "job_id": None,
                "event_id": payload["fields"][0]["provenance"]["event_id"],
                "recorded_at": payload["fields"][0]["provenance"]["recorded_at"],
            },
            "is_output": False,
            "git_evidence": None,
            "evidence_issue": None,
        },
        {
            "id": "summary",
            "label": "Review summary",
            "type": "text",
            "artifact_format": None,
            "value": "Verified existing work",
            "provenance": {
                "actor_kind": "agent",
                "actor_name": "builder",
                "job_id": "run-a",
                "event_id": payload["fields"][1]["provenance"]["event_id"],
                "recorded_at": payload["fields"][1]["provenance"]["recorded_at"],
            },
            "is_output": True,
            "git_evidence": None,
            "evidence_issue": None,
        },
        {
            "id": "evidence",
            "label": "Evidence",
            "type": "artifact",
            "artifact_format": None,
            "value": {"kind": "id", "value": artifact.value},
            "provenance": {
                "actor_kind": "agent",
                "actor_name": "builder",
                "job_id": "run-a",
                "event_id": payload["fields"][2]["provenance"]["event_id"],
                "recorded_at": payload["fields"][2]["provenance"]["recorded_at"],
            },
            "is_output": True,
            "git_evidence": None,
            "evidence_issue": None,
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
    assert set(payload["history"][-1]) == {
        "id",
        "kind",
        "actor",
        "summary",
        "at",
        "data",
        "git_outputs",
        "evidence_issues",
    }
    # An ordinary retained upload carries no verified Git evidence.
    assert payload["history"][-1]["git_outputs"] == {}
    assert payload["history"][-1]["evidence_issues"] == []
    serialized = json.dumps(payload)
    assert str(env.root_a) not in serialized
    assert "receipts" not in serialized


def test_compact_history_retains_originating_job_and_event_job_helper(workflow_web_env):
    from flowgency.tickets.views import build_board_view, event_job_id

    env = workflow_web_env
    actor = env.agent("builder", "activity-view-run")
    created = env.service.create(
        actor,
        env.workflow_id,
        "Compact history",
        "Body",
        {"summary": "ready"},
        env.operation("compact-create", actor_name=actor.agent_name),
    )
    board = build_board_view(env.service, env.user, env.workflow_id)
    ticket = next(
        ticket
        for column in board.columns
        for ticket in column.tickets
        if ticket.ref == created.ticket.ref
    )

    assert ticket.history[-1].job_id == actor.job_id
    assert event_job_id(TicketEvent(kind="reported", actor="builder", summary="old")) is None
    assert event_job_id(TicketEvent(kind="reported", actor="builder", summary="old", data={})) is None


@pytest.mark.parametrize(
    "job_id",
    [None, "", ["other-run"], {"job_id": "other-run"}, "../other-run"],
)
def test_event_job_helper_rejects_malformed_job_ids(job_id):
    from flowgency.tickets.views import event_job_id

    assert event_job_id(
        TicketEvent(kind="reported", actor="builder", summary="old", data={"job_id": job_id})
    ) is None


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
                "inputs": transition.inputs
                + (FieldUse(field_id="attempts", required=False),),
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


def test_terminal_ticket_keeps_saved_output_visible(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(values={"verdict": True, "summary": "Initial"})
    actor = env.agent("builder", "output-run")
    env.service.start_work(
        actor, ticket.version, env.operation("start", actor_name="builder")
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Retained result"}),
        env.operation("finish", actor_name="builder"),
    )
    path = f"{env.base_path}/tickets/{ticket.ref.ticket_id}"
    payload = env.client.get(f"{path}/snapshot").json()
    summary = next(field for field in payload["fields"] if field["id"] == "summary")
    assert payload["ticket"]["state_id"] == "done"
    assert summary["value"] == "Retained result"
    assert summary["is_output"] is True
    page = env.client.get(path)
    assert page.status_code == 200
    assert "<h3>Outputs</h3>" in page.text
    assert 'data-ticket-input="summary"' not in page.text
    assert "Retained result" in page.text


def test_output_projection_ignores_report_payloads(workflow_web_env):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import output_field_definitions

    env = workflow_web_env
    ticket = env.create(values={"verdict": True})
    view = env.read(ticket.ref)
    report = TicketEvent(
        kind="reported", actor="builder", summary="Not an accepted transition",
        data={"effective_outputs": {"invented-result": "do not project"}},
    )
    copied = view.model_copy(update={
        "record": view.record.model_copy(update={"events": view.record.events + (report,)})
    })
    assert "invented-result" not in output_field_definitions(copied)


@pytest.mark.parametrize(
    ("field_type", "current_value"),
    [("boolean", False), ("number", 0), ("text", None), ("text", "Newest")],
)
def test_retired_output_uses_saved_value_and_snapshot_label(
    workflow_web_env, field_type, current_value
):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create()
    view = env.read(ticket.ref)
    definition = {"id": "custom-result", "label": "Recorded result", "type": field_type}
    accepted = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={
            "effective_outputs": {"custom-result": "Historical payload only"},
            "transition_snapshot": {"field_defs": {"custom-result": definition}},
        },
    )
    record = view.record.model_copy(update={
        "field_values": {"custom-result": current_value}, "events": (accepted,)
    })
    rows = _field_rows(view.model_copy(update={"record": record, "definition": None}))
    assert len(rows) == 1
    assert rows[0].is_output is True
    assert rows[0].label == "Recorded result"
    assert rows[0].type == field_type
    assert rows[0].value == current_value
    assert rows[0].provenance is None


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        "not-a-mapping",
        ["also-not-a-mapping"],
        {"field_defs": None},
        {"field_defs": "not-a-mapping"},
        {"field_defs": ["also-not-a-mapping"]},
        {"field_defs": {"custom-result": {"id": "other-id", "label": "Mismatched", "type": "text"}}},
    ],
)
def test_output_projection_falls_back_to_id_on_malformed_snapshot_metadata(
    workflow_web_env, snapshot
):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create()
    view = env.read(ticket.ref)
    accepted = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={
            "effective_outputs": {"custom-result": "Historical payload only"},
            "transition_snapshot": snapshot,
        },
    )
    record = view.record.model_copy(update={
        "field_values": {"custom-result": "Newest"}, "events": (accepted,)
    })
    rows = _field_rows(view.model_copy(update={"record": record, "definition": None}))
    assert len(rows) == 1
    assert rows[0].is_output is True
    assert rows[0].label == "custom-result"
    assert rows[0].type is None
    assert rows[0].value == "Newest"


def _git_change_definition(env):
    """Make the shipped ``evidence`` artifact field require Git evidence."""
    from flowgency.workflows.models import WorkflowDefinition

    source = env.library.inspect(env.blueprint_id)
    document = source.definition.model_dump(mode="json")
    for field in document["fields"]:
        if field["id"] == "evidence":
            field["artifact_format"] = "git-change"
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        WorkflowDefinition.model_validate(document),
    )


def test_detail_snapshot_projects_current_git_change_format(workflow_web_env):
    env = workflow_web_env
    env.publish_artifact_field_workflow()
    _git_change_definition(env)
    ticket = env.create(values={"verdict": True, "summary": "Pending"})

    payload = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"
    ).json()

    rows = {row["id"]: row for row in payload["fields"]}
    assert rows["evidence"]["artifact_format"] == "git-change"
    # An ordinary field of another type carries no format at all.
    assert rows["summary"]["artifact_format"] is None
    definitions = {
        field["id"]: field for field in payload["current_definition"]["fields"]
    }
    assert definitions["evidence"]["artifact_format"] == "git-change"
    assert definitions["summary"]["artifact_format"] is None


def test_detail_snapshot_keeps_an_ordinary_artifact_field_formatless(workflow_web_env):
    env = workflow_web_env
    env.publish_artifact_field_workflow()
    ticket = env.create(values={"verdict": True, "summary": "Pending"})

    payload = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"
    ).json()

    rows = {row["id"]: row for row in payload["fields"]}
    assert rows["evidence"]["type"] == "artifact"
    assert rows["evidence"]["artifact_format"] is None
    definitions = {
        field["id"]: field for field in payload["current_definition"]["fields"]
    }
    assert definitions["evidence"]["artifact_format"] is None


def test_removed_git_change_output_keeps_its_historical_format(workflow_web_env):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create()
    view = env.read(ticket.ref)
    accepted = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={
            "effective_outputs": {"retired-evidence": {"kind": "id", "value": "a" * 64}},
            "transition_snapshot": {
                "field_defs": {
                    "retired-evidence": {
                        "id": "retired-evidence",
                        "label": "Retired evidence",
                        "type": "artifact",
                        "artifact_format": "git-change",
                    }
                }
            },
        },
    )
    record = view.record.model_copy(update={
        "field_values": {"retired-evidence": {"kind": "id", "value": "a" * 64}},
        "events": (accepted,),
    })

    rows = _field_rows(view.model_copy(update={"record": record, "definition": None}))

    assert len(rows) == 1
    assert rows[0].label == "Retired evidence"
    assert rows[0].type == "artifact"
    assert rows[0].artifact_format == "git-change"


@pytest.mark.parametrize(
    "artifact_format",
    ["svn-change", "", 7, {"mode": "git-change"}],
)
def test_invalid_historical_artifact_format_falls_back_to_plain_text(
    workflow_web_env, artifact_format
):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create()
    view = env.read(ticket.ref)
    accepted = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={
            "effective_outputs": {"retired-evidence": "Historical payload only"},
            "transition_snapshot": {
                "field_defs": {
                    "retired-evidence": {
                        "id": "retired-evidence",
                        "label": "Retired evidence",
                        "type": "artifact",
                        "artifact_format": artifact_format,
                    }
                }
            },
        },
    )
    record = view.record.model_copy(update={
        "field_values": {"retired-evidence": "Historical payload only"},
        "events": (accepted,),
    })

    rows = _field_rows(view.model_copy(update={"record": record, "definition": None}))

    assert len(rows) == 1
    assert rows[0].is_output is True
    # Unreadable metadata is not guessed at: the row degrades to escaped text.
    assert rows[0].label == "retired-evidence"
    assert rows[0].type is None
    assert rows[0].artifact_format is None
    assert rows[0].value == "Historical payload only"


@pytest.mark.parametrize("malformed_outputs", [None, "not-a-mapping", ["also-not-a-mapping"]])
def test_output_projection_ignores_malformed_effective_outputs(workflow_web_env, malformed_outputs):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import output_field_definitions

    env = workflow_web_env
    ticket = env.create(values={"verdict": True})
    view = env.read(ticket.ref)
    accepted = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={"effective_outputs": malformed_outputs},
    )
    copied = view.model_copy(update={
        "record": view.record.model_copy(update={"events": view.record.events + (accepted,)}),
        "definition": None,
    })
    assert output_field_definitions(copied) == {}


@pytest.mark.parametrize("malformed_outputs", [None, "not-a-mapping", ["also-not-a-mapping"]])
def test_history_panel_ignores_malformed_effective_outputs_payload(workflow_web_env, malformed_outputs):
    from flowgency.tickets.models import TicketEvent

    env = workflow_web_env
    ticket = env.create(values={"verdict": True})
    current = env.provider.read(ticket.ref)
    malformed = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={"effective_outputs": malformed_outputs},
    )
    env.provider.write_record(
        current.model_copy(update={"events": current.events + (malformed,)})
    )

    path = f"{env.base_path}/tickets/{ticket.ref.ticket_id}"
    page = env.client.get(path)
    snapshot = env.client.get(f"{path}/snapshot")

    assert page.status_code == 200
    assert snapshot.status_code == 200
    verdict = next(field for field in snapshot.json()["fields"] if field["id"] == "verdict")
    assert verdict["is_output"] is False


def test_output_projection_retains_classification_after_output_removed_from_transition(
    workflow_web_env,
):
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create(values={"verdict": True, "summary": "Initial"})
    actor = env.agent("builder", "remove-run")
    env.service.start_work(actor, ticket.version, env.operation("start", actor_name="builder"))
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Recorded"}),
        env.operation("finish", actor_name="builder"),
    )

    source = env.library.inspect(env.blueprint_id)
    transitions = tuple(
        transition.model_copy(update={"outputs": ()})
        if transition.id == "complete"
        else transition
        for transition in source.definition.transitions
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(update={"transitions": transitions}),
    )

    rows = {row.id: row for row in _field_rows(env.read(ticket.ref))}
    assert rows["summary"].is_output is True
    assert rows["summary"].label == "Review summary"
    assert rows["summary"].value == "Recorded"


def test_output_projection_keeps_classification_when_reused_as_input(workflow_web_env):
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create(values={"verdict": True, "summary": "Initial"})
    actor = env.agent("builder", "reuse-run")
    env.service.start_work(actor, ticket.version, env.operation("start", actor_name="builder"))
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Recorded"}),
        env.operation("finish", actor_name="builder"),
    )

    source = env.library.inspect(env.blueprint_id)
    reopen = source.definition.transitions[0].model_copy(
        update={
            "id": "reopen",
            "name": "Reopen",
            "from_state": "done",
            "to_state": "review",
            "inputs": (FieldUse(field_id="summary", required=False),),
            "outputs": (),
            "preconditions": (),
            "criteria": (),
        }
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(update={"transitions": source.definition.transitions + (reopen,)}),
    )

    rows = {row.id: row for row in _field_rows(env.read(ticket.ref))}
    assert rows["summary"].is_output is True


def test_http_repeat_transition_retains_each_accepted_output_in_its_own_history_event(
    workflow_web_env,
):
    env = workflow_web_env
    ticket = env.create(values={"verdict": True, "summary": "Initial"})
    actor = env.agent("builder", "repeat-run")

    source = env.library.inspect(env.blueprint_id)
    reopen = source.definition.transitions[0].model_copy(
        update={
            "id": "reopen",
            "name": "Reopen",
            "from_state": "done",
            "to_state": "review",
            "inputs": (),
            "outputs": (),
            "preconditions": (),
            "criteria": (),
        }
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(update={"transitions": source.definition.transitions + (reopen,)}),
    )

    env.service.start_work(
        actor, env.read(ticket.ref).version, env.operation("start", actor_name="builder")
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(transition_id="complete", outputs={"summary": "First result"}),
        env.operation("finish-a", actor_name="builder"),
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(transition_id="reopen"),
        env.operation("reopen", actor_name="builder"),
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(transition_id="complete", outputs={"summary": "Second result"}),
        env.operation("finish-b", actor_name="builder"),
    )

    path = f"{env.base_path}/tickets/{ticket.ref.ticket_id}"
    payload = env.client.get(f"{path}/snapshot").json()
    summary = next(field for field in payload["fields"] if field["id"] == "summary")
    assert payload["ticket"]["state_id"] == "done"
    assert summary["value"] == "Second result"
    assert summary["is_output"] is True

    accepted_events = [
        event
        for event in payload["history"]
        if event["kind"] == "transitioned" and event["data"].get("effective_outputs")
    ]
    assert len(accepted_events) == 2
    assert accepted_events[0]["id"] != accepted_events[1]["id"]
    assert accepted_events[0]["data"]["effective_outputs"]["summary"] == "First result"
    assert accepted_events[1]["data"]["effective_outputs"]["summary"] == "Second result"

    page = env.client.get(path)
    assert page.status_code == 200
    assert "Second result" in page.text


@requires_git
def test_git_diff_download_is_retained_not_recomputed(workflow_web_env):
    import base64
    import json
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.tickets.git_evidence import GitCaptureRequest
    from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

    env = workflow_web_env
    fixture = create_git_repository(env.tmp_path / "source")
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    captured = env.service.capture_git_evidence(
        actor, ticket.version,
        GitCaptureRequest(transition_id="complete", field_id="evidence", base_commit=fixture.base_commit, end_commit=fixture.end_commit),
        env.operation("capture", actor_name="builder"),
    )
    env.service.transition(
        actor, captured.version,
        env.transition_request(outputs={"summary": "Committed result", "evidence": captured.artifact}),
        env.operation("finish", actor_name="builder"),
    )
    retained = env.current_provider().read_artifact(ticket.ref, captured.artifact.value)
    expected_patch = base64.b64decode(json.loads(retained.content)["patch_b64"], validate=True)
    (fixture.root / "result.txt").write_bytes(b"unrelated later edit\n")
    base_url = f"{env.base_path}/tickets/{ticket.ref.ticket_id}/artifacts/{captured.artifact.value}"
    viewer = env.client.get(f"{base_url}/diff")
    download = env.client.get(f"{base_url}/patch")
    assert viewer.status_code == 200
    assert "after" in viewer.text
    assert "unrelated later edit" not in viewer.text
    assert download.status_code == 200
    assert download.content == expected_patch
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in download.headers["content-disposition"]


def _git_evidence_ticket(env, *, name="source", commit=None):
    """Start a real run on a ticket whose team points at a Git fixture."""
    from flowgency.git_evidence.models import GitPublicationPolicy
    from tests._git_evidence_helpers import (
        configure_git_ticket,
        create_git_repository,
        git_command,
    )

    fixture = create_git_repository(env.tmp_path / name)
    if commit is not None:
        filename, content = commit
        (fixture.root / filename).write_bytes(content)
        git_command(fixture.root, "add", "--", filename)
        git_command(fixture.root, "commit", "-m", "test: add reviewed content")
        fixture = replace(
            fixture,
            end_commit=git_command(fixture.root, "rev-parse", "HEAD").decode().strip(),
        )
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    return fixture, actor, ticket


def _capture_evidence(env, fixture, actor, ticket):
    from flowgency.tickets.git_evidence import GitCaptureRequest

    return env.service.capture_git_evidence(
        actor,
        env.read(ticket.ref).version,
        GitCaptureRequest(
            transition_id="complete",
            field_id="evidence",
            base_commit=fixture.base_commit,
            end_commit=fixture.end_commit,
        ),
        env.operation("capture", actor_name="builder"),
    )


def _accept_evidence(env, actor, captured):
    env.service.transition(
        actor,
        captured.version,
        env.transition_request(
            outputs={"summary": "Committed result", "evidence": captured.artifact}
        ),
        env.operation("finish", actor_name="builder"),
    )


def _evidence_base(env, ticket, captured):
    return (
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}"
        f"/artifacts/{captured.artifact.value}"
    )


def _retained_patch(env, ticket, captured):
    import base64

    retained = env.current_provider().read_artifact(ticket.ref, captured.artifact.value)
    return base64.b64decode(json_module.loads(retained.content)["patch_b64"], validate=True)


def _artifact_file(env, captured):
    return (
        env.root_a
        / env.team_id
        / env.workflow_id
        / "artifacts"
        / f"{captured.artifact.value}.json"
    )


@requires_git
def test_retained_evidence_outlives_refs_source_and_policy(workflow_web_env):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from tests._git_evidence_helpers import git_command

    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    expected_patch = _retained_patch(env, ticket, captured)

    git_command(fixture.root, "update-ref", "-d", "refs/heads/main")
    snapshot = env.store.load()
    env.store.patch(
        snapshot.revision,
        lambda raw: raw["teams"][env.team_id].__setitem__(
            "git_publication",
            GitPublicationPolicy(
                mode="local", allowed_refs=("refs/heads/release/*",)
            ).model_dump(mode="json"),
        ),
    )
    fixture.root.rename(fixture.root.with_name("source-moved-away"))

    base_url = _evidence_base(env, ticket, captured)
    viewer = env.client.get(f"{base_url}/diff")
    download = env.client.get(f"{base_url}/patch")
    assert viewer.status_code == 200
    assert "after" in viewer.text
    assert download.content == expected_patch


@requires_git
def test_damaged_evidence_storage_is_reported_not_regenerated(workflow_web_env):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    base_url = _evidence_base(env, ticket, captured)
    stored = _artifact_file(env, captured)
    original = stored.read_bytes()

    stored.write_bytes(b"not an artifact envelope")
    corrupt = env.client.get(f"{base_url}/diff")
    assert corrupt.status_code == 500

    envelope = json_module.loads(original)
    manifest = json_module.loads(base64.b64decode(envelope["content_b64"], validate=True))
    manifest["patch_sha256"] = "0" * 64
    envelope["content_b64"] = base64.b64encode(
        json_module.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    stored.write_bytes(json_module.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    tampered = env.client.get(f"{base_url}/patch")
    assert tampered.status_code == 409
    assert str(env.root_a) not in tampered.text

    stored.write_bytes(original)
    assert env.client.get(f"{base_url}/diff").status_code == 200


@requires_git
def test_an_untrusted_artifact_is_never_shown_as_git_evidence(workflow_web_env):
    from flowgency.tickets.git_evidence import GIT_EVIDENCE_MEDIA_TYPE, GIT_EVIDENCE_FILENAME

    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    stored = _artifact_file(env, captured)
    manifest_bytes = base64.b64decode(
        json_module.loads(stored.read_bytes())["content_b64"], validate=True
    )
    # The same bytes and the same media type, uploaded as an ordinary artifact.
    forged = RetainedArtifact.create(
        GIT_EVIDENCE_FILENAME, GIT_EVIDENCE_MEDIA_TYPE, manifest_bytes + b" "
    )
    env.current_provider().put_artifact(ticket.ref, forged)

    response = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/artifacts/{forged.digest}/diff"
    )
    assert response.status_code == 422


@requires_git
@pytest.mark.parametrize(
    "case, expected",
    [
        ("wrong-team", 404),
        ("wrong-workflow", 404),
        ("wrong-ticket", 404),
        ("traversal", 403),
        ("unknown-source", 422),
    ],
)
def test_git_evidence_viewer_refuses_foreign_or_unsafe_references(
    workflow_web_env, case, expected
):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    ticket_id = ticket.ref.ticket_id
    artifact_id = captured.artifact.value
    paths = {
        "wrong-team": f"/support/workflows/board-a/tickets/{ticket_id}/artifacts/{artifact_id}/diff",
        "wrong-workflow": f"/{env.team_id}/workflows/board-z/tickets/{ticket_id}/artifacts/{artifact_id}/diff",
        "wrong-ticket": f"{env.base_path}/tickets/ticket-absent/artifacts/{artifact_id}/diff",
        "traversal": f"{env.base_path}/tickets/{ticket_id}/artifacts/%2E%2E%5C%2E%2E%5Cconfig.yaml/diff",
        "unknown-source": f"{_evidence_base(env, ticket, captured)}/diff?source=https://evil.example",
    }

    response = env.client.get(paths[case])

    assert response.status_code == expected
    assert str(env.root_a) not in response.text


@requires_git
def test_a_linked_evidence_envelope_is_refused_rather_than_followed(workflow_web_env):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    stored = _artifact_file(env, captured)
    elsewhere = env.tmp_path / "elsewhere.json"
    elsewhere.write_bytes(stored.read_bytes())
    stored.unlink()
    try:
        stored.symlink_to(elsewhere)
    except (OSError, NotImplementedError):
        pytest.skip("creating a symlink requires privileges this host withholds")

    response = env.client.get(f"{_evidence_base(env, ticket, captured)}/diff")

    assert response.status_code == 403
    assert str(env.tmp_path) not in response.text


@requires_git
def test_git_evidence_page_escapes_committed_markup(workflow_web_env):
    env = workflow_web_env
    hostile = b'<script>alert("evidence")</script>\n<button onclick="steal()">x</button>\n'
    fixture, actor, ticket = _git_evidence_ticket(env, commit=("hostile.txt", hostile))
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)

    viewer = env.client.get(f"{_evidence_base(env, ticket, captured)}/diff")

    assert viewer.status_code == 200
    assert '<script>alert("evidence")</script>' not in viewer.text
    assert '<button onclick="steal()">' not in viewer.text
    assert "&lt;script&gt;alert(" in viewer.text
    assert "&lt;button onclick=" in viewer.text


@requires_git
def test_preview_budget_never_truncates_the_download(workflow_web_env):
    env = workflow_web_env
    wide = "".join(f"generated line {index}\n" for index in range(5000)).encode("utf-8")
    fixture, actor, ticket = _git_evidence_ticket(env, commit=("wide.txt", wide))
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    expected_patch = _retained_patch(env, ticket, captured)
    base_url = _evidence_base(env, ticket, captured)

    viewer = env.client.get(f"{base_url}/diff")
    download = env.client.get(f"{base_url}/patch")

    assert viewer.status_code == 200
    assert "not shown here" in viewer.text
    assert "generated line 4999" not in viewer.text
    assert download.content == expected_patch
    assert b"generated line 4999" in download.content


@requires_git
@pytest.mark.parametrize(
    "name, content",
    [
        ("blob.bin", b"\x00\x01\x02\xffbinary payload\n"),
        ("tail.txt", b"no trailing newline"),
    ],
)
def test_awkward_committed_content_downloads_byte_exactly(
    workflow_web_env, name, content
):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env, commit=(name, content))
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    expected_patch = _retained_patch(env, ticket, captured)
    base_url = _evidence_base(env, ticket, captured)

    viewer = env.client.get(f"{base_url}/diff")
    download = env.client.get(f"{base_url}/patch")

    assert viewer.status_code == 200
    assert download.content == expected_patch
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].endswith('.patch"')


@requires_git
def test_captured_evidence_is_viewable_before_it_is_ever_submitted(workflow_web_env):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)

    viewer = env.client.get(f"{_evidence_base(env, ticket, captured)}/diff")
    payload = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"
    ).json()

    assert viewer.status_code == 200
    rows = {row["id"]: row for row in payload["fields"]}
    assert rows["evidence"]["value"] is None
    assert rows["evidence"]["git_evidence"] is None
    assert all(
        not event["git_outputs"]
        for event in payload["history"]
    )


@requires_git
def test_verified_evidence_links_from_current_fields_and_history(workflow_web_env):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    base_url = _evidence_base(env, ticket, captured)

    payload = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"
    ).json()
    page = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}")

    rows = {row["id"]: row for row in payload["fields"]}
    assert rows["evidence"]["git_evidence"]["artifact_id"] == captured.artifact.value
    assert rows["evidence"]["git_evidence"]["end_commit"] == fixture.end_commit
    assert rows["evidence"]["git_evidence"]["job_id"] == "git-evidence-run"
    assert rows["evidence"]["git_evidence"]["publication_mode"] == "local"
    assert rows["evidence"]["evidence_issue"] is None
    accepted = [event for event in payload["history"] if event["git_outputs"]]
    assert len(accepted) == 1
    assert accepted[0]["git_outputs"]["evidence"]["artifact_id"] == captured.artifact.value
    # The audited payload itself is unchanged by the projection.
    assert accepted[0]["data"]["effective_outputs"]["evidence"] == {
        "kind": "id",
        "value": captured.artifact.value,
    }
    assert page.status_code == 200
    assert f"{base_url}/diff?source=ticket" in page.text
    assert f"{base_url}/patch" in page.text
    assert "Download retained file" not in page.text


def test_an_ordinary_artifact_keeps_its_plain_download_link(workflow_web_env):
    env = workflow_web_env
    env.publish_artifact_field_workflow()
    actor = env.agent("builder", "ordinary-run")
    ticket = env.create(values={"verdict": True, "summary": "Pending"})
    env.service.start_work(
        actor, ticket.version, env.operation("start", actor_name="builder")
    )
    published = env.service.publish_artifact(
        actor,
        env.read(ticket.ref).version,
        "review.txt",
        "text/plain",
        b"ordinary bytes",
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Done", "evidence": published}),
        env.operation("finish", actor_name="builder"),
    )

    page = env.client.get(f"{env.base_path}/tickets/{ticket.ref.ticket_id}")
    payload = env.client.get(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"
    ).json()

    rows = {row["id"]: row for row in payload["fields"]}
    assert rows["evidence"]["git_evidence"] is None
    assert rows["evidence"]["evidence_issue"] is None
    assert "Download retained file" in page.text
    assert "View diff" not in page.text


@requires_git
def test_job_navigation_falls_back_to_the_ticket_when_the_job_is_gone(workflow_web_env):
    env = workflow_web_env
    fixture, actor, ticket = _git_evidence_ticket(env)
    captured = _capture_evidence(env, fixture, actor, ticket)
    _accept_evidence(env, actor, captured)
    base_url = _evidence_base(env, ticket, captured)

    from_job = env.client.get(f"{base_url}/diff?source=job")
    assert from_job.status_code == 200
    assert f"/{env.team_id}/jobs/git-evidence-run" in from_job.text

    env.job_store.path(env.team_id, "git-evidence-run").unlink()
    without_job = env.client.get(f"{base_url}/diff?source=job")

    assert without_job.status_code == 200
    assert f"/{env.team_id}/jobs/git-evidence-run" not in without_job.text
    assert f"{env.base_path}/tickets/{ticket.ref.ticket_id}" in without_job.text
    assert "after" in without_job.text
