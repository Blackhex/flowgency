"""Pure Git publication policy models – no filesystem, network, or Git access.

These types declare how a team's tickets may be evidenced with Git content: a
project either publishes evidence locally only, or to a single named remote
under an explicit set of allowed refs. Construction validates shape and
lexical safety only; resolving a config-relative ``known_hosts`` path against
the config directory is the owning canonical-config boundary's job, not this
module's.
"""

from __future__ import annotations

import hashlib
import json
import re
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
