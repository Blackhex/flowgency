"""Tests for the integration-agnostic git-status changed-file capture.

Copilot reports per-file edits natively; every other integration relies on the
git-status fallback in ``agency.jobs.changes`` so decision outcomes are visible
for all nine tools, not just one.
"""

import subprocess
from pathlib import Path

import pytest

from agency.integrations import FileChange
from agency.jobs.changes import capture_base_sha, capture_git_changes


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "tracked.txt").write_text("line1\nline2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git binary not available")


def test_capture_none_root_returns_empty():
    assert capture_git_changes(None) == []


def test_capture_missing_root_returns_empty(tmp_path):
    assert capture_git_changes(tmp_path / "does-not-exist") == []


def test_capture_non_repo_returns_empty(tmp_path):
    (tmp_path / "file.txt").write_text("hi", encoding="utf-8")
    assert capture_git_changes(tmp_path) == []


def test_capture_clean_repo_returns_empty(tmp_path):
    _init_repo(tmp_path)
    assert capture_git_changes(tmp_path) == []


def test_capture_reports_untracked_file_as_added(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("brand new\n", encoding="utf-8")

    changes = capture_git_changes(tmp_path)

    assert FileChange("new.txt", "added", 0, 0) in changes


def test_capture_reports_modified_file_with_line_counts(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("line1\nchanged\nadded\n", encoding="utf-8")

    changes = capture_git_changes(tmp_path)

    modified = [c for c in changes if c.path == "tracked.txt"]
    assert len(modified) == 1
    change = modified[0]
    assert change.status == "modified"
    assert change.lines_added == 2
    assert change.lines_removed == 1


def test_capture_reports_deleted_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").unlink()

    changes = capture_git_changes(tmp_path)

    deleted = [c for c in changes if c.path == "tracked.txt"]
    assert len(deleted) == 1
    assert deleted[0].status == "deleted"


def test_capture_returns_file_change_instances(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "extra.txt").write_text("x\n", encoding="utf-8")

    changes = capture_git_changes(tmp_path)

    assert changes
    assert all(isinstance(c, FileChange) for c in changes)


def _head_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_capture_base_sha_returns_head(tmp_path):
    _init_repo(tmp_path)
    assert capture_base_sha(tmp_path) == _head_sha(tmp_path)


def test_capture_base_sha_none_root_returns_none():
    assert capture_base_sha(None) is None


def test_capture_base_sha_non_repo_returns_none(tmp_path):
    assert capture_base_sha(tmp_path) is None


def test_capture_sees_committed_work_when_tree_clean(tmp_path):
    """A compliant agent commits every atomic change, leaving a clean working
    tree. With a recorded base_sha the capture must still report the committed
    files — the working-tree-only view would return []."""
    _init_repo(tmp_path)
    base_sha = capture_base_sha(tmp_path)

    (tmp_path / "feature.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "atomic change")

    # Working tree is clean, so the superseded path sees nothing.
    assert capture_git_changes(tmp_path) == []

    changes = {c.path: c for c in capture_git_changes(tmp_path, base_sha)}
    assert set(changes) == {"feature.py", "tracked.txt"}
    assert changes["feature.py"].status == "added"
    assert changes["tracked.txt"].status == "modified"
    assert changes["tracked.txt"].lines_added == 1
    assert changes["tracked.txt"].lines_removed == 0


def test_capture_sees_committed_deletion(tmp_path):
    _init_repo(tmp_path)
    base_sha = capture_base_sha(tmp_path)

    (tmp_path / "tracked.txt").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "remove tracked")

    changes = {c.path: c for c in capture_git_changes(tmp_path, base_sha)}
    assert changes["tracked.txt"].status == "deleted"


def test_capture_unions_committed_and_working_tree(tmp_path):
    _init_repo(tmp_path)
    base_sha = capture_base_sha(tmp_path)

    (tmp_path / "committed.py").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "committed file")
    (tmp_path / "dirty.py").write_text("uncommitted\n", encoding="utf-8")

    changes = {c.path: c for c in capture_git_changes(tmp_path, base_sha)}
    assert set(changes) == {"committed.py", "dirty.py"}
    assert changes["committed.py"].status == "added"
    assert changes["dirty.py"].status == "added"


def test_capture_working_tree_wins_on_conflict(tmp_path):
    """A file committed during the run and then edited again reflects its latest
    working-tree state, not the committed snapshot."""
    _init_repo(tmp_path)
    base_sha = capture_base_sha(tmp_path)

    (tmp_path / "tracked.txt").write_text("line1\nline2\ncommitted\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "commit edit")
    (tmp_path / "tracked.txt").write_text("line1\nline2\nreworked\n", encoding="utf-8")

    matches = [c for c in capture_git_changes(tmp_path, base_sha) if c.path == "tracked.txt"]
    assert len(matches) == 1
    assert matches[0].status == "modified"


def test_capture_unreachable_base_sha_falls_back_to_working_tree(tmp_path):
    """A bogus/unreachable base_sha must degrade to the working-tree-only view
    rather than raising."""
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("brand new\n", encoding="utf-8")

    changes = capture_git_changes(tmp_path, "0" * 40)

    assert FileChange("new.txt", "added", 0, 0) in changes


def test_capture_base_sha_with_clean_tree_and_no_commits_returns_empty(tmp_path):
    """base_sha == HEAD with a clean tree yields an empty range and empty tree."""
    _init_repo(tmp_path)
    base_sha = capture_base_sha(tmp_path)

    assert capture_git_changes(tmp_path, base_sha) == []
