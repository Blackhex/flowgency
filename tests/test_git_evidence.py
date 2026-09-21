"""Git evidence contracts: publication policy models and bounded capture.

The policy models do no filesystem or network work; they only validate the
shape of a project's Git publication policy. The capture tests below run real
Git against isolated ``tmp_path`` repositories built by
``tests._git_evidence_helpers``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from flowgency.git_evidence import capture as capture_module
from flowgency.git_evidence import git as git_module
from flowgency.git_evidence import publication as publication_module
from flowgency.git_evidence.capture import capture_committed_range
from flowgency.git_evidence.git import (
    LOOSE_REF_LIMIT_BYTES,
    PACKED_REFS_LIMIT_BYTES,
    open_git_repository,
    run_git_bytes,
    verify_repository_identity,
)
from flowgency.git_evidence.models import (
    GitCommitRange,
    GitEvidenceError,
    GitPublicationPolicy,
    GitPublicationReceipt,
    git_policy_digest,
)
from flowgency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule
from flowgency.jobs.processes import (
    CompletedRuntimeProcess,
    ProcessStopEvidence,
    RuntimeProcessLifecycle,
)
from tests._git_evidence_helpers import (
    commit_tree,
    create_git_repository,
    git_command,
    init_git_repository,
    read_tree_entries,
    requires_git,
    sha256_supported,
    snapshot_repository,
    tree_entry,
    write_blob,
)

UNRESTRICTED = (EffectiveRuntimePolicy(timeout=30),)


def _lifecycle(name: str = "capture-test") -> RuntimeProcessLifecycle:
    return RuntimeProcessLifecycle(job_id=name, generation="generation-a")


def _capture(root: Path, base: str, end: str, scratch: Path, *, policies=UNRESTRICTED):
    lifecycle = _lifecycle()
    with open_git_repository(root, scratch_root=scratch, lifecycle=lifecycle) as repository:
        return capture_committed_range(
            repository,
            GitCommitRange(base, end),
            policies=policies,
            lifecycle=lifecycle,
            deadline=time.monotonic() + 120,
        )


@contextlib.contextmanager
def _source_unchanged(root: Path):
    """Assert a capture attempt left every source byte and ref exactly as it was."""
    before = snapshot_repository(root)
    yield
    assert snapshot_repository(root) == before


_VERIFIED_AT = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


@contextlib.contextmanager
def _publication_context(root: Path, base: str, end: str, scratch: Path):
    lifecycle = _lifecycle("publication-test")
    with open_git_repository(root, scratch_root=scratch, lifecycle=lifecycle) as repository:
        deadline = time.monotonic() + 120
        captured = capture_committed_range(
            repository,
            GitCommitRange(base, end),
            policies=UNRESTRICTED,
            lifecycle=lifecycle,
            deadline=deadline,
        )
        yield (repository, captured, lifecycle, deadline)


def _verify(context, policy, *, publication_ref):
    repository, captured, lifecycle, deadline = context
    return publication_module.verify_publication(
        repository,
        captured,
        policy,
        publication_ref=publication_ref,
        lifecycle=lifecycle,
        deadline=deadline,
        now=_VERIFIED_AT,
    )


_SUPERSEDED_POLICIES = {
    # The exact shape Task 1 accepted under the superseded remote contract.
    "remote-mode": {
        "mode": "remote",
        "allowed_refs": ["refs/heads/main"],
        "remote": {"name": "origin", "url": "https://example.com/repo.git"},
    },
    "local-with-remote": {
        "mode": "local",
        "remote": {"name": "origin", "url": "file:///abs/repo.git"},
    },
    "local-with-auth": {"mode": "local", "auth": "ssh-agent"},
    "local-with-known-hosts": {"mode": "local", "known_hosts": "known_hosts"},
    "local-with-agent-socket": {
        "mode": "local",
        "ssh_auth_sock": "/run/user/1000/keyring/ssh",
    },
    "local-with-agent-endpoint": {
        "mode": "local",
        "agent_endpoint": "ssh://git@example.com",
    },
}


@pytest.mark.parametrize(
    "raw", list(_SUPERSEDED_POLICIES.values()), ids=list(_SUPERSEDED_POLICIES)
)
def test_a_superseded_remote_publication_policy_is_rejected(raw):
    with pytest.raises(ValidationError):
        GitPublicationPolicy.model_validate(raw)


def test_a_publication_policy_declares_only_local_mode_and_allowed_refs():
    policy = GitPublicationPolicy(mode="local", allowed_refs=("refs/heads/main",))
    assert policy.model_dump(mode="json") == {
        "mode": "local",
        "allowed_refs": ["refs/heads/main"],
    }


def test_a_local_publication_policy_needs_no_allowed_refs():
    policy = GitPublicationPolicy(mode="local")
    assert policy.allowed_refs == ()


def test_a_publication_policy_rejects_duplicate_allowed_refs():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="local", allowed_refs=("refs/heads/main", "refs/heads/main")
        )


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "refs/heads/",
        "refs/heads/../main",
        "refs/heads/ma\\in",
        "refs/heads/main@{0}",
        "refs/heads/ma:in",
        "refs/heads/ma*in",
        "refs/heads/*/main",
        "refs/heads/main.lock",
        "refs/heads/.main",
        "refs/heads/main.",
        "/refs/heads/main",
        "refs/heads/main/",
        "main",
        "heads/main",
    ],
)
def test_invalid_allowed_ref_is_rejected(ref):
    with pytest.raises(ValidationError):
        GitPublicationPolicy(mode="local", allowed_refs=(ref,))


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/main",
        "refs/heads/feature/foo",
        "refs/tags/v1.0.0",
        "refs/heads/*",
    ],
)
def test_valid_allowed_ref_is_accepted(ref):
    policy = GitPublicationPolicy(mode="local", allowed_refs=(ref,))
    assert policy.allowed_refs == (ref,)


def test_git_policy_digest_is_none_for_missing_policy():
    assert git_policy_digest(None) is None


def test_git_policy_digest_is_stable_across_equivalent_construction():
    policy_a = GitPublicationPolicy(
        mode="local", allowed_refs=("refs/heads/main", "refs/tags/v1")
    )
    policy_b = GitPublicationPolicy.model_validate(policy_a.model_dump(mode="json"))
    assert git_policy_digest(policy_a) == git_policy_digest(policy_b)


def test_git_policy_digest_changes_with_policy():
    unrestricted = GitPublicationPolicy(mode="local")
    restricted = GitPublicationPolicy(mode="local", allowed_refs=("refs/heads/main",))
    assert git_policy_digest(unrestricted) != git_policy_digest(restricted)


@requires_git
def test_capture_excludes_dirty_staged_and_untracked_content(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / "unrelated.txt").write_bytes(b"staged secret\n")
    git_command(fixture.root, "add", "--", "unrelated.txt")
    (fixture.root / "result.txt").write_bytes(b"uncommitted result\n")
    (fixture.root / "untracked.txt").write_bytes(b"untracked secret\n")
    lifecycle = RuntimeProcessLifecycle(job_id="capture-test", generation="generation-a")
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        captured = capture_committed_range(
            repository,
            GitCommitRange(fixture.base_commit, fixture.end_commit),
            policies=(EffectiveRuntimePolicy(timeout=30),),
            lifecycle=lifecycle,
            deadline=time.monotonic() + 120,
        )
    assert [change.path for change in captured.files] == ["result.txt"]
    assert b"+after" in captured.patch
    assert b"uncommitted" not in captured.patch
    assert b"secret" not in captured.patch
    assert captured.commit_ids == (fixture.end_commit,)


@requires_git
def test_capture_leaves_the_source_repository_byte_identical(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / "untracked.txt").write_bytes(b"untracked\n")
    before = snapshot_repository(fixture.root)
    _capture(fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch")
    assert snapshot_repository(fixture.root) == before


@requires_git
def test_capture_removes_its_private_view_even_when_the_body_fails(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    scratch = tmp_path / "scratch"
    with pytest.raises(RuntimeError):
        with open_git_repository(
            fixture.root, scratch_root=scratch, lifecycle=_lifecycle()
        ) as repository:
            view = repository.object_view
            assert view.is_dir()
            raise RuntimeError("capture body failed")
    assert not view.exists()
    assert list(scratch.iterdir()) == []


@requires_git
def test_capture_records_additions_deletions_renames_and_type_changes(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    root = fixture.root
    (root / "added.txt").write_bytes(b"added\n")
    (root / "docs").mkdir()
    (root / "docs" / "nested.txt").write_bytes(b"nested\n")
    (root / "result.txt").unlink()
    git_command(root, "mv", "unrelated.txt", "renamed.txt")
    git_command(root, "add", "--all", "--")
    git_command(root, "commit", "-m", "test: restructure")
    end = git_command(root, "rev-parse", "HEAD").decode().strip()

    captured = _capture(root, fixture.end_commit, end, tmp_path / "scratch")
    by_path = {change.path: change for change in captured.files}
    assert by_path["added.txt"].status == "added"
    assert by_path["added.txt"].lines_added == 1
    assert by_path["docs/nested.txt"].status == "added"
    assert by_path["result.txt"].status == "deleted"
    assert by_path["renamed.txt"].status == "renamed"
    assert by_path["renamed.txt"].old_path == "unrelated.txt"
    assert [change.path for change in captured.files] == sorted(by_path)
    assert all(change.binary is False for change in captured.files)
    assert all(change.submodule is False for change in captured.files)


@requires_git
def test_capture_marks_binary_content_instead_of_inventing_line_counts(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / "payload.bin").write_bytes(bytes(range(256)) * 4)
    git_command(fixture.root, "add", "--", "payload.bin")
    git_command(fixture.root, "commit", "-m", "test: add binary payload")
    end = git_command(fixture.root, "rev-parse", "HEAD").decode().strip()

    captured = _capture(fixture.root, fixture.end_commit, end, tmp_path / "scratch")
    change = captured.files[0]
    assert change.path == "payload.bin"
    assert change.binary is True
    assert change.lines_added == 0
    assert change.lines_removed == 0
    assert b"GIT binary patch" in captured.patch


@requires_git
def test_capture_reads_symlink_gitlink_and_awkward_names_from_git_objects(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    root = fixture.root
    link_blob = write_blob(root, b"../../outside-target")
    plain_blob = write_blob(root, b"exotic\n")
    entries = read_tree_entries(root, fixture.end_commit)
    entries += tree_entry("120000", "blob", link_blob, b"link")
    entries += tree_entry("160000", "commit", fixture.base_commit, b"vendor")
    entries += tree_entry("100644", "blob", plain_blob, b"with space.txt")
    entries += tree_entry("100644", "blob", plain_blob, b"with\ttab.txt")
    entries += tree_entry("100644", "blob", plain_blob, "žluť.txt".encode())
    end = commit_tree(root, entries, parent=fixture.end_commit)

    captured = _capture(root, fixture.end_commit, end, tmp_path / "scratch")
    by_path = {change.path: change for change in captured.files}
    assert by_path["link"].new_mode == "120000"
    assert by_path["link"].submodule is False
    assert by_path["vendor"].submodule is True
    assert by_path["vendor"].new_mode == "160000"
    assert by_path["with space.txt"].status == "added"
    assert by_path["with\ttab.txt"].status == "added"
    assert by_path["žluť.txt"].status == "added"
    # The symlink target is diff content read from the blob, never followed.
    assert b"outside-target" in captured.patch
    assert not (root / "link").exists()


@requires_git
def test_capture_rejects_a_path_whose_bytes_are_not_valid_utf8(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    blob = write_blob(fixture.root, b"payload\n")
    entries = read_tree_entries(fixture.root, fixture.end_commit)
    entries += tree_entry("100644", "blob", blob, b"broken-\xff-name.txt")
    end = commit_tree(fixture.root, entries, parent=fixture.end_commit)

    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, fixture.end_commit, end, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-unsupported-path"


@pytest.mark.parametrize(
    "raw", [b"broken-\xff.txt", b"line\nbreak.txt", b"back\\slash.txt", b'"quoted.txt"', b""]
)
def test_unrepresentable_paths_are_named_rather_than_dropped(raw):
    with pytest.raises(GitEvidenceError) as failure:
        capture_module._decode_path(raw)
    assert failure.value.code == "git-evidence-unsupported-path"


@requires_git
def test_capture_of_an_empty_range_is_honestly_empty(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    captured = _capture(
        fixture.root, fixture.end_commit, fixture.end_commit, tmp_path / "scratch"
    )
    assert captured.commit_ids == ()
    assert captured.files == ()
    assert captured.patch == b""


@requires_git
def test_capture_supports_a_sha256_repository(tmp_path):
    if not sha256_supported(tmp_path):
        pytest.skip("this Git build does not support the sha256 object format")
    fixture = create_git_repository(tmp_path / "repo", object_format="sha256")
    assert len(fixture.end_commit) == 64

    lifecycle = _lifecycle()
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        assert repository.object_format == "sha256"
        captured = capture_committed_range(
            repository,
            GitCommitRange(fixture.base_commit, fixture.end_commit),
            policies=UNRESTRICTED,
            lifecycle=lifecycle,
            deadline=time.monotonic() + 120,
        )
    assert [change.path for change in captured.files] == ["result.txt"]
    assert len(captured.files[0].new_object_id) == 64


@requires_git
@pytest.mark.parametrize(
    "selected",
    [
        "HEAD",
        "main~1",
        "-c",
        "0123456789abcdef0123456789abcdef0123456",
        "0123456789ABCDEF0123456789ABCDEF01234567",
    ],
)
def test_capture_rejects_anything_but_a_full_object_id(tmp_path, selected):
    fixture = create_git_repository(tmp_path / "repo")
    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, selected, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-invalid-commit"


@requires_git
def test_capture_rejects_a_commit_the_repository_does_not_have(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    absent = "0" * len(fixture.end_commit)
    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, absent, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-object-missing"


@requires_git
def test_capture_rejects_a_non_ancestor_range(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    with _source_unchanged(fixture.root):
        with pytest.raises(GitEvidenceError) as failure:
            _capture(
                fixture.root, fixture.end_commit, fixture.base_commit, tmp_path / "scratch"
            )
    assert failure.value.code == "git-evidence-range-not-ancestor"


@requires_git
def test_capture_rejects_incomplete_shallow_ancestry(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / ".git" / "shallow").write_bytes(
        f"{fixture.base_commit}\n".encode()
    )
    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-shallow-repository"


@requires_git
def test_capture_stops_at_the_commit_limit(tmp_path, monkeypatch):
    fixture = create_git_repository(tmp_path / "repo")
    monkeypatch.setattr(capture_module, "MAX_COMMITS", 0)
    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-too-many-commits"


@requires_git
def test_capture_stops_at_the_changed_path_limit(tmp_path, monkeypatch):
    fixture = create_git_repository(tmp_path / "repo")
    monkeypatch.setattr(capture_module, "MAX_FILES", 0)
    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-too-many-files"


@requires_git
def test_capture_stops_when_the_patch_exceeds_its_byte_budget(tmp_path, monkeypatch):
    fixture = create_git_repository(tmp_path / "repo")
    monkeypatch.setattr(capture_module, "MAX_PATCH_BYTES", 64)
    with _source_unchanged(fixture.root):
        with pytest.raises(GitEvidenceError) as failure:
            _capture(fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-output-too-large"


@requires_git
def test_run_git_bytes_reports_an_overflowing_read(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        with pytest.raises(GitEvidenceError) as failure:
            run_git_bytes(
                repository,
                ("cat-file", "-p", fixture.end_commit),
                lifecycle=lifecycle,
                deadline=time.monotonic() + 30,
                output_limit=4,
            )
    assert failure.value.code == "git-evidence-output-too-large"


@requires_git
def test_run_git_bytes_refuses_to_start_past_its_deadline(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        with pytest.raises(GitEvidenceError) as failure:
            run_git_bytes(
                repository,
                ("cat-file", "-t", fixture.end_commit),
                lifecycle=lifecycle,
                deadline=time.monotonic() - 1,
                output_limit=4096,
            )
    assert failure.value.code == "git-evidence-timeout"


def _completed(outcome, *, exit_code=0, stdout=b"", stderr=b""):
    return CompletedRuntimeProcess(
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        process_stop_evidence=ProcessStopEvidence(
            job_id="capture-test", generation="generation-a", confirmed=True, reason=outcome
        ),
        outcome=outcome,
        stdout_bytes=stdout,
        stderr_bytes=stderr,
    )


@requires_git
@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        ("timeout", "git-evidence-timeout"),
        ("launch-failed", "git-evidence-command-failed"),
        ("containment-setup-failed", "git-evidence-command-failed"),
    ],
)
def test_supervised_failures_map_to_fixed_public_codes(tmp_path, monkeypatch, outcome, code):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        monkeypatch.setattr(
            git_module, "run_supervised", lambda *args, **kwargs: _completed(outcome)
        )
        with pytest.raises(GitEvidenceError) as failure:
            run_git_bytes(
                repository,
                ("cat-file", "-t", fixture.end_commit),
                lifecycle=lifecycle,
                deadline=time.monotonic() + 30,
                output_limit=4096,
            )
    assert failure.value.code == code
    assert "cat-file" not in failure.value.message


@requires_git
def test_capture_requires_read_permission_for_every_selected_path(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    policy = EffectiveRuntimePolicy(
        timeout=30,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=fixture.root, tools=("read",)),
            ResolvedPermissionRule(path=fixture.root / "result.txt", tools=()),
        ),
    )
    lifecycle = RuntimeProcessLifecycle(job_id="capture-test", generation="generation-b")
    with _source_unchanged(fixture.root):
        with open_git_repository(
            fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
        ) as repository:
            with pytest.raises(GitEvidenceError) as failure:
                capture_committed_range(
                    repository,
                    GitCommitRange(fixture.base_commit, fixture.end_commit),
                    policies=(policy,),
                    lifecycle=lifecycle,
                    deadline=time.monotonic() + 120,
                )
    assert failure.value.code == "git-evidence-path-denied"


@requires_git
def test_capture_denies_a_rename_whose_old_path_is_unreadable(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "mv", "unrelated.txt", "renamed.txt")
    git_command(fixture.root, "commit", "-am", "test: rename secret source")
    end = git_command(fixture.root, "rev-parse", "HEAD").decode().strip()
    policy = EffectiveRuntimePolicy(
        timeout=30,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=fixture.root, tools=("read",)),
            ResolvedPermissionRule(path=fixture.root / "unrelated.txt", tools=("write",)),
        ),
    )
    with pytest.raises(GitEvidenceError) as failure:
        _capture(
            fixture.root, fixture.end_commit, end, tmp_path / "scratch", policies=(policy,)
        )
    assert failure.value.code == "git-evidence-path-denied"


@requires_git
def test_capture_denies_a_path_denied_by_any_single_policy(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    launch_policy = EffectiveRuntimePolicy(
        timeout=30,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=fixture.root, tools=("read",)),),
    )
    current_policy = EffectiveRuntimePolicy(
        timeout=30,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=fixture.root, tools=("write",)),),
    )
    with pytest.raises(GitEvidenceError) as failure:
        _capture(
            fixture.root,
            fixture.base_commit,
            fixture.end_commit,
            tmp_path / "scratch",
            policies=(launch_policy, current_policy),
        )
    assert failure.value.code == "git-evidence-path-denied"


@requires_git
def test_capture_denies_a_git_administrative_path(tmp_path, monkeypatch):
    fixture = create_git_repository(tmp_path / "repo")
    monkeypatch.setattr(
        capture_module, "_decode_path", lambda raw: "../outside/escape.txt"
    )
    with pytest.raises(GitEvidenceError) as failure:
        _capture(fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch")
    assert failure.value.code == "git-evidence-path-denied"


@requires_git
@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../escape.txt", ".git/config", "C:/Windows/system.ini"]
)
def test_unsafe_relative_paths_are_never_authorized(tmp_path, path):
    with pytest.raises(GitEvidenceError) as failure:
        capture_module._authorize_path(path, workspace=tmp_path, policies=UNRESTRICTED)
    assert failure.value.code == "git-evidence-path-denied"


@requires_git
def test_capture_revalidates_repository_identity_before_returning(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        stale = dataclasses.replace(repository, repository_id="0" * 64)
        with pytest.raises(GitEvidenceError) as failure:
            capture_committed_range(
                stale,
                GitCommitRange(fixture.base_commit, fixture.end_commit),
                policies=UNRESTRICTED,
                lifecycle=lifecycle,
                deadline=time.monotonic() + 120,
            )
    assert failure.value.code == "git-evidence-repository-changed"


@requires_git
def test_swapping_the_source_repository_is_detected(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        verify_repository_identity(repository)
        other = create_git_repository(tmp_path / "other")
        (fixture.root / ".git").rename(tmp_path / "moved-git")
        shutil.copytree(other.root / ".git", fixture.root / ".git")
        with pytest.raises(GitEvidenceError) as failure:
            verify_repository_identity(repository)
    assert failure.value.code == "git-evidence-repository-changed"


@requires_git
def test_capture_accepts_a_registered_linked_worktree(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    linked = tmp_path / "linked"
    git_command(fixture.root, "worktree", "add", "--detach", str(linked), fixture.end_commit)

    lifecycle = _lifecycle()
    with open_git_repository(
        linked, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        assert repository.common_dir == (fixture.root / ".git").resolve()
        assert repository.source_git_dir != repository.common_dir
        captured = capture_committed_range(
            repository,
            GitCommitRange(fixture.base_commit, fixture.end_commit),
            policies=UNRESTRICTED,
            lifecycle=lifecycle,
            deadline=time.monotonic() + 120,
        )
    assert [change.path for change in captured.files] == ["result.txt"]


@requires_git
def test_a_git_pointer_into_another_repository_is_not_authority(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    linked = tmp_path / "linked"
    git_command(fixture.root, "worktree", "add", "--detach", str(linked), fixture.end_commit)
    impostor = tmp_path / "impostor"
    impostor.mkdir()
    admin = (fixture.root / ".git" / "worktrees" / "linked").resolve()
    (impostor / ".git").write_text(f"gitdir: {admin.as_posix()}\n", encoding="utf-8")

    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            impostor, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-worktree-unregistered"


@requires_git
@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        ("objects/info/alternates", b"/somewhere/else/objects\n", "git-evidence-unsafe-repository"),
        ("info/grafts", b"", "git-evidence-unsafe-repository"),
        ("refs/replace/deadbeef", b"deadbeef\n", "git-evidence-unsafe-repository"),
    ],
)
def test_open_rejects_object_rewriting_metadata(tmp_path, relative, content, code):
    fixture = create_git_repository(tmp_path / "repo")
    target = fixture.root / ".git" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            fixture.root, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == code


@requires_git
def test_open_rejects_a_replacement_ref_recorded_in_packed_refs(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / ".git" / "packed-refs").write_bytes(
        f"# pack-refs with: peeled\n{fixture.base_commit} refs/replace/{fixture.end_commit}\n".encode()
    )
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            fixture.root, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-unsafe-repository"


def _packed_refs_bytes(fixture, total: int) -> bytes:
    """Ordinary packed refs padded with comment lines to an exact byte length."""
    body = (
        "# pack-refs with: peeled fully-peeled sorted \n"
        f"{fixture.end_commit} refs/heads/main\n"
    ).encode()
    return body + b"#" * (total - len(body) - 1) + b"\n"


@requires_git
def test_open_still_inspects_packed_refs_exactly_at_the_read_bound(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / ".git" / "packed-refs").write_bytes(
        _packed_refs_bytes(fixture, PACKED_REFS_LIMIT_BYTES)
    )
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
    ) as repository:
        assert repository.common_dir == (fixture.root / ".git").resolve()


@requires_git
def test_open_refuses_packed_refs_it_cannot_read_within_its_bound(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / ".git" / "packed-refs").write_bytes(
        _packed_refs_bytes(fixture, PACKED_REFS_LIMIT_BYTES + 1)
    )
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            fixture.root, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-verification-incomplete"


@requires_git
def test_open_rejects_a_bare_repository(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "--template=", str(bare)], check=True, capture_output=True)
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            bare, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-bare-repository"


@requires_git
def test_open_rejects_a_directory_that_is_not_a_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            plain, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-not-a-repository"


@requires_git
@pytest.mark.parametrize("workspace", ["relative/repo", "/absent/repo"])
def test_open_rejects_an_unusable_workspace(tmp_path, workspace):
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            Path(workspace), scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-workspace-invalid"


@requires_git
def test_open_refuses_to_write_its_private_view_into_the_workspace(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            fixture.root,
            scratch_root=fixture.root / "scratch",
            lifecycle=_lifecycle(),
        ):
            pass
    assert failure.value.code == "git-evidence-scratch-invalid"


def _job_scratch_root(tmp_path: Path, job_id: str, *, depth_padding: int = 0) -> Path:
    """The scratch root a real job hands capture: the job's own artifact area."""
    root = tmp_path / "memory" / ".jobs" / "newsletter" / "artifacts" / job_id
    if depth_padding:
        root = root / ("d" * depth_padding)
    return root / "git-evidence"


