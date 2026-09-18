"""Trusted Git evidence: the retained manifest and the event that trusts it.

A Git-evidence field value is never trusted because of its media type, its
extension, its JSON shape, or its field name. It is trusted because this
ticket's own history contains a ``git-evidence-captured`` event – written only
by the capture service – whose receipt names exactly this artifact and repeats
the identity the manifest claims. Everything here is pure: it parses, hashes,
and compares, and never opens a repository, runs Git, or contacts a network.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from flowgency.git_evidence.capture import MAX_COMMITS, MAX_FILES
from flowgency.git_evidence.git import MAX_PATCH_BYTES
from flowgency.git_evidence.models import (
    GIT_EVIDENCE_MESSAGES,
    GitChangeStatus,
    GitFileChange,
    GitPublicationPolicy,
    GitPublicationReceipt,
    GitRangeCapture,
    git_policy_digest,
    validate_exact_ref,
)
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.tickets.errors import (
    TicketConflict,
    TicketEvidenceInvalid,
    TicketForbidden,
    TicketStorageError,
    TicketTooLarge,
)
from flowgency.tickets.models import (
    AgentTicketContext,
    TicketMutationResult,
    TicketRecord,
    TicketVersion,
)
from flowgency.workflows.models import ArtifactRef

GIT_EVIDENCE_EVENT_KIND = "git-evidence-captured"
GIT_EVIDENCE_MEDIA_TYPE = "application/vnd.flowgency.git-change+json"
GIT_EVIDENCE_FILENAME = "committed-changes.json"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_MODE_RE = re.compile(r"^[0-7]{6}$")


def evidence_error(error: type[TicketStorageError], code: str) -> TicketStorageError:
    """Build a ticket error carrying one fixed public evidence message."""
    return error(code, GIT_EVIDENCE_MESSAGES[code])


def workspace_key(path: Path | str) -> str:
    """Platform-normalized absolute spelling of a configured workspace."""
    resolved = str(Path(path).resolve(strict=False))
    return os.path.normcase(resolved) if os.name == "nt" else resolved


def workspace_identity(path: Path | str) -> str:
    """Hash of the configured workspace path; the raw path is never exposed."""
    return hashlib.sha256(workspace_key(path).encode("utf-8")).hexdigest()


def _require_digest(value: str, label: str) -> str:
    if not _DIGEST_RE.match(value):
        raise ValueError(f"{label} must be a SHA-256 digest")
    return value


def _require_object_id(value: str, label: str) -> str:
    if not _OBJECT_ID_RE.match(value):
        raise ValueError(f"{label} must be a full lowercase Git object ID")
    return value


def _require_aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


class GitCaptureRequest(BaseModel):
    """What an agent asks to be captured: a range, and where it belongs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    transition_id: StrictStr
    field_id: StrictStr
    base_commit: StrictStr
    end_commit: StrictStr
    publication_ref: StrictStr | None = None

    @model_validator(mode="after")
    def _validate_request(self) -> "GitCaptureRequest":
        if not self.transition_id or not self.field_id:
            raise ValueError("Capture request must name a transition and a field")
        _require_object_id(self.base_commit, "base_commit")
        _require_object_id(self.end_commit, "end_commit")
        if len(self.base_commit) != len(self.end_commit):
            raise ValueError("Both commits must use one repository's object format")
        if self.publication_ref is not None:
            validate_exact_ref(self.publication_ref)
        return self


