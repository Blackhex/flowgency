"""Pure Git evidence models – no filesystem, network, or Git access.

These types declare how a team's tickets may be evidenced with Git content: a
project either publishes evidence locally only, or to a single named remote
under an explicit set of allowed refs. Construction validates shape and
lexical safety only; resolving a config-relative ``known_hosts`` path against
the config directory is the owning canonical-config boundary's job, not this
module's.

The capture value types below are plain frozen records describing what a
bounded read produced. They carry no behaviour: `git.py` opens and validates a
repository, and `capture.py` fills these in from real Git output.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_TRANSPORT_HELPER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*::")
_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ALLOWED_URL_SCHEMES = {"https", "ssh", "file"}


def _validate_remote_name(name: str) -> None:
    if not _REMOTE_NAME_RE.match(name):
        raise ValueError(
            f"Remote name must be a safe Git config subsection: {name!r}"
        )


def _validate_remote_url(url: str, auth: str) -> None:
    if _CONTROL_CHARS_RE.search(url):
        raise ValueError("Remote URL must not contain control characters")
    if _TRANSPORT_HELPER_RE.match(url):
        raise ValueError("Remote URL must not use a transport helper form")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(f"Unsupported Git remote transport: {parsed.scheme!r}")
    if parsed.query or parsed.fragment:
        raise ValueError("Remote URL must not contain a query or fragment")
    if parsed.password is not None:
        raise ValueError("Remote URL must not contain a password")
    if parsed.scheme == "https":
        if parsed.username is not None:
            raise ValueError("HTTPS remote URL must not contain userinfo")
        if auth == "ssh-agent":
            raise ValueError("ssh-agent authentication requires an SSH remote URL")
    elif parsed.scheme == "ssh":
        if auth != "ssh-agent":
            raise ValueError("SSH remote URL requires ssh-agent authentication")
        if auth == "credential-manager":
            raise ValueError("credential-manager authentication is HTTPS-only")
    elif parsed.scheme == "file":
        if auth != "anonymous":
            raise ValueError("File remote endpoints are anonymous-only")
        if parsed.netloc:
            raise ValueError("File remote URL must not contain a host or UNC share")
        if not parsed.path.startswith("/"):
            raise ValueError("File remote URL must use an absolute path")
    if auth == "credential-manager" and parsed.scheme != "https":
        raise ValueError("credential-manager authentication is HTTPS-only")


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


class GitRemotePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: StrictStr
    url: StrictStr
    auth: Literal["anonymous", "credential-manager", "ssh-agent"] = "anonymous"
    known_hosts: Path | None = None

    @model_validator(mode="after")
    def _validate(self) -> "GitRemotePolicy":
        _validate_remote_name(self.name)
        _validate_remote_url(self.url, self.auth)
        if self.auth == "ssh-agent" and self.known_hosts is None:
            raise ValueError("ssh-agent authentication requires known_hosts")
        if self.auth != "ssh-agent" and self.known_hosts is not None:
            raise ValueError("known_hosts is only used with ssh-agent authentication")
        return self


class GitPublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["local", "remote"]
    allowed_refs: tuple[StrictStr, ...] = ()
    remote: GitRemotePolicy | None = None

    @model_validator(mode="after")
    def _validate(self) -> "GitPublicationPolicy":
        if self.mode == "local":
            if self.remote is not None:
                raise ValueError("Local publication must not declare a remote")
        else:
            if self.remote is None:
                raise ValueError("Remote publication requires a remote")
            if not self.allowed_refs:
                raise ValueError("Remote publication requires at least one allowed ref")
        if len(set(self.allowed_refs)) != len(self.allowed_refs):
            raise ValueError("allowed_refs must not contain duplicates")
        for ref in self.allowed_refs:
            _validate_ref(ref)
        return self


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