@requires_git
def test_capture_works_from_a_deep_artifact_root_with_a_normal_job_id(tmp_path):
    """A 32-hex job id under a deep data root is ordinary, not a fixture luxury."""
    fixture = create_git_repository(tmp_path / "repo")
    scratch = _job_scratch_root(tmp_path, "5adf2d3ec7a84811a0197c48dfab2310")
    scratch.mkdir(parents=True, exist_ok=True)

    captured = _capture(fixture.root, fixture.base_commit, fixture.end_commit, scratch)

    assert captured.commit_ids == (fixture.end_commit,)
    assert [change.path for change in captured.files] == ["result.txt"]


@requires_git
def test_deeper_than_the_platform_supports_names_the_scratch_location(tmp_path):
    """Unsupported depth fails as a scratch problem, not a phantom missing object."""
    fixture = create_git_repository(tmp_path / "repo")
    padding = max(
        0,
        git_module._MAX_WORKING_DIRECTORY_CHARS
        - len(str(_job_scratch_root(tmp_path, "a" * 32)))
        + 1,
    )
    if padding == 0:
        pytest.skip("this platform has no reachable working-directory ceiling here")
    scratch = _job_scratch_root(tmp_path, "a" * 32, depth_padding=padding)

    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            fixture.root, scratch_root=scratch, lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-scratch-invalid"


