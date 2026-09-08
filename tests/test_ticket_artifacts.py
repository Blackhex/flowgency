from __future__ import annotations

import json

import pytest

from flowgency.tickets.artifacts import MAX_RETAINED_ARTIFACT_BYTES, RetainedArtifact
from flowgency.tickets.errors import (
    TicketConflict,
    TicketCorrupt,
    TicketForbidden,
    TicketNotFound,
    TicketTooLarge,
)
from flowgency.tickets.models import TicketRef
from flowgency.tickets.storages import local
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.models import ArtifactRef, ContractError
from flowgency.jobs.artifacts import retain_failed_stage
from tests._ticket_helpers import storage_binding


def test_publish_artifact_returns_retained_reference_and_replays_same_content(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})

    first = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"verified evidence",
    )
    second = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"verified evidence",
    )

    assert first == ArtifactRef(kind="id", value=first.value)
    assert second == first
    retained = env.current_provider().read_artifact(ticket.ref, first.value)
    assert retained.filename == "review.txt"
    assert retained.media_type == "text/plain"
    assert retained.content == b"verified evidence"
    assert retained.digest == first.value


def test_transition_requires_existing_retained_artifact_and_matching_digest(workflow_env):
    env = workflow_env
    env.publish_artifact_field_workflow()
    ticket = env.create(values={"verdict": True, "evidence": None})
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

    accepted = env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Done", "evidence": artifact}),
        env.operation("with-artifact", actor_name=actor.agent_name),
    )
    assert accepted.ticket.field_values["evidence"] == artifact

    with pytest.raises(ContractError):
        env.service.transition(
            actor,
            env.read(ticket.ref).version,
            env.transition_request(
                outputs={
                    "summary": "Done",
                    "evidence": ArtifactRef(kind="id", value="artifact-missing"),
                },
            ),
            env.operation("missing-artifact", actor_name=actor.agent_name),
        )


def test_retained_artifact_rejects_cross_binding_and_tampering(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"verified evidence",
    )

    env.set_storage_root(env.root_b)
    with pytest.raises(TicketForbidden):
        env.current_provider().read_artifact(ticket.ref, artifact.value)

    env.set_storage_root(env.root_a)
    env.current_provider()._artifact_path(ticket.ref, artifact.value).write_bytes(
        RetainedArtifact.create("review.txt", "text/plain", b"tampered").to_storage_bytes()
    )
    with pytest.raises(TicketConflict):
        env.current_provider().read_artifact(ticket.ref, artifact.value)


def test_publish_artifact_sanitizes_display_filename_and_keeps_reference_exact(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})

    artifact = env.service.publish_artifact(
        env.user,
        ticket.version,
        "../review.txt",
        "text/plain",
        b"verified evidence",
    )
    retained = env.current_provider().read_artifact(ticket.ref, artifact.value)
    assert retained.filename == "review.txt"
    assert retained.digest == artifact.value
    assert retained.download_metadata() == {
        "content_disposition": "attachment",
        "content_length": len(b"verified evidence"),
        "filename": "review.txt",
        "media_type": "text/plain",
        "x_content_type_options": "nosniff",
    }


def test_publish_artifact_rejects_oversize_content(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})

    with pytest.raises(ValueError):
        env.service.publish_artifact(
            env.user,
            ticket.version,
            "review.txt",
            "text/plain",
            b"x" * (MAX_RETAINED_ARTIFACT_BYTES + 1),
        )


def test_agent_publish_artifact_rejects_stale_revision_without_writing_bytes(workflow_env):
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

    with pytest.raises(TicketConflict):
        env.service.publish_artifact(
            actor,
            stale_version,
            "review.txt",
            "text/plain",
            b"verified evidence",
        )

    current = env.read(ticket.ref)
    artifact = RetainedArtifact.create("review.txt", "text/plain", b"verified evidence")
    assert not env.current_provider()._artifact_path(current.ref, artifact.digest).exists()
    assert env.read(ticket.ref).record == current.record


