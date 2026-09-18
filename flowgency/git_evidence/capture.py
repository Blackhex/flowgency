"""Capture exactly what a selected committed range changed.

The range is read from the private object view opened by :mod:`~.git`, so the
result describes committed objects only: staged, unstaged, and untracked
content in the source work tree cannot reach it. Paths come from Git's
NUL-delimited machine formats, never from human-formatted status text, and
every path in the range must be readable under the actor's effective policies
before any patch byte is produced.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from flowgency.git_evidence.git import (
    MAX_PATCH_BYTES,
    METADATA_OUTPUT_LIMIT_BYTES,
    run_git_bytes,
    run_git_exit_code,
    verify_repository_identity,
)
from flowgency.git_evidence.models import (
    GitChangeStatus,
    GitCommitRange,
    GitEvidenceError,
    GitFileChange,
    GitRangeCapture,
    GitRepository,
    validate_object_id,
)
from flowgency.integrations.models import EffectiveRuntimePolicy
from flowgency.jobs.processes import RuntimeProcessLifecycle

MAX_COMMITS = 512
MAX_FILES = 1024

_STATUS_NAMES: dict[bytes, GitChangeStatus] = {
    b"A": "added",
    b"M": "modified",
    b"D": "deleted",
    b"R": "renamed",
    b"T": "type-changed",
}
_SUBMODULE_MODE = "160000"


class _RawRecord:
    __slots__ = ("old_mode", "new_mode", "old_id", "new_id", "status", "old_path", "new_path")

    def __init__(
        self,
        old_mode: str,
        new_mode: str,
        old_id: str,
        new_id: str,
        status: GitChangeStatus,
        old_path: bytes | None,
        new_path: bytes,
    ) -> None:
        self.old_mode = old_mode
        self.new_mode = new_mode
        self.old_id = old_id
        self.new_id = new_id
        self.status = status
        self.old_path = old_path
        self.new_path = new_path


def _require_commit(
    repository: GitRepository,
    object_id: str,
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> None:
    code, data = run_git_exit_code(
        repository,
        ("cat-file", "-t", object_id),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=4096,
    )
    if code != 0:
        raise GitEvidenceError("git-evidence-object-missing")
    if data.strip() != b"commit":
        raise GitEvidenceError("git-evidence-invalid-commit")


def _decode_path(raw: bytes) -> str:
    if raw.startswith(b'"'):
        raise GitEvidenceError("git-evidence-unsupported-path")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GitEvidenceError("git-evidence-unsupported-path") from None
    if not text:
        raise GitEvidenceError("git-evidence-unsupported-path")
    # A tab is a legal, unambiguous Git path byte under -z; a newline or other
    # control byte is not worth supporting for the injection risk it carries.
    if "\\" in text or any(
        (ord(character) < 0x20 and character != "\t") or ord(character) == 0x7F
        for character in text
    ):
        raise GitEvidenceError("git-evidence-unsupported-path")
    return text


def _parse_raw(data: bytes) -> list[_RawRecord]:
    parts = data.split(b"\0")
    records: list[_RawRecord] = []
    index = 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if not item:
            continue
        if not item.startswith(b":"):
            raise GitEvidenceError("git-evidence-unsupported-change")
        fields = item[1:].split(b" ")
        if len(fields) != 5:
            raise GitEvidenceError("git-evidence-unsupported-change")
        status = _STATUS_NAMES.get(fields[4][:1])
        if status is None:
            raise GitEvidenceError("git-evidence-unsupported-change")
        if index >= len(parts):
            raise GitEvidenceError("git-evidence-unsupported-change")
        first = parts[index]
        index += 1
        old_path: bytes | None = None
        new_path = first
        if status == "renamed":
            if index >= len(parts):
                raise GitEvidenceError("git-evidence-unsupported-change")
            old_path = first
            new_path = parts[index]
            index += 1
        records.append(
            _RawRecord(
                old_mode=fields[0].decode("ascii", errors="replace"),
                new_mode=fields[1].decode("ascii", errors="replace"),
                old_id=fields[2].decode("ascii", errors="replace"),
                new_id=fields[3].decode("ascii", errors="replace"),
                status=status,
                old_path=old_path,
                new_path=new_path,
            )
        )
    return records


def _parse_numstat(data: bytes, records: list[_RawRecord]) -> list[tuple[bytes, bytes]]:
    """Return per-record added/removed fields in the raw diff's own order.

    ``--numstat -z`` writes ``added TAB removed TAB`` and then one NUL-ended
    path, or an empty field followed by two NUL-ended paths for a rename, so
    arity is only knowable from the raw records.
    """
    parts = data.split(b"\0")
    counts: list[tuple[bytes, bytes]] = []
    index = 0
    for record in records:
        if index >= len(parts):
            raise GitEvidenceError("git-evidence-command-failed")
        head = parts[index]
        index += 1
        fields = head.split(b"\t", 2)
        if len(fields) != 3:
            raise GitEvidenceError("git-evidence-command-failed")
        if record.status == "renamed":
            if index + 1 >= len(parts):
                raise GitEvidenceError("git-evidence-command-failed")
            index += 2
        counts.append((fields[0], fields[1]))
    return counts


def _is_unsafe_relative_path(text: str) -> bool:
    pure = PurePosixPath(text)
    if pure.is_absolute() or text.startswith("/"):
        return True
    if len(text) > 1 and text[1] == ":":
        return True
    parts = pure.parts
    if not parts:
        return True
    return any(part in {"..", "."} or part.lower() == ".git" for part in parts)


def _authorize_path(
    text: str,
    *,
    workspace: Path,
    policies: tuple[EffectiveRuntimePolicy, ...],
) -> None:
    if _is_unsafe_relative_path(text):
        raise GitEvidenceError("git-evidence-path-denied")
    relative = PurePosixPath(text)
    parent = workspace
    if len(relative.parts) > 1:
        parent = (workspace / Path(*relative.parts[:-1])).resolve(strict=False)
    if not _under(parent, workspace):
        raise GitEvidenceError("git-evidence-path-denied")
    # The final component is never resolved or opened: a committed symlink blob
    # is diff content, not a file to follow out of the workspace.
    candidate = parent / relative.parts[-1]
    for policy in policies:
        tools = policy.tools_for(candidate)
        if tools is not None and "read" not in tools:
            raise GitEvidenceError("git-evidence-path-denied")


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _commit_ids(
    repository: GitRepository,
    base_commit: str,
    end_commit: str,
    *,
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> tuple[str, ...]:
    data = run_git_bytes(
        repository,
        (
            "rev-list",
            "--reverse",
            "--topo-order",
            f"--max-count={MAX_COMMITS + 1}",
            f"{base_commit}..{end_commit}",
            "--",
        ),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=METADATA_OUTPUT_LIMIT_BYTES,
    )
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise GitEvidenceError("git-evidence-command-failed") from None
    commits = [line.strip() for line in text.split("\n") if line.strip()]
    if len(commits) > MAX_COMMITS:
        raise GitEvidenceError("git-evidence-too-many-commits")
    return tuple(
        validate_object_id(commit, repository.object_format) for commit in commits
    )


def capture_committed_range(
    repository: GitRepository,
    selected: GitCommitRange,
    *,
    policies: tuple[EffectiveRuntimePolicy, ...],
    lifecycle: RuntimeProcessLifecycle,
    deadline: float,
) -> GitRangeCapture:
    base_commit = validate_object_id(selected.base_commit, repository.object_format)
    end_commit = validate_object_id(selected.end_commit, repository.object_format)
    _require_commit(repository, base_commit, lifecycle=lifecycle, deadline=deadline)
    _require_commit(repository, end_commit, lifecycle=lifecycle, deadline=deadline)

    if base_commit != end_commit:
        code, _ = run_git_exit_code(
            repository,
            ("merge-base", "--is-ancestor", base_commit, end_commit),
            lifecycle=lifecycle,
            deadline=deadline,
            output_limit=4096,
        )
        if code == 1:
            raise GitEvidenceError("git-evidence-range-not-ancestor")
        if code != 0:
            raise GitEvidenceError("git-evidence-command-failed")

    commit_ids = _commit_ids(
        repository, base_commit, end_commit, lifecycle=lifecycle, deadline=deadline
    )

    shared = ("--find-renames=50%", "-l1024", base_commit, end_commit, "--")
    records = _parse_raw(
        run_git_bytes(
            repository,
            ("diff", "--raw", "--no-abbrev", "-z", *shared),
            lifecycle=lifecycle,
            deadline=deadline,
            output_limit=METADATA_OUTPUT_LIMIT_BYTES,
        )
    )
    if len(records) > MAX_FILES:
        raise GitEvidenceError("git-evidence-too-many-files")
    counts = _parse_numstat(
        run_git_bytes(
            repository,
            ("diff", "--numstat", "-z", *shared),
            lifecycle=lifecycle,
            deadline=deadline,
            output_limit=METADATA_OUTPUT_LIMIT_BYTES,
        ),
        records,
    )

    changes: list[GitFileChange] = []
    for record, (added, removed) in zip(records, counts):
        new_path = _decode_path(record.new_path)
        old_path = _decode_path(record.old_path) if record.old_path is not None else None
        _authorize_path(new_path, workspace=repository.workspace, policies=policies)
        if old_path is not None:
            _authorize_path(old_path, workspace=repository.workspace, policies=policies)
        binary = added == b"-" or removed == b"-"
        changes.append(
            GitFileChange(
                path=new_path,
                old_path=old_path,
                status=record.status,
                lines_added=0 if binary else int(added),
                lines_removed=0 if binary else int(removed),
                binary=binary,
                submodule=_SUBMODULE_MODE in {record.old_mode, record.new_mode},
                old_mode=record.old_mode,
                new_mode=record.new_mode,
                old_object_id=record.old_id,
                new_object_id=record.new_id,
            )
        )

    patch = run_git_bytes(
        repository,
        (
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            *shared,
        ),
        lifecycle=lifecycle,
        deadline=deadline,
        output_limit=MAX_PATCH_BYTES,
    )

    verify_repository_identity(repository)
    return GitRangeCapture(
        repository_id=repository.repository_id,
        base_commit=base_commit,
        end_commit=end_commit,
        commit_ids=commit_ids,
        files=tuple(sorted(changes, key=lambda change: (change.path, change.old_path or ""))),
        patch=patch,
    )