@requires_git
def test_hostile_source_configuration_neither_runs_nor_redirects_the_read(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    marker = tmp_path / "sentinel-ran.txt"
    sentinel = tmp_path / "sentinel.py"
    sentinel.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = f'"{Path(sys.executable).as_posix()}" "{sentinel.as_posix()}" "{marker.as_posix()}"'

    # Positive control: the sentinel really does run when something executes it.
    subprocess.run([sys.executable, str(sentinel), str(marker)], check=True)
    assert marker.read_text(encoding="utf-8") == "ran"
    marker.unlink()

    included = tmp_path / "included.config"
    included.write_text(
        "[core]\n"
        "\tbare = true\n"
        f"\tfsmonitor = {command}\n",
        encoding="utf-8",
    )
    config = fixture.root / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "[core]\n"
        + f"\tfsmonitor = {command}\n"
        + f"\tsshCommand = {command}\n"
        + f"\tpager = {command}\n"
        + "[diff]\n"
        + f"\texternal = {command}\n"
        + "[include]\n"
        + f"\tpath = {included.as_posix()}\n",
        encoding="utf-8",
    )

    before = snapshot_repository(fixture.root)
    # Ordinary Git work against this source really does run the hostile
    # fsmonitor command, including the parity snapshot above, so clear the
    # marker right before the capture: only the capture may be accused.
    marker.unlink(missing_ok=True)

    captured = _capture(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    )

    assert not marker.exists()
    assert [change.path for change in captured.files] == ["result.txt"]
    assert b"+after" in captured.patch
    assert snapshot_repository(fixture.root) == before


