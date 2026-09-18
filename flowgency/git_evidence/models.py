"""Pure Git evidence models – no filesystem, network, or Git access.

These types declare how a team's tickets may be evidenced with Git content.
Flowgency verifies committed content locally only: a project either evidences
any commit range its own repository can prove, or restricts evidence to a set
of allowed local refs. Construction validates shape and lexical safety only.

The capture value types below are plain frozen records describing what a
bounded read produced. They carry no behaviour: `git.py` opens and validates a
repository, and `capture.py` fills these in from real Git output.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _validate_ref(ref: str) -> None:
    if not ref:
        raise ValueError("Ref must not be empty")
    if _CONTROL_CHARS_RE.search(ref):
        raise ValueError(f"Ref must not contain control characters: {ref!r}")
    if "\\" in ref:
        raise ValueError(f"Ref must not contain a backslash: {ref!r}")
    if ".." in ref:
        raise ValueError(f"Ref must not contain '..': {ref!r}")
    if "@{" in ref:
        raise ValueError(f"Ref must not contain '@{{': {ref!r}")
    if ":" in ref:
        raise ValueError(f"Ref must not contain ':': {ref!r}")
    if ref.startswith("/") or ref.endswith("/"):
        raise ValueError(f"Ref must not start or end with '/': {ref!r}")
    if "*" in ref and not ref.endswith("/*"):
        raise ValueError(f"Ref wildcard is only allowed as a trailing '/*': {ref!r}")
    if ref.count("*") > 1:
        raise ValueError(f"Ref must not contain more than one wildcard: {ref!r}")
    for component in ref.split("/"):
        if component == "" or component == "*":
            if component == "":
                raise ValueError(f"Ref must not contain empty path components: {ref!r}")
            continue
        if component.endswith(".lock"):
            raise ValueError(f"Ref component must not end with '.lock': {ref!r}")
        if component.startswith(".") or component.endswith("."):
            raise ValueError(
                f"Ref component must not start or end with '.': {ref!r}"
            )
    if ref.startswith("refs/heads/"):
        if not ref[len("refs/heads/") :]:
            raise ValueError(f"Malformed refs/heads/ name: {ref!r}")
    elif ref.startswith("refs/tags/"):
        if not ref[len("refs/tags/") :]:
            raise ValueError(f"Malformed refs/tags/ name: {ref!r}")
    else:
        raise ValueError(f"Ref must be under refs/heads/ or refs/tags/: {ref!r}")


class GitPublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["local"]
    allowed_refs: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "GitPublicationPolicy":
        if len(set(self.allowed_refs)) != len(self.allowed_refs):
            raise ValueError("allowed_refs must not contain duplicates")
        for ref in self.allowed_refs:
            _validate_ref(ref)
        return self


def validate_exact_ref(ref: str) -> str:
    """Validate one concrete ref name, rejecting the allowlist wildcard form."""
    if not isinstance(ref, str):
        raise ValueError("Ref must be a string")
    _validate_ref(ref)
    if "*" in ref:
        raise ValueError(f"A publication ref must be exact, not a pattern: {ref!r}")
    return ref


def ref_matches_allowlist(ref: str, allowed_refs: tuple[str, ...]) -> bool:
    """Whether an exact ref is authorized by an allowlist entry.

    A trailing ``/*`` entry authorizes names below that prefix; it never
    authorizes the prefix itself, and no other wildcard form exists.
    """
    for pattern in allowed_refs:
        if pattern.endswith("/*"):
            prefix = pattern[:-1]
            if ref.startswith(prefix) and len(ref) > len(prefix):
                return True
        elif ref == pattern:
            return True
    return False


def git_policy_digest(policy: GitPublicationPolicy | None) -> str | None:
    """Return a canonical SHA-256 digest of a publication policy, or None."""
    if policy is None:
        return None
    payload = json.dumps(
        policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


GitChangeStatus = Literal["added", "modified", "deleted", "renamed", "type-changed"]

GIT_EVIDENCE_MESSAGES: dict[str, str] = {
    "git-evidence-workspace-invalid": "The configured workspace is not a usable Git work tree.",
    "git-evidence-not-a-repository": "The configured workspace is not a Git repository.",
    "git-evidence-bare-repository": "A bare repository has no work tree to evidence.",
    "git-evidence-worktree-unregistered": "The workspace is not a registered worktree of its repository.",
    "git-evidence-unsafe-repository": "The repository uses object rewriting or alternates that capture cannot trust.",
    "git-evidence-shallow-repository": "The repository history is incomplete, so the range cannot be proven.",
    "git-evidence-scratch-invalid": "The evidence scratch location is unusable.",
    "git-evidence-git-unavailable": "No trusted Git executable was found on the deployment path.",
    "git-evidence-timeout": "The Git read exceeded its time budget.",
    "git-evidence-command-failed": "A Git read did not complete successfully.",
    "git-evidence-output-too-large": "The Git read produced more output than the evidence limit allows.",
    "git-evidence-object-missing": "A selected commit is not available in this repository.",
    "git-evidence-invalid-commit": "A selected commit is not a full object ID of this repository's format.",
    "git-evidence-range-not-ancestor": "The base commit is not an ancestor of the end commit.",
    "git-evidence-too-many-commits": "The selected range contains more commits than capture allows.",
    "git-evidence-too-many-files": "The selected range changes more paths than capture allows.",
    "git-evidence-unsupported-path": "A changed path could not be represented safely.",
    "git-evidence-unsupported-change": "A change kind in the selected range is not supported.",
    "git-evidence-path-denied": "A changed path is outside the actor's effective read permissions.",
    "git-evidence-repository-changed": "The source repository changed while evidence was being read.",
    "git-evidence-publication-policy-missing": "No Git publication policy is configured for this team.",
    "git-evidence-publication-ref-required": "This publication policy requires an explicitly allowed ref.",
    "git-evidence-publication-ref-denied": "The requested publication ref is not allowed by this policy.",
    "git-evidence-not-published": "The selected end commit is not contained in the required local ref.",
    "git-evidence-verification-incomplete": "Publication could not be proven from the available history.",
}


class GitEvidenceError(Exception):
    """A capture failure with a fixed public code and message.

    Messages are drawn from a fixed table so raw Git stderr, absolute paths,
    and environment values never reach a caller or a ticket.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        if code not in GIT_EVIDENCE_MESSAGES:
            raise ValueError(f"Unknown Git evidence code: {code!r}")
        resolved = GIT_EVIDENCE_MESSAGES[code] if message is None else message
        super().__init__(resolved)
        self.code = code
        self.message = resolved


_OBJECT_ID_LENGTHS = {"sha1": 40, "sha256": 64}
_HEX_DIGITS = frozenset(string.hexdigits.lower())


def validate_object_id(value: str, object_format: str) -> str:
    """Accept only a full lowercase object ID of this repository's format."""
    expected = _OBJECT_ID_LENGTHS.get(object_format)
    if expected is None:
        raise GitEvidenceError("git-evidence-invalid-commit")
    if not isinstance(value, str) or len(value) != expected:
        raise GitEvidenceError("git-evidence-invalid-commit")
    if any(character not in _HEX_DIGITS or character.isupper() for character in value):
        raise GitEvidenceError("git-evidence-invalid-commit")
    return value


@dataclass(frozen=True)
class GitRepository:
    """A validated source repository plus its disposable read-only view."""

    workspace: Path
    source_git_dir: Path
    common_dir: Path
    object_view: Path
    object_format: str
    repository_id: str


@dataclass(frozen=True)
class GitCommitRange:
    base_commit: str
    end_commit: str


@dataclass(frozen=True)
class GitFileChange:
    path: str
    old_path: str | None
    status: GitChangeStatus
    lines_added: int
    lines_removed: int
    binary: bool
    submodule: bool
    old_mode: str
    new_mode: str
    old_object_id: str
    new_object_id: str


@dataclass(frozen=True)
class GitRangeCapture:
    repository_id: str
    base_commit: str
    end_commit: str
    commit_ids: tuple[str, ...]
    files: tuple[GitFileChange, ...]
    patch: bytes


@dataclass(frozen=True)
class GitRefObservation:
    """One ref's own object plus the commit it resolves to.

    An annotated tag's ref object is the tag object, not its commit, so both
    identities are kept: peeling must never be mistaken for the published ref.
    """

    ref_object_id: str
    commit_id: str


@dataclass(frozen=True)
class GitPublicationReceipt:
    """What was observed locally, and when – not a claim about a remote."""

    mode: Literal["local"]
    policy_digest: str
    publication_ref: str | None
    ref_object_id: str | None
    observed_commit: str
    verified_at: datetime