class GitFileEntry(BaseModel):
    """One changed path, as the retained manifest records it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: StrictStr
    old_path: StrictStr | None = None
    status: GitChangeStatus
    lines_added: StrictInt
    lines_removed: StrictInt
    binary: StrictBool
    submodule: StrictBool
    old_mode: StrictStr
    new_mode: StrictStr
    old_object_id: StrictStr
    new_object_id: StrictStr

    @model_validator(mode="after")
    def _validate_entry(self) -> "GitFileEntry":
        if not self.path:
            raise ValueError("A changed path must not be empty")
        if self.lines_added < 0 or self.lines_removed < 0:
            raise ValueError("Line counts must not be negative")
        for mode in (self.old_mode, self.new_mode):
            if not _MODE_RE.match(mode):
                raise ValueError("File modes must be six octal digits")
        _require_object_id(self.old_object_id, "old_object_id")
        _require_object_id(self.new_object_id, "new_object_id")
        return self

    @classmethod
    def from_change(cls, change: GitFileChange) -> "GitFileEntry":
        return cls(
            path=change.path,
            old_path=change.old_path,
            status=change.status,
            lines_added=change.lines_added,
            lines_removed=change.lines_removed,
            binary=change.binary,
            submodule=change.submodule,
            old_mode=change.old_mode,
            new_mode=change.new_mode,
            old_object_id=change.old_object_id,
            new_object_id=change.new_object_id,
        )


class GitEvidenceManifest(BaseModel):
    """The immutable retained artifact: the patch plus who captured it, where."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    repository_id: StrictStr
    workspace_identity: StrictStr
    base_commit: StrictStr
    end_commit: StrictStr
    commit_ids: tuple[StrictStr, ...]
    files: tuple[GitFileEntry, ...]
    patch_b64: StrictStr
    patch_sha256: StrictStr
    team_id: StrictStr
    workflow_id: StrictStr
    ticket_id: StrictStr
    binding_id: StrictStr
    agent_name: StrictStr
    job_id: StrictStr
    captured_at: datetime
    policy_digest: StrictStr
    policy_snapshot: GitPublicationPolicy
    publication: GitPublicationReceipt

    @model_validator(mode="after")
    def _validate_manifest(self) -> "GitEvidenceManifest":
        _require_digest(self.repository_id, "repository_id")
        _require_digest(self.workspace_identity, "workspace_identity")
        _require_digest(self.patch_sha256, "patch_sha256")
        _require_digest(self.policy_digest, "policy_digest")
        _require_digest(self.binding_id, "binding_id")
        _require_object_id(self.base_commit, "base_commit")
        _require_object_id(self.end_commit, "end_commit")
        if len(self.base_commit) != len(self.end_commit):
            raise ValueError("Both commits must use one repository's object format")
        if len(self.commit_ids) > MAX_COMMITS:
            raise ValueError("The manifest records more commits than capture allows")
        if len(set(self.commit_ids)) != len(self.commit_ids):
            raise ValueError("The manifest must not repeat a commit")
        for commit in self.commit_ids:
            _require_object_id(commit, "commit_ids")
            if len(commit) != len(self.end_commit):
                raise ValueError("Every commit must use one repository's object format")
        if len(self.files) > MAX_FILES:
            raise ValueError("The manifest records more paths than capture allows")
        if not self.team_id or not self.workflow_id or not self.ticket_id:
            raise ValueError("The manifest must be scoped to one ticket")
        if not self.agent_name or not self.job_id:
            raise ValueError("The manifest must name its producing agent and job")
        _require_aware(self.captured_at, "captured_at")
        _require_aware(self.publication.verified_at, "publication.verified_at")
        if self.policy_digest != git_policy_digest(self.policy_snapshot):
            raise ValueError("policy_digest does not match the recorded policy")
        if self.publication.policy_digest != self.policy_digest:
            raise ValueError("The publication receipt records a different policy")
        _require_object_id(
            self.publication.observed_commit, "publication.observed_commit"
        )
        if self.publication.ref_object_id is not None:
            _require_object_id(
                self.publication.ref_object_id, "publication.ref_object_id"
            )
        if self.publication.publication_ref is not None:
            validate_exact_ref(self.publication.publication_ref)
        patch = self.patch
        if len(patch) > MAX_PATCH_BYTES:
            raise ValueError("The recorded patch exceeds the capture limit")
        if hashlib.sha256(patch).hexdigest() != self.patch_sha256:
            raise ValueError("patch_sha256 does not match the recorded patch")
        return self

    @property
    def patch(self) -> bytes:
        try:
            return base64.b64decode(self.patch_b64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("The recorded patch is not strict base64") from error

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")


class GitCaptureReceipt(BaseModel):
    """The trusted event payload: identity and checks only, never content.

    ``transition_id`` and ``field_id`` record what the capture was intended
    for. They are provenance, not a restriction: verified evidence may be
    written to any declared Git-change field of the same ticket, by any later
    transition or job, while identity, integrity, workspace and policy checks
    stay in force.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: StrictStr
    repository_id: StrictStr
    workspace_identity: StrictStr
    policy_digest: StrictStr
    workflow_digest: StrictStr
    context_digest: StrictStr
    transition_id: StrictStr
    field_id: StrictStr
    agent_name: StrictStr
    job_id: StrictStr

    @model_validator(mode="after")
    def _validate_receipt(self) -> "GitCaptureReceipt":
        for label in (
            "artifact_id",
            "repository_id",
            "workspace_identity",
            "policy_digest",
            "workflow_digest",
            "context_digest",
        ):
            _require_digest(getattr(self, label), label)
        if not self.transition_id or not self.field_id:
            raise ValueError("A capture receipt must name its transition and field")
        if not self.agent_name or not self.job_id:
            raise ValueError("A capture receipt must name its agent and job")
        return self


class GitCaptureResult(BaseModel):
    """What a capture returns: the artifact, the ticket version, the mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: ArtifactRef
    version: TicketVersion
    mutation: TicketMutationResult


def retained_git_artifact(
    captured: GitRangeCapture,
    receipt: GitPublicationReceipt,
    *,
    actor: AgentTicketContext,
    version: TicketVersion,
    request: GitCaptureRequest,
    policy: GitPublicationPolicy,
    captured_at: datetime,
    workspace: Path,
) -> RetainedArtifact:
    """Encode one bounded read as the immutable retained evidence artifact."""
    if captured.end_commit != request.end_commit or (
        captured.base_commit != request.base_commit
    ):
        raise evidence_error(TicketEvidenceInvalid, "git-evidence-manifest-invalid")
    manifest = GitEvidenceManifest(
        schema_version=1,
        repository_id=captured.repository_id,
        workspace_identity=workspace_identity(workspace),
        base_commit=captured.base_commit,
        end_commit=captured.end_commit,
        commit_ids=captured.commit_ids,
        files=tuple(GitFileEntry.from_change(change) for change in captured.files),
        patch_b64=base64.b64encode(captured.patch).decode("ascii"),
        patch_sha256=hashlib.sha256(captured.patch).hexdigest(),
        team_id=version.ref.team_id,
        workflow_id=version.ref.workflow_id,
        ticket_id=version.ref.ticket_id,
        binding_id=version.ref.binding_id,
        agent_name=actor.agent_name,
        job_id=actor.job_id,
        captured_at=captured_at,
        policy_digest=git_policy_digest(policy),
        policy_snapshot=policy,
        publication=receipt,
    )
    try:
        return RetainedArtifact.create(
            GIT_EVIDENCE_FILENAME, GIT_EVIDENCE_MEDIA_TYPE, manifest.to_bytes()
        )
    except ValueError as error:
        raise evidence_error(TicketTooLarge, "git-evidence-manifest-too-large") from error


def _parse_manifest(artifact: RetainedArtifact) -> GitEvidenceManifest:
    if artifact.media_type != GIT_EVIDENCE_MEDIA_TYPE:
        raise evidence_error(TicketEvidenceInvalid, "git-evidence-untrusted")
    try:
        return GitEvidenceManifest.model_validate_json(artifact.content)
    except ValidationError as error:
        raise evidence_error(
            TicketEvidenceInvalid, "git-evidence-manifest-invalid"
        ) from error


def _capture_receipts(
    record: TicketRecord, artifact_id: str
) -> list[GitCaptureReceipt]:
    receipts: list[GitCaptureReceipt] = []
    for event in record.events:
        if event.kind != GIT_EVIDENCE_EVENT_KIND:
            continue
        payload: Any = event.data.get("capture")
        if not isinstance(payload, dict) or payload.get("artifact_id") != artifact_id:
            continue
        try:
            receipts.append(GitCaptureReceipt.model_validate(payload))
        except ValidationError as error:
            raise evidence_error(
                TicketEvidenceInvalid, "git-evidence-untrusted"
            ) from error
    return receipts


def captured_artifact_ids(record: TicketRecord) -> frozenset[str]:
    """Artifact IDs this ticket's own capture events vouch for.

    A cheap prefilter for readers: an artifact this ticket never captured can
    never become trusted evidence, so it is never read or verified at all.
    """
    ids: set[str] = set()
    for event in record.events:
        if event.kind != GIT_EVIDENCE_EVENT_KIND or not isinstance(event.data, dict):
            continue
        payload: Any = event.data.get("capture")
        if not isinstance(payload, dict):
            continue
        artifact_id = payload.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            ids.add(artifact_id)
    return frozenset(ids)


def validate_git_artifact(
    record: TicketRecord,
    artifact: RetainedArtifact,
    *,
    workspace: Path | None = None,
    policy: GitPublicationPolicy | None = None,
) -> GitEvidenceManifest:
    """Prove this ticket itself captured this artifact, and that it still fits.

    The trusted-capture binding is always checked. A supplied workspace and
    policy additionally demand today's configuration, which historical
    read-only display deliberately omits.
    """
    manifest = _parse_manifest(artifact)
    receipts = _capture_receipts(record, artifact.digest)
    if not receipts:
        raise evidence_error(TicketEvidenceInvalid, "git-evidence-untrusted")
    if record.ref is not None and (
        manifest.team_id != record.ref.team_id
        or manifest.workflow_id != record.ref.workflow_id
        or manifest.binding_id != record.ref.binding_id
        or manifest.ticket_id != record.ref.ticket_id
    ):
        raise evidence_error(TicketForbidden, "git-evidence-cross-ticket")
    if manifest.ticket_id != record.id:
        raise evidence_error(TicketForbidden, "git-evidence-cross-ticket")
    if not any(
        receipt.repository_id == manifest.repository_id
        and receipt.workspace_identity == manifest.workspace_identity
        and receipt.policy_digest == manifest.policy_digest
        and receipt.agent_name == manifest.agent_name
        and receipt.job_id == manifest.job_id
        for receipt in receipts
    ):
        raise evidence_error(TicketEvidenceInvalid, "git-evidence-untrusted")
    if workspace is not None and manifest.workspace_identity != workspace_identity(
        workspace
    ):
        raise evidence_error(TicketConflict, "git-evidence-workspace-changed")
    if policy is not None and manifest.policy_digest != git_policy_digest(policy):
        raise evidence_error(TicketConflict, "git-evidence-policy-changed")
    return manifest