@requires_git
def test_source_hooks_never_run_during_a_capture(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    marker = tmp_path / "hook-ran.txt"
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    for name in (
        "pre-commit",
        "post-index-change",
        "post-checkout",
        "pre-auto-gc",
        "fsmonitor-watchman",
        "proc-receive",
    ):
        hook = hooks / name
        hook.write_text(
            f'#!/bin/sh\necho ran > "{marker.as_posix()}"\nexit 0\n',
            encoding="utf-8",
            newline="\n",
        )
        hook.chmod(0o755)
    git_command(fixture.root, "config", "core.hooksPath", str(hooks))

    # Positive control: these hooks really do run for an ordinary Git operation.
    (fixture.root / "hooked.txt").write_bytes(b"hooked\n")
    git_command(fixture.root, "add", "--", "hooked.txt")
    git_command(fixture.root, "commit", "-m", "test: exercise the hook")
    assert marker.read_text(encoding="utf-8").strip() == "ran"
    end = git_command(fixture.root, "rev-parse", "HEAD").decode().strip()

    before = snapshot_repository(fixture.root)
    # The parity snapshot itself touches the source index, which is exactly the
    # kind of ordinary Git work that may fire a hook; clear it right before the
    # capture so the sentinel can only accuse the capture.
    marker.unlink(missing_ok=True)

    captured = _capture(fixture.root, fixture.base_commit, end, tmp_path / "scratch")

    assert not marker.exists()
    assert [change.path for change in captured.files] == ["hooked.txt", "result.txt"]
    assert snapshot_repository(fixture.root) == before


@requires_git
def test_git_is_resolved_off_the_deployment_path_not_the_workspace(tmp_path, monkeypatch):
    fixture = create_git_repository(tmp_path / "repo")
    impostor = fixture.root / ("git.exe" if sys.platform == "win32" else "git")
    impostor.write_bytes(b"")
    monkeypatch.setenv("PATH", f"{fixture.root}{__import__('os').pathsep}.")
    with pytest.raises(GitEvidenceError) as failure:
        with open_git_repository(
            fixture.root, scratch_root=tmp_path / "scratch", lifecycle=_lifecycle()
        ):
            pass
    assert failure.value.code == "git-evidence-git-unavailable"


@requires_git
def test_git_subprocess_environment_drops_inherited_git_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "python -c pass")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("GIT_ASKPASS", "python")
    session = tmp_path / "session"
    env = git_module._git_environment(session, forbidden_roots=(session,))
    assert not any(
        name in env for name in ("GIT_SSH_COMMAND", "GIT_CONFIG_COUNT", "GIT_TRACE", "GIT_ASKPASS")
    )
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["HOME"] == str(session / "home")


