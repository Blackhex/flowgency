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
from pathlib import Path

import pytest
from pydantic import ValidationError

from flowgency.git_evidence import capture as capture_module
from flowgency.git_evidence import git as git_module
from flowgency.git_evidence.capture import capture_committed_range
from flowgency.git_evidence.git import (
    open_git_repository,
    run_git_bytes,
    verify_repository_identity,
)
from flowgency.git_evidence.models import (
    GitCommitRange,
    GitEvidenceError,
    GitPublicationPolicy,
    GitRemotePolicy,
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


def test_local_git_policy_needs_no_remote():
    policy = GitPublicationPolicy(mode="local")
    assert policy.remote is None
    assert policy.allowed_refs == ()


def test_remote_git_policy_cannot_omit_publication_destination():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(mode="remote")


def test_remote_git_policy_requires_allowed_refs():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="remote",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        )


def test_local_git_policy_rejects_a_remote():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="local",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        )


def test_remote_git_policy_rejects_duplicate_allowed_refs():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="remote",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
            allowed_refs=("refs/heads/main", "refs/heads/main"),
        )


def test_https_remote_accepts_anonymous_and_credential_manager():
    for auth in ("anonymous", "credential-manager"):
        policy = GitRemotePolicy(name="origin", url="https://example.com/repo.git", auth=auth)
        assert policy.auth == auth


def test_ssh_remote_requires_ssh_agent_and_known_hosts():
    policy = GitRemotePolicy(
        name="origin",
        url="ssh://git@example.com/repo.git",
        auth="ssh-agent",
        known_hosts="known_hosts",
    )
    assert policy.auth == "ssh-agent"
    assert str(policy.known_hosts) == "known_hosts"


def test_file_remote_accepts_absolute_path_anonymous():
    policy = GitRemotePolicy(name="origin", url="file:///abs/repo.git")
    assert policy.auth == "anonymous"


@pytest.mark.parametrize(
    "url,auth,known_hosts",
    [
        ("ssh://git@example.com/repo.git", "anonymous", None),
        ("ssh://git@example.com/repo.git", "ssh-agent", None),
        ("ssh://git:pass@example.com/repo.git", "ssh-agent", "known_hosts"),
        ("https://example.com/repo.git", "ssh-agent", None),
        ("https://user@example.com/repo.git", "anonymous", None),
        ("https://user:pass@example.com/repo.git", "anonymous", None),
        ("https://example.com/repo.git?x=1", "anonymous", None),
        ("https://example.com/repo.git#frag", "anonymous", None),
        ("git://example.com/repo.git", "anonymous", None),
        ("ext::sh -c 'touch pwned'", "anonymous", None),
        ("file:relative/repo.git", "anonymous", None),
        ("file:///abs/repo.git", "credential-manager", None),
        ("file:///abs/repo.git", "ssh-agent", None),
        ("https://example.com/repo.git", "anonymous", "known_hosts"),
        ("file://server/share/repo.git", "anonymous", None),
        ("file://localhost/abs/repo.git", "anonymous", None),
    ],
)
def test_invalid_remote_url_combinations_are_rejected(url, auth, known_hosts):
    with pytest.raises(ValidationError):
        GitRemotePolicy(name="origin", url=url, auth=auth, known_hosts=known_hosts)


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
        GitPublicationPolicy(
            mode="remote",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
            allowed_refs=(ref,),
        )


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
    policy = GitPublicationPolicy(
        mode="remote",
        remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        allowed_refs=(ref,),
    )
    assert policy.allowed_refs == (ref,)


def test_remote_name_rejects_unsafe_subsection():
    with pytest.raises(ValidationError):
        GitRemotePolicy(name='bad"name', url="https://example.com/repo.git")


def test_git_policy_digest_is_none_for_missing_policy():
    assert git_policy_digest(None) is None


def test_git_policy_digest_is_stable_across_equivalent_construction():
    policy_a = GitPublicationPolicy(
        mode="remote",
        remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        allowed_refs=("refs/heads/main", "refs/tags/v1"),
    )
    policy_b = GitPublicationPolicy.model_validate(policy_a.model_dump(mode="json"))
    assert git_policy_digest(policy_a) == git_policy_digest(policy_b)


def test_git_policy_digest_changes_with_policy():
    local = GitPublicationPolicy(mode="local")
    remote = GitPublicationPolicy(
        mode="remote",
        remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        allowed_refs=("refs/heads/main",),
    )
    assert git_policy_digest(local) != git_policy_digest(remote)


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
