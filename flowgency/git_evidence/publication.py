"""Prove that captured Git content is present in this repository's own history.

Flowgency verifies committed content locally only. Verification is a *read* of
objects and refs this repository already has: it never commits, pushes, fetches,
updates a ref, contacts a remote, invokes a credential helper or SSH, or touches
the source work tree, and it never claims a verified push. An unrestricted
policy is proven by the verified commit range itself; a restricted policy also
requires one allowed local ref that already contains the captured end commit.

A ref the local graph cannot judge is reported as incomplete. Nothing here
fetches missing objects or downgrades an unprovable claim into a provable one.
"""

from __future__ import annotations

from datetime import datetime

from flowgency.git_evidence.git import run_git_exit_code
from flowgency.git_evidence.models import (
    GitEvidenceError,
    GitPublicationPolicy,
    GitPublicationReceipt,
    GitRangeCapture,
    GitRefObservation,
    GitRepository,
    git_policy_digest,
    ref_matches_allowlist,
    validate_exact_ref,
    validate_object_id,
)
from flowgency.jobs.processes import RuntimeProcessLifecycle

_PROBE_OUTPUT_LIMIT_BYTES = 4096
# A ref is resolved by reading a path under the source ``refs/`` directory, so a
# name that could select anything other than itself never reaches that lookup.
_GLOB_CHARACTERS = "*?[]"


def _validated_ref(ref_name: str) -> str:
    try:
        validate_exact_ref(ref_name)
    except (TypeError, ValueError):
        raise GitEvidenceError("git-evidence-publication-ref-denied") from None
    if any(character in ref_name for character in _GLOB_CHARACTERS):
        raise GitEvidenceError("git-evidence-publication-ref-denied")
    return ref_name


def _read_source_ref(repository: GitRepository, ref: str) -> str | None:
    """Read one loose or packed ref from inside the authorized repository."""
    refs_root = (repository.common_dir / "refs").resolve(strict=False)
    candidate = (repository.common_dir / ref).resolve(strict=False)
    if not candidate.is_relative_to(refs_root):
        # A ref entry that leaves ``refs/`` is refused, never followed.
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
        # The ref is real; this object view simply cannot judge it.
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


def _selected_ref(
    policy: GitPublicationPolicy, publication_ref: str | None
) -> str | None:
    if publication_ref is None:
        if policy.allowed_refs:
            raise GitEvidenceError("git-evidence-publication-ref-required")
        return None
    ref = _validated_ref(publication_ref)
    # An empty allowlist means no ref restriction, not "no ref is authorized".
    if policy.allowed_refs and not ref_matches_allowlist(ref, policy.allowed_refs):
        raise GitEvidenceError("git-evidence-publication-ref-denied")
    return ref


def verify_publication(
    repository: GitRepository,
    captured: GitRangeCapture,
    policy: GitPublicationPolicy | None,
    *,
    publication_ref: str | None,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
    now: datetime,
) -> GitPublicationReceipt:
    """Prove the captured end commit locally, as this project's policy requires."""
    if policy is None:
        raise GitEvidenceError("git-evidence-publication-policy-missing")
    digest = git_policy_digest(policy)
    assert digest is not None
    end_commit = validate_object_id(captured.end_commit, repository.object_format)
    selected = _selected_ref(policy, publication_ref)

    if selected is None:
        # Without a ref restriction the verified commit range is the proof.
        observed_commit, ref_object_id = end_commit, None
    else:
        observation = resolve_publication_ref(
            repository, selected, lifecycle=lifecycle, deadline=deadline
        )
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
    )
