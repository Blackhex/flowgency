"""Read-only presentation of retained Git evidence.

Nothing here opens a repository, runs Git, or contacts a network. Every byte
displayed or downloaded comes from the immutable artifact the capture service
retained, proven by this ticket's own trusted capture receipt. Today's
publication policy is deliberately not required: historical evidence stays
readable after the project's configuration moves on.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from flowgency.git_evidence.models import GitFileChange
from flowgency.jobs.models import JobRecord
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.tickets.errors import TicketStorageError
from flowgency.tickets.git_evidence import (
    GitEvidenceManifest,
    captured_artifact_ids as _captured_artifact_ids,
    validate_git_artifact,
)
from flowgency.tickets.models import TicketRecord, TicketRef, UserTicketContext
from flowgency.tickets.service import TicketService
from flowgency.tickets.views import ViewIssue

MAX_PREVIEW_BYTES = 200 * 1024
MAX_PREVIEW_LINES = 2000

PREVIEW_UNAVAILABLE_ENCODING = (
    "This patch is not UTF-8 text, so it cannot be shown here. "
    "Download the patch to read it."
)
PREVIEW_UNAVAILABLE_FORMAT = (
    "This patch is not in a form the viewer can lay out. "
    "Download the patch to read it."
)

# A parser given real-world Git output can fail in more than one way. All of
# them mean the same thing here – the retained bytes stay exact and
# downloadable, and only their rendering is withheld.
_PARSE_FAILURES = (UnidiffParseError, AttributeError, IndexError, KeyError, ValueError)

_LINE_KINDS: dict[str, str] = {"+": "added", "-": "removed", " ": "context"}

_C_ESCAPES: dict[str, int] = {
    "a": 0x07,
    "b": 0x08,
    "t": 0x09,
    "n": 0x0A,
    "v": 0x0B,
    "f": 0x0C,
    "r": 0x0D,
    '"': 0x22,
    "\\": 0x5C,
}
_OCTAL_DIGITS = frozenset("01234567")

# A NUL can never appear in a Git path, so an undecodable header name matches
# no manifest entry instead of being attributed to the wrong file.
_UNDECODABLE = "\0"


@dataclass(frozen=True)
class GitDiffLine:
    kind: Literal["context", "added", "removed", "marker"]
    old_line: int | None
    new_line: int | None
    text: str


@dataclass(frozen=True)
class GitDiffFile:
    anchor: str
    change: GitFileChange
    lines: tuple[GitDiffLine, ...]


@dataclass(frozen=True)
class GitDiffPreview:
    files: tuple[GitDiffFile, ...]
    omitted: bool
    unavailable_reason: str | None


def _change(entry) -> GitFileChange:
    return GitFileChange(
        path=entry.path,
        old_path=entry.old_path,
        status=entry.status,
        lines_added=entry.lines_added,
        lines_removed=entry.lines_removed,
        binary=entry.binary,
        submodule=entry.submodule,
        old_mode=entry.old_mode,
        new_mode=entry.new_mode,
        old_object_id=entry.old_object_id,
        new_object_id=entry.new_object_id,
    )


def _strip_prefix(name: str) -> str:
    if name.startswith("a/") or name.startswith("b/"):
        return name[2:]
    return name


def _unquote_c_style(name: str) -> str | None:
    """Decode Git's C-quoted header path; ``None`` when it cannot be decoded.

    Git quotes a header path whose bytes are non-ASCII or awkward, escaping
    control bytes by name and every other byte as three octal digits, so the
    real path is only recoverable byte by byte.
    """
    if len(name) < 2 or not name.endswith('"'):
        return None
    raw = bytearray()
    index = 1
    end = len(name) - 1
    while index < end:
        character = name[index]
        index += 1
        if character != "\\":
            raw.extend(character.encode("utf-8"))
            continue
        if index >= end:
            return None
        escape = name[index]
        index += 1
        if escape in _C_ESCAPES:
            raw.append(_C_ESCAPES[escape])
            continue
        if escape not in _OCTAL_DIGITS:
            return None
        digits = escape
        while len(digits) < 3 and index < end and name[index] in _OCTAL_DIGITS:
            digits += name[index]
            index += 1
        value = int(digits, 8)
        if value > 0xFF:
            return None
        raw.append(value)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _header_path(name: str | None) -> str | None:
    """The real path a patch header names, or ``None`` for an absent side."""
    if name is None:
        return None
    if name.startswith('"'):
        decoded = _unquote_c_style(name)
        if decoded is None:
            return _UNDECODABLE
        name = decoded
    if name == "/dev/null":
        return None
    return _strip_prefix(name)


def _parsed_pair(patched_file) -> tuple[str | None, str | None]:
    return (
        _header_path(patched_file.source_file),
        _header_path(patched_file.target_file),
    )


def _expected_pair(change: GitFileChange) -> tuple[str | None, str | None]:
    if change.status == "added":
        return (None, change.path)
    if change.status == "deleted":
        return (change.path, None)
    return (change.old_path or change.path, change.path)


def _file_rows(patched_file) -> tuple[tuple[GitDiffLine, ...], ...]:
    """One tuple of rows per complete hunk; hunks are never split."""
    groups: list[tuple[GitDiffLine, ...]] = []
    for hunk in patched_file:
        rows: list[GitDiffLine] = []
        for line in hunk:
            rows.append(
                GitDiffLine(
                    kind=_LINE_KINDS.get(line.line_type, "marker"),
                    old_line=line.source_line_no,
                    new_line=line.target_line_no,
                    text=line.value,
                )
            )
        groups.append(tuple(rows))
    return tuple(groups)


def _pending_hunks(parsed: PatchSet) -> dict[tuple[str | None, str | None], deque]:
    """Parsed hunk groups keyed by the exact old/new path pair they describe."""
    pending: dict[tuple[str | None, str | None], deque] = defaultdict(deque)
    for patched_file in parsed:
        pending[_parsed_pair(patched_file)].append(_file_rows(patched_file))
    return pending


def _claim(pending, key) -> tuple[tuple[GitDiffLine, ...], ...] | None:
    """Take one parsed entry for this key, so no entry is ever shown twice."""
    queue = pending.get(key)
    if not queue:
        return None
    return queue.popleft()


def _hunks_for(change: GitFileChange, pending) -> tuple[tuple[GitDiffLine, ...], ...] | None:
    claimed = _claim(pending, _expected_pair(change))
    if claimed is not None or change.status != "type-changed":
        return claimed
    # Git writes a type change as a delete of the old blob followed by an add
    # of the new one, both naming the same path.
    removed = _claim(pending, (change.path, None))
    added = _claim(pending, (None, change.path))
    if removed is None and added is None:
        return None
    return (removed or ()) + (added or ())


def parse_git_diff(
    manifest: GitEvidenceManifest,
    *,
    max_bytes: int = MAX_PREVIEW_BYTES,
    max_lines: int = MAX_PREVIEW_LINES,
) -> GitDiffPreview:
    """Lay out the retained patch within a bounded display budget.

    The complete retained patch is parsed; only its presentation is bounded,
    and a hunk is either shown whole or reported as omitted.
    """
    changes = tuple(_change(entry) for entry in manifest.files)
    patch = manifest.patch
    if not patch:
        return GitDiffPreview(
            files=tuple(
                GitDiffFile(anchor=f"change-{index}", change=change, lines=())
                for index, change in enumerate(changes)
            ),
            omitted=False,
            unavailable_reason=None,
        )
    try:
        patch_text = patch.decode("utf-8")
    except UnicodeDecodeError:
        return GitDiffPreview(
            files=(), omitted=False, unavailable_reason=PREVIEW_UNAVAILABLE_ENCODING
        )
    try:
        parsed = PatchSet(patch_text.splitlines(keepends=True))
        pending = _pending_hunks(parsed)
    except _PARSE_FAILURES:
        return GitDiffPreview(
            files=(), omitted=False, unavailable_reason=PREVIEW_UNAVAILABLE_FORMAT
        )
    used_bytes = 0
    used_lines = 0
    omitted = False
    files: list[GitDiffFile] = []
    for position, change in enumerate(changes):
        hunks = _hunks_for(change, pending)
        if hunks is None and (change.lines_added or change.lines_removed):
            # The patch describes rows for this file that could not be matched
            # to it; report that rather than imply an empty change.
            omitted = True
        shown: list[GitDiffLine] = []
        for rows in hunks or ():
            cost = sum(len(row.text.encode("utf-8")) for row in rows)
            if used_bytes + cost > max_bytes or used_lines + len(rows) > max_lines:
                omitted = True
                break
            used_bytes += cost
            used_lines += len(rows)
            shown.extend(rows)
        files.append(
            GitDiffFile(
                anchor=f"change-{position}", change=change, lines=tuple(shown)
            )
        )
    unclaimed = any(
        any(rows) for queue in pending.values() for groups in queue for rows in groups
    )
    return GitDiffPreview(
        files=tuple(files), omitted=omitted or unclaimed, unavailable_reason=None
    )


def load_ticket_git_evidence(
    service: TicketService,
    actor: UserTicketContext,
    ref: TicketRef,
    artifact_id: str,
) -> tuple[RetainedArtifact, GitEvidenceManifest]:
    """Read one artifact and prove this ticket itself captured it.

    Access, storage integrity and the trusted-capture binding are all checked.
    The current publication policy is not: a read never re-verifies today's
    configuration against evidence retained under an earlier one.
    """
    view = service.inspect(actor, ref)
    binding = service._resolve_current_binding(ref.team_id, ref.workflow_id)
    provider = service.storage_factory(binding.storage)
    artifact = provider.read_artifact(ref, artifact_id)
    manifest = validate_git_artifact(view.record, artifact)
    return artifact, manifest


def captured_artifact_ids(record: TicketRecord) -> frozenset[str]:
    """Re-exported so viewer callers read the owning domain's one definition."""
    return _captured_artifact_ids(record)


