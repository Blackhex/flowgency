"""Integration-agnostic changed-file capture via git.

Copilot reports per-file edits natively from its JSONL output. The other eight
integrations report nothing, so decision outcomes were blind for every tool but
one. This module provides a git fallback that works for any integration whose
sandbox root lives inside a git repository: after a run completes, it reads the
changes and reports them in the same ``FileChange`` shape the native path
produces.

Every builder identity in the fleet is contractually required to commit each
atomic change, so a compliant agent leaves a clean working tree. A working-tree
only view would therefore capture the exception (uncommitted leftovers) and miss
the rule (committed work). To see committed work, callers record the sandbox
root's HEAD before the run (``capture_base_sha``) and pass it back afterwards:
the capture then unions the current working-tree changes with the committed range
``base_sha..HEAD``, de-duplicated by path with the working-tree status winning on
conflict.

The capture is strictly best-effort. A missing git binary, a root that is not a
repository, a missing/unreachable ``base_sha`` (detached HEAD or shallow clone
where it is not present locally, or rewritten history), or any git error yields
an empty list or falls back to the working-tree-only result — outcome capture
must never break a durable job or mask a real execution result.
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


def _parse_name_status(raw: str) -> list[tuple[str, str]]:
    """Parse ``git diff --name-status`` into [(path, status)].

    Each line is tab-separated: a status code followed by the path. Rename and
    copy entries render as ``R100\\told\\tnew`` / ``C100\\told\\tnew``; the
    destination (last field) is reported. The leading letter of the code selects
    the status via ``_STATUS_MAP`` so committed changes map consistently with the
    working-tree porcelain view.
    """
    entries: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0]
        path = parts[-1].strip().strip('"')
        if not code or not path:
            continue
        entries.append((path, _STATUS_MAP.get(code[0], "modified")))
    return entries


def capture_base_sha(root: Path | None) -> str | None:
    """Return ``root``'s current HEAD commit, or None on any problem.

    Recorded before a run so the post-run capture can observe committed work via
    ``base_sha..HEAD``. Best-effort: a missing git binary, non-repo root, or an
    unborn branch (no commits yet) yields None, which disables the committed-range
    union without raising.
    """
    if root is None:
        return None
    root = Path(root)
    if not root.exists():
        return None
    out = _run_git(root, "rev-parse", "HEAD")
    if out is None:
        return None
    sha = out.strip()
    return sha or None


def _capture_working_tree(root: Path) -> dict[str, FileChange]:
    """Working-tree changes under ``root`` keyed by path. Empty on any problem."""
    status_out = _run_git(root, "status", "--porcelain")
    if status_out is None:
        return {}
    entries = _parse_porcelain(status_out)
    if not entries:
        return {}
    numstat_out = _run_git(root, "diff", "--numstat", "HEAD")
    counts = _parse_numstat(numstat_out) if numstat_out else {}
    changes: dict[str, FileChange] = {}
    for path, status in entries:
        added, removed = counts.get(path, (0, 0))
        changes[path] = FileChange(
            path=path,
            status=status,
            lines_added=added,
            lines_removed=removed,
        )
    return changes


def _capture_committed_range(root: Path, base_sha: str) -> dict[str, FileChange]:
    """Committed changes in ``base_sha..HEAD`` keyed by path. Empty on any problem.

    If ``base_sha`` is unreachable (shallow clone missing the commit, rewritten
    history) the underlying git command fails and this returns ``{}``, leaving the
    caller with the working-tree-only view.
    """
    rng = f"{base_sha}..HEAD"
    name_status_out = _run_git(root, "diff", "--name-status", rng)
    if name_status_out is None:
        return {}
    entries = _parse_name_status(name_status_out)
    if not entries:
        return {}
    numstat_out = _run_git(root, "diff", "--numstat", rng)
    counts = _parse_numstat(numstat_out) if numstat_out else {}
    changes: dict[str, FileChange] = {}
    for path, status in entries:
        added, removed = counts.get(path, (0, 0))
        changes[path] = FileChange(
            path=path,
            status=status,
            lines_added=added,
            lines_removed=removed,
        )
    return changes


def capture_git_changes(
    root: Path | None, base_sha: str | None = None
) -> list[FileChange]:
    """Return changes under ``root`` as a list of ``FileChange``.

    Unions the working-tree changes (``git status --porcelain`` +
    ``git diff --numstat HEAD``, including untracked files) with the committed
    range ``base_sha..HEAD`` when ``base_sha`` is provided and reachable. Entries
    are de-duplicated by path, with the working-tree view winning on conflict so a
    file that was committed and then edited again reflects its latest state.

    Returns ``[]`` on any problem (no root, non-repo, git failure). A provided but
    unreachable ``base_sha`` degrades gracefully to the working-tree-only result.
    """
    if root is None:
        return []
    root = Path(root)
    if not root.exists():
        return []

    merged: dict[str, FileChange] = {}
    if base_sha:
        # Committed range first so the working-tree view can override on conflict.
        merged.update(_capture_committed_range(root, base_sha))
    merged.update(_capture_working_tree(root))
    return list(merged.values())
