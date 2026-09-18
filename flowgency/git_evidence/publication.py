"""Prove that captured Git content is published where a project requires it.

Verification is a *read*. It never commits, pushes, fetches, updates a ref, or
touches the source work tree, and it never manufactures the publication it is
asked to prove. A local policy proves publication from objects the repository
already has. A remote policy observes exactly one approved ref on one pinned,
credential-free endpoint with ``ls-remote`` and proves the captured end commit
either *is* that observation or is an ancestor of it in the local object view.

A transport, authentication, or graph failure is reported as itself. Nothing
here downgrades to local mode, falls back to a cached tracking ref, or fetches
to make an unprovable claim provable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from flowgency.git_evidence.git import (
    read_source_config_values,
    resolve_trusted_executable,
    run_git_exit_code,
    run_git_remote,
    untrusted_roots,
)
from flowgency.git_evidence.models import (
    GitEvidenceError,
    GitPublicationPolicy,
    GitPublicationReceipt,
    GitRangeCapture,
    GitRefObservation,
    GitRemotePolicy,
    GitRepository,
    git_policy_digest,
    ref_matches_allowlist,
    validate_exact_ref,
    validate_object_id,
)
from flowgency.jobs.processes import RuntimeProcessLifecycle

REMOTE_ADVERTISEMENT_LIMIT_BYTES = 64 * 1024

_PROBE_OUTPUT_LIMIT_BYTES = 4096
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_TRANSPORT_HELPER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*::")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_NETWORK_SCHEMES = {"https", "ssh"}
# ``ls-remote`` matches its patterns as globs, so a ref that reaches the wire
# must not be able to select anything but itself.
_GLOB_CHARACTERS = "*?[]"


@dataclass(frozen=True)
class _Endpoint:
    """One approved destination in three forms: comparable, public, and literal."""

    key: str
    identity: str
    target: str
    scheme: str


def _misconfigured() -> GitEvidenceError:
    return GitEvidenceError("git-evidence-remote-misconfigured")


def _looks_like_scp_syntax(text: str) -> bool:
    head, separator, _ = text.partition(":")
    if not separator or "/" in head or "\\" in head:
        return False
    return not (len(head) == 1 and head.isalpha())


def _file_endpoint(raw: Path) -> _Endpoint:
    text = str(raw)
    if text.startswith(("\\\\", "//")):
        raise _misconfigured()
    if not raw.is_absolute() or ".." in raw.parts:
        raise _misconfigured()
    resolved = raw.resolve(strict=False)
    if not resolved.is_absolute() or ".." in resolved.parts:
        raise _misconfigured()
    return _Endpoint(
        key=f"file:{os.path.normcase(str(resolved))}",
        identity=f"file:{resolved.as_posix()}",
        target=str(resolved),
        scheme="file",
    )


def _file_url_endpoint(text: str) -> _Endpoint:
    parsed = urlparse(text)
    if parsed.netloc not in ("", "localhost"):
        raise _misconfigured()
    if parsed.query or parsed.fragment:
        raise _misconfigured()
    return _file_endpoint(Path(url2pathname(parsed.path)))


def _network_endpoint(text: str) -> _Endpoint:
    parsed = urlparse(text)
    if parsed.scheme not in _NETWORK_SCHEMES:
        raise _misconfigured()
    if parsed.query or parsed.fragment or parsed.password is not None:
        raise _misconfigured()
    if parsed.scheme == "https" and parsed.username is not None:
        raise _misconfigured()
    try:
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise _misconfigured() from None
    if not host:
        raise _misconfigured()
    path = parsed.path or "/"
    if not path.startswith("/"):
        raise _misconfigured()
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    authority = host if port is None else f"{host}:{port}"
    # An SSH login name is part of the destination, not a credential; a
    # password never is, and was already refused above.
    if parsed.username:
        authority = f"{parsed.username}@{authority}"
    canonical = f"{parsed.scheme}://{authority}{path}"
    return _Endpoint(
        key=canonical, identity=canonical, target=canonical, scheme=parsed.scheme
    )


def _canonical_endpoint(value: str) -> _Endpoint:
    """Reduce one declared destination to a single comparable identity.

    A native absolute path and its ``file:`` URI are the same destination; a
    relative path, a UNC or host-bearing ``file:`` URL, an scp-style address,
    and a transport helper are not destinations this boundary will approve.
    """
    text = value.strip()
    if not text or _CONTROL_CHARS_RE.search(text) or _TRANSPORT_HELPER_RE.match(text):
        raise _misconfigured()
    if text.lower().startswith("file:"):
        return _file_url_endpoint(text)
    if _URL_SCHEME_RE.match(text):
        return _network_endpoint(text)
    if _looks_like_scp_syntax(text):
        raise _misconfigured()
    return _file_endpoint(Path(text))


def _approved_endpoint(
    repository: GitRepository,
    remote: GitRemotePolicy,
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> _Endpoint:
    """Require the project's own alias to name exactly the approved endpoint."""
    approved = _canonical_endpoint(remote.url)
    configured = read_source_config_values(
        repository, f"remote.{remote.name}.url", lifecycle=lifecycle, deadline=deadline
    )
    if len(configured) != 1:
        raise _misconfigured()
    if _canonical_endpoint(configured[0]).key != approved.key:
        raise _misconfigured()
    return approved