def accepted_output_artifact_ids(record: TicketRecord) -> tuple[str, ...]:
    """Artifact IDs an accepted transition recorded as an output, in order."""
    found: list[str] = []
    for event in record.events:
        if event.kind != "transitioned" or not isinstance(event.data, dict):
            continue
        outputs = event.data.get("effective_outputs")
        if not isinstance(outputs, dict):
            continue
        for value in outputs.values():
            if not isinstance(value, dict) or value.get("kind") != "id":
                continue
            artifact_id = value.get("value")
            if isinstance(artifact_id, str) and artifact_id not in found:
                found.append(artifact_id)
    return tuple(found)


def _diff_href(ref: TicketRef, artifact_id: str, *, source: str) -> str:
    return (
        f"/{ref.team_id}/workflows/{ref.workflow_id}/tickets/{ref.ticket_id}"
        f"/artifacts/{artifact_id}/diff?source={source}"
    )


def ticket_href(ref: TicketRef) -> str:
    return f"/{ref.team_id}/workflows/{ref.workflow_id}/tickets/{ref.ticket_id}"


def job_git_evidence_links(
    service: TicketService,
    actor: UserTicketContext,
    record: JobRecord,
) -> tuple[tuple[dict[str, str], ...], tuple[ViewIssue, ...]]:
    """Evidence this exact persisted job produced, as accepted ticket output.

    Only the team's currently configured workflows are consulted, and only a
    manifest whose producing job and agent match this record is listed. A
    later reviewer that merely referenced the artifact never becomes its
    producer.
    """
    job_id = record.spec.job_id
    agent_name = record.spec.agent_name
    links: list[dict[str, str]] = []
    issues: list[ViewIssue] = []
    try:
        bindings = service.list_workflows(actor)
    except TicketStorageError as error:
        return (), (ViewIssue(code=error.code, message=error.message),)
    for binding in bindings:
        try:
            views = service.list_tickets(actor, binding.workflow_id)
        except TicketStorageError as error:
            issues.append(ViewIssue(code=error.code, message=error.message))
            continue
        provider = service.storage_factory(binding.storage)
        for view in views:
            ticket = view.record
            if ticket.ref is None:
                continue
            trusted = captured_artifact_ids(ticket)
            for artifact_id in accepted_output_artifact_ids(ticket):
                if artifact_id not in trusted:
                    continue
                try:
                    artifact = provider.read_artifact(ticket.ref, artifact_id)
                    manifest = validate_git_artifact(ticket, artifact)
                except TicketStorageError as error:
                    issues.append(ViewIssue(code=error.code, message=error.message))
                    continue
                if manifest.job_id != job_id or manifest.agent_name != agent_name:
                    continue
                links.append(
                    {
                        "label": f"{ticket.title} · {manifest.end_commit[:12]}",
                        "href": _diff_href(ticket.ref, artifact_id, source="job"),
                        "ticket_href": ticket_href(ticket.ref),
                    }
                )
    return tuple(links), tuple(issues)
