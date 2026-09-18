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
from tests._git_evidence_helpers import requires_git
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


@requires_git
def test_git_capture_records_receipt_without_advancing_ticket(workflow_env):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.tickets.git_evidence import GitCaptureRequest
    from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

    env = workflow_env
    fixture = create_git_repository(env.tmp_path / "source")
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    before = env.read(ticket.ref).record
    request = GitCaptureRequest(
        transition_id="complete",
        field_id="evidence",
        base_commit=fixture.base_commit,
        end_commit=fixture.end_commit,
    )
    result = env.service.capture_git_evidence(
        actor, ticket.version, request, env.operation("capture", actor_name="builder")
    )
    after = env.read(ticket.ref).record
    assert after.state_id == before.state_id
    assert after.field_values == before.field_values
    assert after.active_run == before.active_run
    assert after.revision == before.revision + 1
    assert after.events[-1].kind == "git-evidence-captured"
    assert after.events[-1].data["capture"]["artifact_id"] == result.artifact.value
    assert result.version.revision == after.revision


def _git_capture_env(env, name: str = "source"):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from tests._git_evidence_helpers import (
        capture_request,
        configure_git_ticket,
        create_git_repository,
    )

    fixture = create_git_repository(env.tmp_path / name)
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    return actor, ticket, capture_request(fixture)


def _restrict_policy(env):
    from flowgency.git_evidence.models import GitPublicationPolicy

    snapshot = env.store.load()
    env.store.patch(
        snapshot.revision,
        lambda raw: raw["teams"][env.team_id].__setitem__(
            "git_publication",
            GitPublicationPolicy(
                mode="local", allowed_refs=("refs/heads/main",)
            ).model_dump(mode="json"),
        ),
    )


@requires_git
def test_git_capture_replay_never_reruns_git_even_after_a_policy_change(
    workflow_env, monkeypatch
):
    from flowgency.tickets import service as service_module
    from flowgency.tickets.errors import OperationConflict, TicketConflict
    from flowgency.tickets.models import TicketOperation

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)
    operation = env.operation("capture", actor_name="builder")
    first = env.service.capture_git_evidence(actor, ticket.version, request, operation)

    def refuse(*args, **kwargs):
        raise AssertionError("an accepted capture must never read Git again")

    monkeypatch.setattr(service_module, "capture_committed_range", refuse)

    replay = env.service.capture_git_evidence(
        actor, ticket.version, request, operation
    )
    assert replay.artifact == first.artifact
    assert replay.version == first.version
    assert replay.mutation.replayed is True
    assert replay.mutation.event_id == first.mutation.event_id
    assert env.read(ticket.ref).record.revision == first.version.revision

    _restrict_policy(env)
    again = env.service.capture_git_evidence(actor, ticket.version, request, operation)
    assert again.artifact == first.artifact
    assert again.mutation.replayed is True

    with pytest.raises(TicketConflict) as stale:
        env.service.transition(
            actor,
            env.read(ticket.ref).version,
            env.transition_request(
                outputs={"summary": "Stale", "evidence": first.artifact}
            ),
            env.operation("stale-evidence", actor_name="builder"),
        )
    assert stale.value.code == "git-evidence-policy-changed"

    with pytest.raises(OperationConflict):
        env.service.capture_git_evidence(
            actor,
            ticket.version,
            request,
            TicketOperation(
                operation_id=operation.operation_id, request_digest="c" * 64
            ),
        )


@requires_git
def test_git_capture_commits_nothing_when_the_ticket_or_session_moves(
    workflow_env, monkeypatch
):
    from flowgency.tickets import service as service_module
    from flowgency.tickets.access import TicketAccessRegistry
    from flowgency.tickets.errors import TicketConflict, TicketForbidden
    from flowgency.tickets.models import TicketPatch

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)
    before = env.read(ticket.ref).record
    original = service_module.capture_committed_range

    def interfere(*args, **kwargs):
        captured = original(*args, **kwargs)
        env.service.update(
            env.user,
            env.read(ticket.ref).version,
            TicketPatch(title="Renamed during the read"),
            env.operation("interfering-update"),
        )
        return captured

    monkeypatch.setattr(service_module, "capture_committed_range", interfere)
    with pytest.raises(TicketConflict):
        env.service.capture_git_evidence(
            actor,
            ticket.version,
            request,
            env.operation("racing-capture", actor_name="builder"),
        )

    raced = env.read(ticket.ref).record
    assert all(event.kind != "git-evidence-captured" for event in raced.events)
    assert raced.state_id == before.state_id
    assert raced.field_values == before.field_values

    def revoke(*args, **kwargs):
        captured = original(*args, **kwargs)
        TicketAccessRegistry(env.job_store).close(actor.session_id)
        return captured

    monkeypatch.setattr(service_module, "capture_committed_range", revoke)
    with pytest.raises(TicketForbidden):
        env.service.capture_git_evidence(
            actor,
            env.read(ticket.ref).version,
            request,
            env.operation("revoked-capture", actor_name="builder"),
        )
    revoked = env.read(ticket.ref).record
    assert all(event.kind != "git-evidence-captured" for event in revoked.events)


