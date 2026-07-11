"""Tests for the integration-agnostic git-status changed-file capture.

Copilot reports per-file edits natively; every other integration relies on the
git-status fallback in ``agency.jobs.changes`` so decision outcomes are visible
for all nine tools, not just one.
"""

import subprocess
from pathlib import Path

import pytest

from agency.integrations import FileChange
from agency.jobs.changes import capture_git_changes


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