def test_child_path_drops_every_untrusted_root_not_only_the_session(tmp_path, monkeypatch):
    # Git looks its own helpers up on the child PATH, so the entries it may
    # search must be the same forbidden-root set the executable lookup uses.
    workspace = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    session = scratch / "git-evidence-session"
    trusted = tmp_path / "deployment-bin"
    for directory in (workspace / "tools", scratch / "helpers", session, trusted):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            [
                str(workspace / "tools"),
                str(scratch / "helpers"),
                str(session),
                "relative/bin",
                str(trusted),
            ]
        ),
    )

    roots = git_module._untrusted_roots(workspace, session)
    env = git_module._git_environment(session, forbidden_roots=roots)

    assert [Path(entry) for entry in env["PATH"].split(os.pathsep) if entry] == [
        trusted.resolve()
    ]


@requires_git
def test_capture_child_path_never_offers_the_workspace_or_scratch_root(tmp_path, monkeypatch):
    fixture = create_git_repository(tmp_path / "repo")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    observed: list[str] = []
    real_run_supervised = git_module.run_supervised

    def recording(argv, **kwargs):
        observed.append(kwargs["env"]["PATH"])
        return real_run_supervised(argv, **kwargs)

    monkeypatch.setattr(git_module, "run_supervised", recording)
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(fixture.root), str(scratch), os.environ["PATH"]])
    )

    captured = _capture(fixture.root, fixture.base_commit, fixture.end_commit, scratch)

    assert [change.path for change in captured.files] == ["result.txt"]
    assert observed
    for value in observed:
        for entry in (Path(part) for part in value.split(os.pathsep) if part):
            assert not entry.is_relative_to(fixture.root)
            assert not entry.is_relative_to(scratch)


@requires_git
def test_git_reads_reserve_a_bounded_stderr_allowance_beyond_the_stdout_limit(
    tmp_path, monkeypatch
):
    # A patch that exactly fills its byte budget must not be rejected because
    # Git also printed a harmless warning.
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    recorded: dict[str, object] = {}
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:

        def fake_run(argv, **kwargs):
            recorded.update(kwargs)
            return _completed("exited", stdout=b"x" * 64, stderr=b"warning: noisy\n")

        monkeypatch.setattr(git_module, "run_supervised", fake_run)
        data = run_git_bytes(
            repository,
            ("cat-file", "-t", fixture.end_commit),
            lifecycle=lifecycle,
            deadline=time.monotonic() + 30,
            output_limit=64,
        )
    assert data == b"x" * 64
    assert recorded["output_limit_bytes"] == 64 + git_module.STDERR_ALLOWANCE_BYTES


@requires_git
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_git_reads_bound_each_stream_even_without_a_combined_overflow(
    tmp_path, monkeypatch, stream
):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    oversized = {
        "stdout": {"stdout": b"x" * 65},
        "stderr": {"stderr": b"y" * (git_module.STDERR_ALLOWANCE_BYTES + 1)},
    }[stream]
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        monkeypatch.setattr(
            git_module,
            "run_supervised",
            lambda argv, **kwargs: _completed("exited", **oversized),
        )
        with pytest.raises(GitEvidenceError) as failure:
            run_git_bytes(
                repository,
                ("cat-file", "-t", fixture.end_commit),
                lifecycle=lifecycle,
                deadline=time.monotonic() + 30,
                output_limit=64,
            )
    assert failure.value.code == "git-evidence-output-too-large"