def _deny_workspace_reads(env):
    """Narrow the effective policy so the workspace is searchable, not readable."""
    workspace = str(env.store.load().config.teams[env.team_id].workspace_path)
    snapshot = env.store.load()
    env.store.patch(
        snapshot.revision,
        lambda raw: raw["teams"][env.team_id].__setitem__(
            "permissions",
            {
                "mode": "unrestricted",
                "rules": [{"tools": None}, {"path": workspace, "tools": ["search"]}],
            },
        ),
    )


@requires_git
def test_git_capture_rejects_reads_narrowed_while_the_capture_is_in_flight(
    workflow_env, monkeypatch
):
    from flowgency.tickets import service as service_module
    from flowgency.tickets.errors import TicketForbidden
    from tests._git_evidence_helpers import snapshot_repository

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)
    before = env.read(ticket.ref).record
    workspace = env.store.load().config.teams[env.team_id].workspace_path
    before_source = snapshot_repository(workspace)
    original = service_module.capture_committed_range

    def narrow(*args, **kwargs):
        captured = original(*args, **kwargs)
        _deny_workspace_reads(env)
        return captured

    monkeypatch.setattr(service_module, "capture_committed_range", narrow)
    operation = env.operation("narrowed-capture", actor_name="builder")
    with pytest.raises(TicketForbidden) as denied:
        env.service.capture_git_evidence(actor, ticket.version, request, operation)

    assert denied.value.code == "git-evidence-path-denied"
    assert env.read(ticket.ref).record == before
    assert env.current_provider().receipt(ticket.ref, operation) is None
    assert snapshot_repository(workspace) == before_source


@requires_git
def test_git_capture_rejects_a_policy_changed_while_the_capture_is_in_flight(
    workflow_env, monkeypatch
):
    from flowgency.tickets import service as service_module
    from flowgency.tickets.errors import TicketConflict

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)
    before = env.read(ticket.ref).record
    original = service_module.capture_committed_range

    def restrict(*args, **kwargs):
        captured = original(*args, **kwargs)
        _restrict_policy(env)
        return captured

    monkeypatch.setattr(service_module, "capture_committed_range", restrict)
    operation = env.operation("policy-raced-capture", actor_name="builder")
    with pytest.raises(TicketConflict):
        env.service.capture_git_evidence(actor, ticket.version, request, operation)

    assert env.read(ticket.ref).record == before
    assert env.current_provider().receipt(ticket.ref, operation) is None


@requires_git
def test_replayed_capture_separates_corrupt_receipts_from_programming_errors(
    workflow_env, monkeypatch
):
    from flowgency.tickets import git_evidence as git_evidence_module

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)
    operation = env.operation("capture", actor_name="builder")
    captured = env.service.capture_git_evidence(
        actor, ticket.version, request, operation
    )

    def defect(*args, **kwargs):
        raise RuntimeError("programming defect")

    monkeypatch.setattr(
        git_evidence_module.GitCaptureReceipt,
        "model_validate",
        staticmethod(defect),
    )
    with pytest.raises(RuntimeError):
        env.service.capture_git_evidence(actor, ticket.version, request, operation)
    monkeypatch.undo()

    path = env.current_provider()._ticket_path(ticket.ref)
    stored = path.read_text(encoding="utf-8")
    corrupted = stored.replace(
        f"artifact_id: {captured.artifact.value}", "artifact_id: not-a-digest"
    )
    assert corrupted != stored
    path.write_text(corrupted, encoding="utf-8")

    with pytest.raises(TicketCorrupt):
        env.service.capture_git_evidence(actor, ticket.version, request, operation)