def _quoted(text: str) -> str:
    if '"' in text or _CONTROL_CHARS_RE.search(text):
        raise _misconfigured()
    return f'"{text}"'


def _ssh_command(repository: GitRepository, remote: GitRemotePolicy) -> str:
    known_hosts = remote.known_hosts
    if known_hosts is None:
        raise _misconfigured()
    known_hosts = Path(known_hosts)
    if not known_hosts.is_absolute() or not known_hosts.is_file():
        raise _misconfigured()
    session = repository.object_view.parent
    empty_config = session / "ssh-config"
    empty_config.write_bytes(b"")
    # A path that does not exist: SSH may not discover or offer a key file, so
    # only the explicitly allowed agent can supply an identity.
    absent_identity = session / "absent-identity"
    options = (
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        f"UserKnownHostsFile={known_hosts.as_posix()}",
        "GlobalKnownHostsFile=/dev/null",
        f"IdentityFile={absent_identity.as_posix()}",
        "IdentityAgent=SSH_AUTH_SOCK",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "NumberOfPasswordPrompts=0",
        "AddKeysToAgent=no",
        "ControlMaster=no",
        "ControlPath=none",
    )
    executable = resolve_trusted_executable(
        "ssh", forbidden_roots=untrusted_roots(repository)
    )
    parts = [_quoted(executable.as_posix()), "-F", _quoted(empty_config.as_posix())]
    for option in options:
        parts.extend(("-o", _quoted(option)))
    return " ".join(parts)


def _transport_settings(
    repository: GitRepository,
    remote: GitRemotePolicy,
    endpoint: _Endpoint,
    *,
    credentials_allowed: bool,
) -> tuple[tuple[str, ...], dict[str, str]]:
    options = [
        "protocol.allow=never",
        f"protocol.{endpoint.scheme}.allow=always",
        "http.followRedirects=false",
        "core.askPass=",
        "credential.helper=",
    ]
    environment: dict[str, str] = {}
    if remote.auth == "anonymous":
        return tuple(options), environment
    if not credentials_allowed:
        raise GitEvidenceError("git-evidence-credentials-denied")
    if remote.auth == "credential-manager":
        try:
            helper = resolve_trusted_executable(
                "git-credential-manager", forbidden_roots=untrusted_roots(repository)
            )
        except GitEvidenceError:
            raise GitEvidenceError("git-evidence-credential-helper-unavailable") from None
        options.extend(
            [
                f"credential.helper={helper.as_posix()}",
                "credential.useHttpPath=true",
                "credential.interactive=false",
                "credential.guiPrompt=false",
            ]
        )
        return tuple(options), environment
    socket = os.environ.get("SSH_AUTH_SOCK", "").strip()
    if not socket:
        raise GitEvidenceError("git-evidence-ssh-agent-unavailable")
    environment["GIT_SSH_COMMAND"] = _ssh_command(repository, remote)
    environment["SSH_AUTH_SOCK"] = socket
    return tuple(options), environment