@requires_git
def test_a_read_that_exactly_fills_its_stdout_limit_is_accepted(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = _lifecycle()
    arguments = ("cat-file", "-p", fixture.end_commit)
    with open_git_repository(
        fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle
    ) as repository:
        exact = run_git_bytes(
            repository,
            arguments,
            lifecycle=lifecycle,
            deadline=time.monotonic() + 30,
            output_limit=64 * 1024,
        )
        at_limit = run_git_bytes(
            repository,
            arguments,
            lifecycle=lifecycle,
            deadline=time.monotonic() + 30,
            output_limit=len(exact),
        )
        with pytest.raises(GitEvidenceError) as failure:
            run_git_bytes(
                repository,
                arguments,
                lifecycle=lifecycle,
                deadline=time.monotonic() + 30,
                output_limit=len(exact) - 1,
            )
    assert at_limit == exact
    assert failure.value.code == "git-evidence-output-too-large"


@requires_git
def test_local_publication_accepts_unpushed_commits(tmp_path):
    import time
    from datetime import datetime, timezone

    from flowgency.git_evidence import publication
    from flowgency.git_evidence.capture import capture_committed_range
    from flowgency.git_evidence.git import open_git_repository
    from flowgency.git_evidence.models import GitCommitRange, GitPublicationPolicy
    from flowgency.integrations.models import EffectiveRuntimePolicy
    from flowgency.jobs.processes import RuntimeProcessLifecycle
    from tests._git_evidence_helpers import create_git_repository

    fixture = create_git_repository(tmp_path / "repo")
    lifecycle = RuntimeProcessLifecycle(job_id="capture-test", generation="local-only")
    with open_git_repository(fixture.root, scratch_root=tmp_path / "scratch", lifecycle=lifecycle) as repository:
        deadline = time.monotonic() + 120
        captured = capture_committed_range(
            repository, GitCommitRange(fixture.base_commit, fixture.end_commit),
            policies=(EffectiveRuntimePolicy(timeout=30),), lifecycle=lifecycle, deadline=deadline,
        )
        receipt = publication.verify_publication(
            repository, captured, GitPublicationPolicy(mode="local"),
            publication_ref=None,
            lifecycle=lifecycle, deadline=deadline, now=datetime.now(timezone.utc),
        )
    assert receipt.mode == "local"
    assert receipt.observed_commit == fixture.end_commit


@requires_git
def test_an_unrestricted_local_policy_proves_the_range_without_a_ref(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    policy = GitPublicationPolicy(mode="local")
    with _source_unchanged(fixture.root):
        with _publication_context(
            fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
        ) as context:
            receipt = _verify(context, policy, publication_ref=None)
            with_ref = _verify(context, policy, publication_ref="refs/heads/main")
    assert receipt.mode == "local"
    assert receipt.observed_commit == fixture.end_commit
    assert receipt.publication_ref is None
    assert receipt.ref_object_id is None
    assert receipt.verified_at == _VERIFIED_AT
    assert receipt.policy_digest == git_policy_digest(policy)
    # A valid supplied ref is proven, not rejected, when nothing restricts it.
    assert with_ref.publication_ref == "refs/heads/main"
    assert with_ref.ref_object_id == fixture.end_commit
    assert with_ref.observed_commit == fixture.end_commit


@requires_git
def test_an_unrestricted_local_policy_rejects_a_ref_that_does_not_contain_the_end(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "branch", "before", fixture.base_commit)
    policy = GitPublicationPolicy(mode="local")
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(context, policy, publication_ref="refs/heads/before")
    assert failure.value.code == "git-evidence-not-published"


@requires_git
@pytest.mark.parametrize(
    "requested",
    ["main", "refs/remotes/origin/main", "refs/heads/ma?n", "refs/heads/../../evil"],
)
def test_an_unrestricted_local_policy_still_rejects_malformed_or_tracking_refs(
    tmp_path, requested
):
    fixture = create_git_repository(tmp_path / "repo")
    policy = GitPublicationPolicy(mode="local")
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(context, policy, publication_ref=requested)
    assert failure.value.code == "git-evidence-publication-ref-denied"


@requires_git
def test_local_publication_can_require_an_allowed_ref(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    policy = GitPublicationPolicy(mode="local", allowed_refs=("refs/heads/main",))
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        receipt = _verify(context, policy, publication_ref="refs/heads/main")
        with pytest.raises(GitEvidenceError) as missing:
            _verify(context, policy, publication_ref=None)
    assert receipt.mode == "local"
    assert receipt.publication_ref == "refs/heads/main"
    assert receipt.ref_object_id == fixture.end_commit
    assert receipt.observed_commit == fixture.end_commit
    assert missing.value.code == "git-evidence-publication-ref-required"


@requires_git
def test_local_publication_preserves_an_annotated_tag_and_its_commit(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "tag", "-a", "v1", "-m", "test: release one")
    tag_object = git_command(fixture.root, "rev-parse", "refs/tags/v1").decode().strip()
    assert tag_object != fixture.end_commit
    policy = GitPublicationPolicy(mode="local", allowed_refs=("refs/tags/*",))
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        receipt = _verify(context, policy, publication_ref="refs/tags/v1")
    assert receipt.ref_object_id == tag_object
    assert receipt.observed_commit == fixture.end_commit


@requires_git
@pytest.mark.parametrize(
    "requested",
    [
        "refs/heads/other",
        "refs/heads/*",
        "refs/heads/ma?n",
        "refs/heads/ma[i]n",
        "refs/tags/v1",
        "main",
        "refs/heads/../../evil",
    ],
)
def test_local_publication_rejects_a_ref_outside_the_allowlist(tmp_path, requested):
    fixture = create_git_repository(tmp_path / "repo")
    policy = GitPublicationPolicy(mode="local", allowed_refs=("refs/heads/main",))
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(context, policy, publication_ref=requested)
    assert failure.value.code == "git-evidence-publication-ref-denied"


@requires_git
def test_a_missing_publication_policy_is_an_actionable_configuration_error(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(context, None, publication_ref=None)
    assert failure.value.code == "git-evidence-publication-policy-missing"


def _refs_dir(fixture) -> Path:
    return fixture.root / ".git" / "refs" / "heads"


def _restricted(*allowed_refs: str) -> GitPublicationPolicy:
    return GitPublicationPolicy(mode="local", allowed_refs=allowed_refs)


@requires_git
def test_a_receipt_records_only_local_observations():
    names = [field.name for field in dataclasses.fields(GitPublicationReceipt)]
    assert names == [
        "mode",
        "policy_digest",
        "publication_ref",
        "ref_object_id",
        "observed_commit",
        "verified_at",
    ]


@requires_git
def test_a_packed_ref_is_resolved_without_unpacking_it(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "pack-refs", "--all")
    assert not (_refs_dir(fixture) / "main").exists()

    with _source_unchanged(fixture.root):
        with _publication_context(
            fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
        ) as context:
            receipt = _verify(
                context, _restricted("refs/heads/main"), publication_ref="refs/heads/main"
            )
    assert receipt.observed_commit == fixture.end_commit
    assert not (_refs_dir(fixture) / "main").exists()


@requires_git
def test_an_absent_local_ref_is_not_published(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/*"), publication_ref="refs/heads/absent"
            )
    assert failure.value.code == "git-evidence-not-published"
    assert "push" not in failure.value.message.lower()


@requires_git
def test_a_lightweight_tag_is_its_own_ref_object_and_commit(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "tag", "v1-light")
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        receipt = _verify(
            context, _restricted("refs/tags/*"), publication_ref="refs/tags/v1-light"
        )
    assert receipt.ref_object_id == fixture.end_commit
    assert receipt.observed_commit == fixture.end_commit


@requires_git
def test_a_trailing_prefix_allowlist_never_authorizes_the_prefix_itself(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "update-ref", "refs/heads/topic/one", fixture.end_commit)
    # A sibling name, because Git refuses a ref that is also a directory.
    git_command(fixture.root, "update-ref", "refs/heads/feature", fixture.end_commit)
    policy = _restricted("refs/heads/topic/*", "refs/heads/feature/*")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        allowed = _verify(context, policy, publication_ref="refs/heads/topic/one")
        with pytest.raises(GitEvidenceError) as denied:
            _verify(context, policy, publication_ref="refs/heads/feature")
    assert allowed.publication_ref == "refs/heads/topic/one"
    assert denied.value.code == "git-evidence-publication-ref-denied"


@requires_git
def test_an_end_commit_ahead_of_the_allowed_ref_is_not_published(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "update-ref", "refs/heads/release", fixture.base_commit)
    with _source_unchanged(fixture.root):
        with _publication_context(
            fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
        ) as context:
            with pytest.raises(GitEvidenceError) as failure:
                _verify(
                    context,
                    _restricted("refs/heads/release"),
                    publication_ref="refs/heads/release",
                )
    assert failure.value.code == "git-evidence-not-published"


@requires_git
def test_an_end_commit_contained_by_a_later_ref_tip_is_proven(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (fixture.root / "later.txt").write_bytes(b"later\n")
    git_command(fixture.root, "add", "--", "later.txt")
    git_command(fixture.root, "commit", "-m", "test: advance past the evidence")
    later = git_command(fixture.root, "rev-parse", "HEAD").decode().strip()

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        receipt = _verify(
            context, _restricted("refs/heads/main"), publication_ref="refs/heads/main"
        )
    assert receipt.observed_commit == later
    assert receipt.observed_commit != fixture.end_commit


@requires_git
def test_an_unrelated_ref_tip_is_not_published(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    unrelated = (
        git_command(
            fixture.root,
            "commit-tree",
            git_command(fixture.root, "mktree", "-z", stdin=b"").decode().strip(),
            "-m",
            "test: unrelated root commit",
        )
        .decode()
        .strip()
    )
    git_command(fixture.root, "update-ref", "refs/heads/unrelated", unrelated)

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context,
                _restricted("refs/heads/unrelated"),
                publication_ref="refs/heads/unrelated",
            )
    assert failure.value.code == "git-evidence-not-published"


@requires_git
def test_a_ref_whose_object_is_missing_is_incomplete_not_fetched(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    absent_object = "0123456789abcdef" * 2 + "01234567"
    (_refs_dir(fixture) / "orphan").write_text(f"{absent_object}\n", encoding="utf-8")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context,
                _restricted("refs/heads/orphan"),
                publication_ref="refs/heads/orphan",
            )
    assert failure.value.code == "git-evidence-verification-incomplete"


@requires_git
def test_a_symbolic_ref_is_refused_rather_than_followed(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (_refs_dir(fixture) / "alias").write_text("ref: refs/heads/main\n", encoding="utf-8")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/alias"), publication_ref="refs/heads/alias"
            )
    assert failure.value.code == "git-evidence-verification-incomplete"


@requires_git
def test_a_ref_entry_leaving_the_repository_is_refused_not_read(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main").write_text(f"{fixture.end_commit}\n", encoding="utf-8")
    try:
        (_refs_dir(fixture) / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context,
                _restricted("refs/heads/escape/*"),
                publication_ref="refs/heads/escape/main",
            )
    assert failure.value.code == "git-evidence-publication-ref-denied"


@requires_git
def test_a_ref_aliased_within_refs_is_refused_not_followed(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    # The decoy resolves to the exact captured end commit, so following the
    # alias would let the policy appear satisfied without proving the
    # *requested* name ever pointed there.
    (_refs_dir(fixture) / "decoy").write_text(f"{fixture.end_commit}\n", encoding="utf-8")
    try:
        (_refs_dir(fixture) / "alias").symlink_to(_refs_dir(fixture) / "decoy")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/alias"), publication_ref="refs/heads/alias"
            )
    assert failure.value.code == "git-evidence-publication-ref-denied"


@requires_git
def test_a_linked_packed_refs_file_is_refused_not_read(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "pack-refs", "--all")
    packed = fixture.root / ".git" / "packed-refs"
    forged = fixture.root / ".git" / "packed-refs-forged"
    forged.write_bytes(packed.read_bytes())
    packed.unlink()
    try:
        packed.symlink_to(forged)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/main"), publication_ref="refs/heads/main"
            )
    assert failure.value.code == "git-evidence-publication-ref-denied"


@requires_git
@pytest.mark.parametrize("directory", ("refs", "refs/heads"))
def test_a_linked_ref_directory_is_refused(tmp_path, directory):
    fixture = create_git_repository(tmp_path / "repo")
    linked = fixture.root / ".git" / directory
    target = linked.with_name(f"{linked.name}-real")
    linked.rename(target)
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/main"),
                publication_ref="refs/heads/main",
            )
    assert failure.value.code == "git-evidence-publication-ref-denied"


@requires_git
def test_packed_refs_that_grow_past_the_bound_are_refused_not_parsed(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    git_command(fixture.root, "pack-refs", "--all")
    packed = fixture.root / ".git" / "packed-refs"

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        packed.write_bytes(_packed_refs_bytes(fixture, PACKED_REFS_LIMIT_BYTES + 1))
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/main"), publication_ref="refs/heads/main"
            )
    assert failure.value.code == "git-evidence-verification-incomplete"


@requires_git
def test_an_oversized_loose_ref_is_refused_not_read_as_its_prefix(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    (_refs_dir(fixture) / "bloated").write_bytes(
        fixture.end_commit.encode() + b" " * LOOSE_REF_LIMIT_BYTES
    )

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context,
                _restricted("refs/heads/bloated"),
                publication_ref="refs/heads/bloated",
            )
    assert failure.value.code == "git-evidence-verification-incomplete"


_TRANSPORT_SUBCOMMANDS = frozenset(
    {"ls-remote", "fetch", "push", "clone", "remote", "pull", "archive", "submodule"}
)


@requires_git
def test_local_verification_runs_no_transport_helper_or_credential_program(
    tmp_path, monkeypatch
):
    fixture = create_git_repository(tmp_path / "repo")
    markers = tmp_path / "markers"
    markers.mkdir()
    sentinel = tmp_path / "sentinel.py"
    sentinel.write_text(
        "import pathlib, sys\n"
        "(pathlib.Path(sys.argv[1]) / sys.argv[2]).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    invocation = f'"{Path(sys.executable).as_posix()}" "{sentinel.as_posix()}" "{markers.as_posix()}"'
    # Positive control: the sentinel really does leave a marker when it runs.
    subprocess.run(
        [sys.executable, str(sentinel), str(markers), "control"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert (markers / "control").is_file()

    git_command(fixture.root, "config", "credential.helper", f"!{invocation} credential")
    git_command(fixture.root, "config", "core.sshCommand", f"{invocation} ssh")
    git_command(fixture.root, "remote", "add", "origin", str(tmp_path / "absent.git"))
    git_command(fixture.root, "update-ref", "refs/remotes/origin/main", fixture.base_commit)
    for name in ("SSH_AUTH_SOCK", "GIT_ASKPASS", "GIT_SSH_COMMAND", "GIT_SSH"):
        monkeypatch.setenv(name, f"{invocation} ambient-{name.lower()}")

    invoked: list[tuple[str, ...]] = []
    real_run = publication_module.run_git_exit_code

    def record(repository, arguments, **keywords):
        invoked.append(tuple(arguments))
        return real_run(repository, arguments, **keywords)

    monkeypatch.setattr(publication_module, "run_git_exit_code", record)
    with _source_unchanged(fixture.root):
        with _publication_context(
            fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
        ) as context:
            receipt = _verify(
                context, _restricted("refs/heads/main"), publication_ref="refs/heads/main"
            )

    assert receipt.observed_commit == fixture.end_commit
    # Positive control for the observer itself: real Git reads were recorded.
    assert any(arguments[0] == "cat-file" for arguments in invoked)
    assert not any(set(arguments) & _TRANSPORT_SUBCOMMANDS for arguments in invoked)
    assert sorted(entry.name for entry in markers.iterdir()) == ["control"]


@requires_git
def test_a_receipt_is_fixed_at_the_state_it_observed(tmp_path):
    fixture = create_git_repository(tmp_path / "repo")
    policy = _restricted("refs/heads/main")
    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        first = _verify(context, policy, publication_ref="refs/heads/main")
        again = _verify(context, policy, publication_ref="refs/heads/main")
    assert first == again

    (fixture.root / "later.txt").write_bytes(b"later\n")
    git_command(fixture.root, "add", "--", "later.txt")
    git_command(fixture.root, "commit", "-m", "test: move the local ref onward")
    later = git_command(fixture.root, "rev-parse", "HEAD").decode().strip()

    with _publication_context(
        fixture.root, fixture.base_commit, later, tmp_path / "scratch-later"
    ) as context:
        after = _verify(context, policy, publication_ref="refs/heads/main")

    assert first.observed_commit == fixture.end_commit
    assert first.ref_object_id == fixture.end_commit
    assert after.observed_commit == later
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.observed_commit = later


# -- bounded presentation of a retained patch --------------------------------


def _retained_manifest(capture):
    """The manifest a real capture would be retained as, without a ticket."""
    import base64
    import hashlib

    from flowgency.tickets.git_evidence import GitEvidenceManifest, GitFileEntry

    policy = GitPublicationPolicy(mode="local")
    digest = git_policy_digest(policy)
    receipt = GitPublicationReceipt(
        mode="local",
        policy_digest=digest,
        publication_ref=None,
        ref_object_id=None,
        observed_commit=capture.end_commit,
        verified_at=_VERIFIED_AT,
    )
    return GitEvidenceManifest(
        schema_version=1,
        repository_id=capture.repository_id,
        workspace_identity="a" * 64,
        base_commit=capture.base_commit,
        end_commit=capture.end_commit,
        commit_ids=capture.commit_ids,
        files=tuple(GitFileEntry.from_change(change) for change in capture.files),
        patch_b64=base64.b64encode(capture.patch).decode("ascii"),
        patch_sha256=hashlib.sha256(capture.patch).hexdigest(),
        team_id="newsletter",
        workflow_id="board-a",
        ticket_id="ticket-1",
        binding_id="b" * 64,
        agent_name="builder",
        job_id="job-1",
        captured_at=_VERIFIED_AT,
        policy_digest=digest,
        policy_snapshot=policy,
        publication=receipt,
    )


def _committed_manifest(tmp_path, name: str, content: bytes):
    root = tmp_path / "preview-source"
    init_git_repository(root)
    (root / name).write_bytes(b"before\n")
    git_command(root, "add", "--", name)
    git_command(root, "commit", "-m", "test: seed")
    base = git_command(root, "rev-parse", "HEAD").decode().strip()
    (root / name).write_bytes(content)
    git_command(root, "add", "--", name)
    git_command(root, "commit", "-m", "test: change")
    end = git_command(root, "rev-parse", "HEAD").decode().strip()
    return _retained_manifest(_capture(root, base, end, tmp_path / "scratch-preview"))


@requires_git
def test_preview_rows_carry_line_numbers_and_a_no_newline_marker(tmp_path):
    from flowgency.web.git_evidence import parse_git_diff

    manifest = _committed_manifest(tmp_path, "result.txt", b"after")

    preview = parse_git_diff(manifest)

    assert preview.unavailable_reason is None
    assert preview.omitted is False
    assert len(preview.files) == 1
    assert preview.files[0].anchor == "change-0"
    assert preview.files[0].change.path == "result.txt"
    kinds = [line.kind for line in preview.files[0].lines]
    assert "removed" in kinds and "added" in kinds
    removed = next(line for line in preview.files[0].lines if line.kind == "removed")
    added = next(line for line in preview.files[0].lines if line.kind == "added")
    assert (removed.old_line, removed.new_line) == (1, None)
    assert (added.old_line, added.new_line) == (None, 1)
    assert kinds[-1] == "marker"


@requires_git
def test_a_non_utf8_patch_is_unshowable_but_never_altered(tmp_path):
    from flowgency.web.git_evidence import PREVIEW_UNAVAILABLE_ENCODING, parse_git_diff

    manifest = _committed_manifest(tmp_path, "encoded.txt", b"\xff\xfe latin bytes\n")

    preview = parse_git_diff(manifest)

    assert preview.unavailable_reason == PREVIEW_UNAVAILABLE_ENCODING
    assert preview.files == ()
    assert b"\xff\xfe latin bytes" in manifest.patch


@requires_git
def test_a_hunk_beyond_the_display_budget_is_omitted_whole(tmp_path):
    from flowgency.web.git_evidence import parse_git_diff

    manifest = _committed_manifest(tmp_path, "result.txt", b"after\n")

    bounded = parse_git_diff(manifest, max_lines=1)
    complete = parse_git_diff(manifest)

    assert bounded.omitted is True
    assert bounded.files[0].lines == ()
    assert complete.omitted is False
    assert complete.files[0].lines != ()


@requires_git
def test_an_empty_range_is_no_net_change_not_an_omission(tmp_path):
    from flowgency.web.git_evidence import parse_git_diff

    fixture = create_git_repository(tmp_path / "empty-range")
    capture = _capture(
        fixture.root, fixture.end_commit, fixture.end_commit, tmp_path / "scratch-empty"
    )

    preview = parse_git_diff(_retained_manifest(capture))

    assert preview.files == ()
    assert preview.omitted is False
    assert preview.unavailable_reason is None


@requires_git
def test_binary_metadata_comes_from_the_manifest_not_the_parser(tmp_path):
    from flowgency.web.git_evidence import parse_git_diff

    manifest = _committed_manifest(tmp_path, "blob.bin", b"\x00\x01\x02\x00binary\n")

    preview = parse_git_diff(manifest)

    assert len(preview.files) == 1
    assert preview.files[0].change.binary is True
    assert preview.files[0].change.lines_added == 0
    assert preview.files[0].lines == ()


def _tree_without(root: Path, commit: str, name: bytes) -> bytes:
    rows = [row for row in read_tree_entries(root, commit).split(b"\0") if row]
    return b"".join(row + b"\0" for row in rows if not row.endswith(b"\t" + name))


@requires_git
def test_quoted_header_paths_still_show_their_own_rows(tmp_path):
    """Git C-quotes Unicode and tab names in patch headers; rows must survive."""
    from flowgency.web.git_evidence import parse_git_diff

    fixture = create_git_repository(tmp_path / "quoted")
    blob = write_blob(fixture.root, b"exotic content\n")
    entries = read_tree_entries(fixture.root, fixture.end_commit)
    entries += tree_entry("100644", "blob", blob, "žluť.txt".encode())
    entries += tree_entry("100644", "blob", blob, b"with\ttab.txt")
    end = commit_tree(fixture.root, entries, parent=fixture.end_commit)
    manifest = _retained_manifest(
        _capture(fixture.root, fixture.end_commit, end, tmp_path / "scratch-quoted")
    )
    # Positive control: the fixture really does carry quoted header paths.
    assert b'"b/' in manifest.patch

    preview = parse_git_diff(manifest)

    rows = {
        entry.change.path: [line.text for line in entry.lines] for entry in preview.files
    }
    assert preview.unavailable_reason is None
    assert preview.omitted is False
    assert rows["žluť.txt"] == ["exotic content\n"]
    assert rows["with\ttab.txt"] == ["exotic content\n"]


@requires_git
def test_a_type_change_shows_both_halves_it_really_contains(tmp_path):
    """Git writes a type change as a /dev/null delete plus a /dev/null add."""
    from flowgency.web.git_evidence import parse_git_diff

    fixture = create_git_repository(tmp_path / "typechange")
    link = write_blob(fixture.root, b"../../outside-target")
    entries = _tree_without(fixture.root, fixture.end_commit, b"result.txt")
    entries += tree_entry("120000", "blob", link, b"result.txt")
    end = commit_tree(fixture.root, entries, parent=fixture.end_commit)
    manifest = _retained_manifest(
        _capture(fixture.root, fixture.end_commit, end, tmp_path / "scratch-type")
    )
    assert [entry.status for entry in manifest.files] == ["type-changed"]

    preview = parse_git_diff(manifest)

    text = "".join(line.text for line in preview.files[0].lines)
    assert "after" in text
    assert "../../outside-target" in text
    assert preview.omitted is False
    assert preview.unavailable_reason is None


@requires_git
def test_a_rename_beside_an_added_file_keeps_each_files_own_rows(tmp_path):
    """A renamed path and an unrelated added path never borrow each other's rows."""
    from flowgency.web.git_evidence import parse_git_diff

    root = tmp_path / "rename"
    init_git_repository(root)
    shared = b"".join(b"shared line %d\n" % index for index in range(10))
    (root / "notes.txt").write_bytes(shared)
    git_command(root, "add", "--", "notes.txt")
    git_command(root, "commit", "-m", "test: seed notes")
    base = git_command(root, "rev-parse", "HEAD").decode().strip()
    (root / "notes.txt").unlink()
    (root / "archive.txt").write_bytes(b"archived heading\n" + shared)
    (root / "fresh.txt").write_bytes(b"fresh replacement\n")
    git_command(root, "add", "--all", "--", "notes.txt", "archive.txt", "fresh.txt")
    git_command(root, "commit", "-m", "test: rename and add")
    end = git_command(root, "rev-parse", "HEAD").decode().strip()
    manifest = _retained_manifest(_capture(root, base, end, tmp_path / "scratch-rename"))
    assert {entry.path: entry.status for entry in manifest.files} == {
        "archive.txt": "renamed",
        "fresh.txt": "added",
    }

    preview = parse_git_diff(manifest)

    rows = {
        entry.change.path: "".join(line.text for line in entry.lines)
        for entry in preview.files
    }
    assert "fresh replacement" in rows["fresh.txt"]
    assert "archived heading" not in rows["fresh.txt"]
    assert "archived heading" in rows["archive.txt"]
    assert preview.unavailable_reason is None


@requires_git
def test_unparseable_patch_content_is_never_shown_as_an_empty_diff(tmp_path):
    from flowgency.web.git_evidence import PREVIEW_UNAVAILABLE_FORMAT, parse_git_diff

    capture = _capture(
        *_seeded_range(tmp_path, "unparseable"), tmp_path / "scratch-unparseable"
    )
    hostile = b"@@ -1 +1 @@\n-before\n+after\n"
    manifest = _retained_manifest(dataclasses.replace(capture, patch=hostile))

    preview = parse_git_diff(manifest)

    assert preview.unavailable_reason == PREVIEW_UNAVAILABLE_FORMAT
    assert preview.files == ()
    # The exact retained bytes stay available for download.
    assert manifest.patch == hostile


@requires_git
def test_rows_that_belong_to_no_manifest_file_are_reported_as_omitted(tmp_path):
    from flowgency.web.git_evidence import parse_git_diff

    capture = _capture(
        *_seeded_range(tmp_path, "unattributed"), tmp_path / "scratch-unattributed"
    )
    stranger = (
        b"diff --git a/stranger.txt b/stranger.txt\n"
        b"--- a/stranger.txt\n"
        b"+++ b/stranger.txt\n"
        b"@@ -1 +1 @@\n"
        b"-before\n"
        b"+after\n"
    )
    manifest = _retained_manifest(dataclasses.replace(capture, patch=stranger))

    preview = parse_git_diff(manifest)

    assert [entry.change.path for entry in preview.files] == ["result.txt"]
    assert preview.files[0].lines == ()
    # A file the patch never described must not read as a clean, empty change.
    assert preview.omitted is True


def _seeded_range(tmp_path, name: str):
    fixture = create_git_repository(tmp_path / name)
    return fixture.root, fixture.base_commit, fixture.end_commit