@requires_git
def test_one_job_captures_each_ticket_against_its_own_identity(workflow_env):
    from flowgency.tickets.git_evidence import GitEvidenceManifest
    from tests._git_evidence_helpers import snapshot_repository

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)
    workspace = env.store.load().config.teams[env.team_id].workspace_path
    before_source = snapshot_repository(workspace)
    first = env.service.capture_git_evidence(
        actor, ticket.version, request, env.operation("capture-one", actor_name="builder")
    )

    second_ticket = env.create(title="Second", values={"verdict": True, "summary": "Two"})
    env.service.start_work(
        actor, second_ticket.version, env.operation("start-two", actor_name="builder")
    )
    second = env.service.capture_git_evidence(
        actor,
        env.read(second_ticket.ref).version,
        request,
        env.operation("capture-two", actor_name="builder"),
    )

    assert second.artifact != first.artifact
    assert snapshot_repository(workspace) == before_source
    provider = env.current_provider()
    for ref, result in ((ticket.ref, first), (second_ticket.ref, second)):
        manifest = GitEvidenceManifest.model_validate_json(
            provider.read_artifact(ref, result.artifact.value).content
        )
        assert manifest.ticket_id == ref.ticket_id
        record = env.read(ref).record
        assert record.events[-1].data["capture"]["artifact_id"] == result.artifact.value


@requires_git
def test_capture_is_unavailable_to_users_and_without_a_trusted_job_resolver(
    workflow_env,
):
    from flowgency.tickets.errors import TicketConflict, TicketForbidden

    env = workflow_env
    actor, ticket, request = _git_capture_env(env)

    with pytest.raises(TicketForbidden):
        env.service.capture_git_evidence(
            env.user, ticket.version, request, env.operation("user-capture")
        )

    env.service.resolve_git_job = None
    with pytest.raises(TicketConflict) as failure:
        env.service.capture_git_evidence(
            actor,
            ticket.version,
            request,
            env.operation("unresolved-capture", actor_name="builder"),
        )
    assert failure.value.code == "git-evidence-unavailable"
    assert all(
        event.kind != "git-evidence-captured"
        for event in env.read(ticket.ref).record.events
    )


def test_oversized_evidence_manifest_is_rejected_not_truncated(workflow_env):
    from datetime import datetime, timezone

    from flowgency.git_evidence.models import (
        GitFileChange,
        GitPublicationPolicy,
        GitPublicationReceipt,
        GitRangeCapture,
        git_policy_digest,
    )
    from flowgency.tickets.errors import TicketTooLarge
    from flowgency.tickets.git_evidence import GitCaptureRequest, retained_git_artifact
    from flowgency.tickets.models import AgentTicketContext

    env = workflow_env
    ticket = env.create(values={"verdict": True})
    policy = GitPublicationPolicy(mode="local")
    commit = "a" * 40
    captured = GitRangeCapture(
        repository_id="b" * 64,
        base_commit=commit,
        end_commit=commit,
        commit_ids=(),
        files=tuple(
            GitFileChange(
                path=f"{index:04d}/" + "p" * 150,
                old_path=None,
                status="modified",
                lines_added=1,
                lines_removed=0,
                binary=False,
                submodule=False,
                old_mode="100644",
                new_mode="100644",
                old_object_id="c" * 40,
                new_object_id="d" * 40,
            )
            for index in range(1024)
        ),
        patch=b"x" * (640 * 1024),
    )
    receipt = GitPublicationReceipt(
        mode="local",
        policy_digest=git_policy_digest(policy),
        publication_ref=None,
        ref_object_id=None,
        observed_commit=commit,
        verified_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )

    with pytest.raises(TicketTooLarge):
        retained_git_artifact(
            captured,
            receipt,
            actor=AgentTicketContext(
                job_id="run-a",
                team_id=env.team_id,
                agent_name="builder",
                session_id=f"{env.team_id}:run-a:abc",
            ),
            version=ticket.version,
            request=GitCaptureRequest(
                transition_id="complete",
                field_id="evidence",
                base_commit=commit,
                end_commit=commit,
            ),
            policy=policy,
            captured_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
            workspace=env.tmp_path,
        )
