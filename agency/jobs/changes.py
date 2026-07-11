"""Integration-agnostic changed-file capture via git.

Copilot reports per-file edits natively from its JSONL output. The other eight
integrations report nothing, so decision outcomes were blind for every tool but
one. This module provides a git-status fallback that works for any integration
whose sandbox root lives inside a git repository: after a run completes, it reads
the working-tree changes and reports them in the same ``FileChange`` shape the
native path produces.

The capture is strictly best-effort. A missing git binary, a root that is not a
repository, or any git error yields an empty list — outcome capture must never
break a durable job or mask a real execution result.
"""

import logging
import subprocess
from pathlib import Path

from agency.integrations import FileChange

logger = logging.getLogger(__name__)

# porcelain XY status codes → FileChange status. The first non-space code across
# the index (X) and worktree (Y) columns wins.
_STATUS_MAP = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "R": "modified",
    "C": "modified",
    "T": "modified",
    "U": "modified",
}


def _run_git(root: Path, *args: str) -> str | None:
    """Run a git command in ``root``. Return stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("git %s failed in %s: %s", args, root, error)
        return None
    if result.returncode != 0:
        logger.debug("git %s in %s exited %s: %s", args, root, result.returncode, result.stderr.strip())
        return None
    return result.stdout


def _parse_numstat(raw: str) -> dict[str, tuple[int, int]]:
    """Parse ``git diff --numstat`` output into {path: (added, removed)}.

    Binary files show ``-`` for both counts and are recorded as (0, 0). Rename
    entries (``old => new`` / tab-separated old/new) map the new path.
    """
    counts: dict[str, tuple[int, int]] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_s, removed_s, path = parts[0], parts[1], parts[-1]
        added = int(added_s) if added_s.isdigit() else 0
        removed = int(removed_s) if removed_s.isdigit() else 0
        counts[path] = (added, removed)
    return counts


def _status_from_codes(index_code: str, worktree_code: str) -> str:
    if index_code == "?" or worktree_code == "?":
        return "added"
    for code in (index_code, worktree_code):
        mapped = _STATUS_MAP.get(code)
        if mapped:
            return mapped
    return "modified"


def _parse_porcelain(raw: str) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain`` into [(path, status)]."""
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        index_code, worktree_code = line[0], line[1]
        rest = line[3:]
        # Renames/copies render as "old -> new"; report the destination path.
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        path = rest.strip().strip('"')
        if not path:
            continue
        entries.append((path, _status_from_codes(index_code, worktree_code)))
    return entries


def capture_git_changes(root: Path | None) -> list[FileChange]:
    """Return working-tree changes under ``root`` as a list of ``FileChange``.

    Combines ``git status --porcelain`` (which files changed, including untracked)
    with ``git diff --numstat HEAD`` (line counts for tracked changes). Untracked
    files have no diff counts and report (0, 0). Returns [] on any problem.
    """
    if root is None:
        return []
    root = Path(root)
    if not root.exists():
        return []

    status_out = _run_git(root, "status", "--porcelain")
    if status_out is None:
        return []

    entries = _parse_porcelain(status_out)
    if not entries:
        return []

    numstat_out = _run_git(root, "diff", "--numstat", "HEAD")
    counts = _parse_numstat(numstat_out) if numstat_out else {}

    changes: list[FileChange] = []
    for path, status in entries:
        added, removed = counts.get(path, (0, 0))
        changes.append(
            FileChange(
                path=path,
                status=status,
                lines_added=added,
                lines_removed=removed,
            )
        )
    return changes