def test_agent_publish_artifact_requires_active_owner_and_current_run(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    owner = env.agent("builder", "run-a")
    env.service.start_work(
        owner,
        ticket.version,
        env.operation("start", actor_name=owner.agent_name),
    )
    current = env.read(ticket.ref)

    with pytest.raises(TicketForbidden):
        env.service.publish_artifact(
            env.agent("observer", "run-b"),
            current.version,
            "review.txt",
            "text/plain",
            b"verified evidence",
        )

    with pytest.raises(TicketForbidden):
        env.service.publish_artifact(
            env.agent("builder", "run-b"),
            current.version,
            "review.txt",
            "text/plain",
            b"verified evidence",
        )


def test_publish_artifact_rejects_final_storage_drift_before_write(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    version = env.read(ticket.ref).version
    original_provider = env.current_provider()
    original_path = original_provider._artifact_path(
        ticket.ref,
        RetainedArtifact.create("review.txt", "text/plain", b"verified evidence").digest,
    )

    env.set_storage_root(env.root_b)
    drift_snapshot = env.store.load()
    env.set_storage_root(env.root_a)
    current_snapshot = env.store.load()
    calls = {"count": 0}

    def drifted_snapshot():
        calls["count"] += 1
        if calls["count"] == 1:
            return current_snapshot
        return drift_snapshot

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(env.service, "_load_current_snapshot", drifted_snapshot)
    with pytest.raises(TicketConflict):
        env.service.publish_artifact(
            actor,
            version,
            "review.txt",
            "text/plain",
            b"verified evidence",
        )
    monkeypatch.undo()

    assert not original_path.exists()


def test_read_artifact_rejects_tampered_metadata_without_normalizing(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = RetainedArtifact.create("review.txt", "text/plain", b"verified evidence")
    path = env.current_provider()._artifact_path(ticket.ref, artifact.digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(artifact.to_storage_bytes().decode("utf-8"))
    payload["filename"] = "../review.txt"
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(TicketCorrupt):
        env.current_provider().read_artifact(ticket.ref, artifact.digest)


def test_read_artifact_rejects_unknown_missing_or_wrong_typed_envelope_keys(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = RetainedArtifact.create("review.txt", "text/plain", b"verified evidence")
    path = env.current_provider()._artifact_path(ticket.ref, artifact.digest)
    path.parent.mkdir(parents=True, exist_ok=True)

    malformed_payloads = (
        {"filename": "review.txt", "media_type": "text/plain"},
        {"filename": "review.txt", "media_type": "text/plain", "content_b64": "dmVyaWZpZWQgZXZpZGVuY2U=", "extra": True},
        {"filename": 3, "media_type": "text/plain", "content_b64": "dmVyaWZpZWQgZXZpZGVuY2U="},
        {"filename": "review.txt", "media_type": ["text/plain"], "content_b64": "dmVyaWZpZWQgZXZpZGVuY2U="},
        {"filename": "review.txt", "media_type": "text/plain", "content_b64": 7},
    )

    for payload in malformed_payloads:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        with pytest.raises(TicketCorrupt):
            env.current_provider().read_artifact(ticket.ref, artifact.digest)


def test_read_artifact_rejects_invalid_base64_payload(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = RetainedArtifact.create("review.txt", "text/plain", b"verified evidence")
    path = env.current_provider()._artifact_path(ticket.ref, artifact.digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "filename": "review.txt",
                "media_type": "text/plain",
                "content_b64": "%%%not-base64%%%",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    with pytest.raises(TicketCorrupt):
        env.current_provider().read_artifact(ticket.ref, artifact.digest)


def test_read_artifact_rejects_oversized_envelope_before_decode(workflow_env, monkeypatch):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = RetainedArtifact.create("review.txt", "text/plain", b"verified evidence")
    path = env.current_provider()._artifact_path(ticket.ref, artifact.digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (local.MAX_RETAINED_ARTIFACT_ENVELOPE_BYTES + 1))

    def fail_parse(payload):
        raise AssertionError("parser should not run for oversized envelope")

    monkeypatch.setattr(local.RetainedArtifact, "from_storage_bytes", fail_parse)

    with pytest.raises(TicketTooLarge):
        env.current_provider().read_artifact(ticket.ref, artifact.digest)


def test_retained_artifact_rejects_cross_team_reference(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"verified evidence",
    )

    foreign_ref = TicketRef.from_binding(
        storage_binding(env.root_a, team_id="support", workflow_id="board-a"),
        ticket.ref.ticket_id,
    )
    foreign_provider = resolve_storage(
        storage_binding(env.root_a, team_id="support", workflow_id="board-a"),
        clock=env._clock,
    )
    with pytest.raises(TicketNotFound):
        foreign_provider.read_artifact(foreign_ref, artifact.value)


def test_job_artifact_cleanup_does_not_delete_retained_ticket_evidence(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    artifact = env.service.publish_artifact(
        env.user,
        ticket.version,
        "review.txt",
        "text/plain",
        b"verified evidence",
    )
    job_store = env.tmp_path / ".jobs" / env.team_id
    job_store.mkdir(parents=True)
    stage = env.tmp_path / "stage"
    stage.mkdir()
    (stage / "note.md").write_text("kept", encoding="utf-8")

    retain_failed_stage(
        job_store=job_store,
        job_id="run-a",
        stage_directory=stage,
        diff_bytes=None,
    )

    retained = env.current_provider().read_artifact(ticket.ref, artifact.value)
    assert retained.content == b"verified evidence"


def test_rejected_transition_leaves_retained_artifact_intact_and_ticket_unchanged(workflow_env):
    env = workflow_env
    env.publish_artifact_field_workflow()
    ticket = env.create(values={"verdict": False, "evidence": None})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    published = env.service.publish_artifact(
        actor,
        env.read(ticket.ref).version,
        "evidence.txt",
        "text/plain",
        b"artifact-bytes",
    )
    before = env.read(ticket.ref).record

    with pytest.raises(ContractError):
        env.service.transition(
            actor,
            env.read(ticket.ref).version,
            env.transition_request(outputs={"summary": "Done", "evidence": published}),
            env.operation("reject-artifact", actor_name=actor.agent_name),
        )

    assert env.read(ticket.ref).record == before
    retained = env.current_provider().read_artifact(ticket.ref, published.value)
    assert retained.content == b"artifact-bytes"
