"""Validation of a populated per-job outbox."""

from __future__ import annotations

import itertools
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from agency.proposals import validate_proposal_schema

from .frontmatter import parse_frontmatter
from .outbox import OutboxPaths

MAX_RECORDS_PER_KIND = 20
MAX_RECORD_BYTES = 65536
MAX_OUTBOX_ENTRIES_PER_KIND = 100


@dataclass(frozen=True)
class RecordCandidate:
    kind: str
    source_name: str
    meta: dict
    body: str


@dataclass(frozen=True)
class OutboxRejection:
    kind: str
    source_name: str
    reason: str


@dataclass(frozen=True)
class OutboxValidation:
    accepted: tuple[RecordCandidate, ...]
    rejected: tuple[OutboxRejection, ...]

    @property
    def ok(self) -> bool:
        return not self.rejected


def _is_reparse_point(entry_stat: os.stat_result) -> bool:
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validate_kind(
    directory: Path,
    kind: str,
    *,
    writable_agents: frozenset[str],
    accepted: list[RecordCandidate],
    rejected: list[OutboxRejection],
) -> None:
    scanned = list(itertools.islice(directory.iterdir(), MAX_OUTBOX_ENTRIES_PER_KIND + 1))
    if len(scanned) > MAX_OUTBOX_ENTRIES_PER_KIND:
        rejected.append(
            OutboxRejection(
                kind=kind,
                source_name=directory.name,
                reason=f"too many entries: more than {MAX_OUTBOX_ENTRIES_PER_KIND}",
            )
        )
        return
    entries = sorted(scanned, key=lambda item: item.name.casefold())
    markdown_entries = [item for item in entries if item.suffix.casefold() == ".md"]
    if len(markdown_entries) > MAX_RECORDS_PER_KIND:
        rejected.append(
            OutboxRejection(
                kind=kind,
                source_name=directory.name,
                reason=(
                    f"too many records: {len(markdown_entries)} exceeds the "
                    f"limit of {MAX_RECORDS_PER_KIND}"
                ),
            )
        )
        return

    for entry in entries:
        entry_stat = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            rejected.append(
                OutboxRejection(kind, entry.name, "subdirectories are not allowed")
            )
            continue
        if not stat.S_ISREG(entry_stat.st_mode) or _is_reparse_point(entry_stat):
            rejected.append(
                OutboxRejection(kind, entry.name, "not a regular file")
            )
            continue
        if entry.suffix.casefold() != ".md":
            rejected.append(
                OutboxRejection(kind, entry.name, "not a markdown file")
            )
            continue
        if entry_stat.st_size > MAX_RECORD_BYTES:
            rejected.append(
                OutboxRejection(
                    kind,
                    entry.name,
                    f"record size {entry_stat.st_size} exceeds {MAX_RECORD_BYTES}",
                )
            )
            continue

        try:
            raw = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            rejected.append(
                OutboxRejection(kind, entry.name, "record is not valid UTF-8")
            )
            continue
        meta, body = parse_frontmatter(raw)
        if raw.startswith("---") and not meta:
            rejected.append(
                OutboxRejection(kind, entry.name, "front matter did not parse")
            )
            continue
        if not isinstance(meta, dict):
            rejected.append(
                OutboxRejection(kind, entry.name, "front matter must be a mapping")
            )
            continue
        if not body.strip():
            rejected.append(OutboxRejection(kind, entry.name, "record body is empty"))
            continue

        if kind == "proposal":
            errors = validate_proposal_schema(meta)
            if errors:
                rejected.append(OutboxRejection(kind, entry.name, "; ".join(errors)))
                continue
            executor = str(meta.get("execution_agent", "")).strip()
            if executor not in writable_agents:
                executor_truncated = executor[:80]
                rejected.append(
                    OutboxRejection(
                        kind,
                        entry.name,
                        f"execution_agent '{executor_truncated}' is not a writable, "
                        "executable configured agent (writable-agent set resolved "
                        "from config at execution time; may differ from config at "
                        "submission time if an agent was renamed or its capabilities "
                        "changed)",
                    )
                )
                continue

        accepted.append(
            RecordCandidate(kind=kind, source_name=entry.name, meta=meta, body=body)
        )


def validate_outbox(
    outbox: OutboxPaths,
    *,
    writable_agents: frozenset[str],
) -> OutboxValidation:
    accepted: list[RecordCandidate] = []
    rejected: list[OutboxRejection] = []
    for directory, kind in (
        (outbox.observations, "observation"),
        (outbox.proposals, "proposal"),
    ):
        if not directory.is_dir():
            continue
        _validate_kind(
            directory,
            kind,
            writable_agents=writable_agents,
            accepted=accepted,
            rejected=rejected,
        )
    return OutboxValidation(accepted=tuple(accepted), rejected=tuple(rejected))


def writable_agent_names(config, group_key: str) -> frozenset[str]:
    """Configured instances in a group that may be trusted to execute."""
    from agency.integrations import get_integration

    group = config.groups.get(group_key)
    if group is None:
        return frozenset()

    names: set[str] = set()
    for name, agent in group.agents.items():
        if not agent.capabilities.write:
            continue
        try:
            integration = get_integration(agent.integration)
        except KeyError:
            continue
        if integration.supports_execution:
            names.add(name)
    return frozenset(names)