def _validated_ref(ref_name: str) -> str:
    try:
        validate_exact_ref(ref_name)
    except (TypeError, ValueError):
        raise GitEvidenceError("git-evidence-publication-ref-denied") from None
    if any(character in ref_name for character in _GLOB_CHARACTERS):
        raise GitEvidenceError("git-evidence-publication-ref-denied")
    return ref_name


def _parse_advertisement(data: bytes, object_format: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise GitEvidenceError("git-evidence-remote-response-invalid") from None
    advertised: dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if not line:
            continue
        object_id, separator, name = line.partition("\t")
        if not separator or not name:
            raise GitEvidenceError("git-evidence-remote-response-invalid")
        try:
            validate_object_id(object_id, object_format)
        except GitEvidenceError:
            raise GitEvidenceError("git-evidence-remote-response-invalid") from None
        if advertised.setdefault(name, object_id) != object_id:
            raise GitEvidenceError("git-evidence-remote-response-invalid")
    if not advertised:
        raise GitEvidenceError("git-evidence-remote-response-invalid")
    return advertised


def observe_remote_ref(
    repository: GitRepository,
    remote: GitRemotePolicy,
    ref_name: str,
    *,
    credentials_allowed: bool,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> GitRefObservation:
    """Read one approved ref from the pinned endpoint without fetching it."""
    ref = _validated_ref(ref_name)
    endpoint = _approved_endpoint(
        repository, remote, lifecycle=lifecycle, deadline=deadline
    )
    options, environment = _transport_settings(
        repository, remote, endpoint, credentials_allowed=credentials_allowed
    )
    is_tag = ref.startswith("refs/tags/")
    if is_tag:
        # Without ``--refs`` the peeled ``^{}`` line is advertised too, which is
        # the only way to learn an annotated tag's commit without fetching it.
        arguments = ("ls-remote", "--exit-code", endpoint.target, ref, f"{ref}^{{}}")
    else:
        arguments = ("ls-remote", "--exit-code", "--refs", endpoint.target, ref)
    code, data = run_git_remote(
        repository,
        arguments,
        config_options=options,
        environment_overrides=environment,
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=REMOTE_ADVERTISEMENT_LIMIT_BYTES,
    )
    if code == 2:
        raise GitEvidenceError("git-evidence-not-published")
    if code != 0:
        raise GitEvidenceError("git-evidence-remote-unreachable")
    advertised = _parse_advertisement(data, repository.object_format)
    ref_object_id = advertised.get(ref)
    if ref_object_id is None:
        raise GitEvidenceError("git-evidence-not-published")
    # A lightweight tag has no peeled line, so its ref object *is* its commit.
    commit_id = advertised.get(f"{ref}^{{}}", ref_object_id) if is_tag else ref_object_id
    return GitRefObservation(ref_object_id=ref_object_id, commit_id=commit_id)


def _read_source_ref(repository: GitRepository, ref: str) -> str | None:
    refs_root = (repository.common_dir / "refs").resolve(strict=False)
    candidate = (repository.common_dir / ref).resolve(strict=False)
    if not candidate.is_relative_to(refs_root):
        raise GitEvidenceError("git-evidence-publication-ref-denied")
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("ref:"):
            raise GitEvidenceError("git-evidence-verification-incomplete")
        return text
    packed = repository.common_dir / "packed-refs"
    if not packed.is_file():
        return None
    for raw in packed.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = raw.strip()
        if not line or line.startswith(("#", "^")):
            continue
        object_id, separator, name = line.partition(" ")
        if separator and name == ref:
            return object_id
    return None


def resolve_publication_ref(
    repository: GitRepository,
    ref_name: str,
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> GitRefObservation:
    """Resolve one validated source ref to its ref object and peeled commit."""
    ref = _validated_ref(ref_name)
    raw = _read_source_ref(repository, ref)
    if raw is None:
        raise GitEvidenceError("git-evidence-not-published")
    ref_object_id = validate_object_id(raw, repository.object_format)
    code, data = run_git_exit_code(
        repository,
        ("cat-file", "-t", ref_object_id),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=_PROBE_OUTPUT_LIMIT_BYTES,
    )
    if code != 0:
        raise GitEvidenceError("git-evidence-verification-incomplete")
    kind = data.strip()
    if kind == b"commit":
        return GitRefObservation(ref_object_id=ref_object_id, commit_id=ref_object_id)
    if kind != b"tag":
        raise GitEvidenceError("git-evidence-invalid-commit")
    code, data = run_git_exit_code(
        repository,
        ("rev-parse", "--verify", "--quiet", f"{ref_object_id}^{{commit}}"),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=_PROBE_OUTPUT_LIMIT_BYTES,
    )
    if code != 0:
        raise GitEvidenceError("git-evidence-verification-incomplete")
    commit_id = validate_object_id(
        data.decode("ascii", errors="replace").strip(), repository.object_format
    )
    return GitRefObservation(ref_object_id=ref_object_id, commit_id=commit_id)


def _require_ancestor(
    repository: GitRepository,
    end_commit: str,
    observed_commit: str,
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> None:
    code, _ = run_git_exit_code(
        repository,
        ("cat-file", "-e", f"{observed_commit}^{{commit}}"),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=_PROBE_OUTPUT_LIMIT_BYTES,
    )
    if code != 0:
        # The observation is real; this repository simply cannot judge it yet.
        raise GitEvidenceError("git-evidence-verification-incomplete")
    code, _ = run_git_exit_code(
        repository,
        ("merge-base", "--is-ancestor", end_commit, observed_commit),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=_PROBE_OUTPUT_LIMIT_BYTES,
    )
    if code == 0:
        return
    if code == 1:
        raise GitEvidenceError("git-evidence-not-published")
    raise GitEvidenceError("git-evidence-verification-incomplete")


def _selected_ref(policy: GitPublicationPolicy, publication_ref: str | None) -> str | None:
    if publication_ref is None:
        if policy.allowed_refs or policy.mode != "local":
            raise GitEvidenceError("git-evidence-publication-ref-required")
        return None
    ref = _validated_ref(publication_ref)
    if not ref_matches_allowlist(ref, policy.allowed_refs):
        raise GitEvidenceError("git-evidence-publication-ref-denied")
    return ref


def verify_publication(
    repository: GitRepository,
    captured: GitRangeCapture,
    policy: GitPublicationPolicy | None,
    *,
    publication_ref: str | None,
    credentials_allowed: bool,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
    now: datetime,
) -> GitPublicationReceipt:
    """Prove the captured end commit is published as this project requires."""
    if policy is None:
        raise GitEvidenceError("git-evidence-publication-policy-missing")
    digest = git_policy_digest(policy)
    assert digest is not None
    end_commit = validate_object_id(captured.end_commit, repository.object_format)
    selected = _selected_ref(policy, publication_ref)

    remote_identity: str | None = None
    if policy.mode == "local":
        observation = (
            None
            if selected is None
            else resolve_publication_ref(
                repository, selected, lifecycle=lifecycle, deadline=deadline
            )
        )
    else:
        assert policy.remote is not None and selected is not None
        remote_identity = _canonical_endpoint(policy.remote.url).identity
        observation = observe_remote_ref(
            repository,
            policy.remote,
            selected,
            credentials_allowed=credentials_allowed,
            lifecycle=lifecycle,
            deadline=deadline,
        )

    if observation is None:
        observed_commit, ref_object_id = end_commit, None
    else:
        observed_commit = validate_object_id(
            observation.commit_id, repository.object_format
        )
        ref_object_id = validate_object_id(
            observation.ref_object_id, repository.object_format
        )
        if observed_commit != end_commit:
            _require_ancestor(
                repository,
                end_commit,
                observed_commit,
                lifecycle=lifecycle,
                deadline=deadline,
            )
    return GitPublicationReceipt(
        mode=policy.mode,
        policy_digest=digest,
        publication_ref=selected,
        ref_object_id=ref_object_id,
        observed_commit=observed_commit,
        verified_at=now,
        remote_identity=remote_identity,
    )
