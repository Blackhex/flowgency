"""Ingest validated outbox records into the group's pipeline directories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from agency.fs.atomic import atomic_write_text

from .frontmatter import extract_display_title, slugify
from .validation import OutboxValidation, RecordCandidate

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]{1,60}$")

# Agency owns these; an author-supplied value is discarded.
_STAMPED_FIELDS = ("agent", "date", "status")


@dataclass(frozen=True)
class IngestedRecord:
    kind: str
    path: Path


def _record_slug(candidate: RecordCandidate, job_id: str) -> str:
    raw = candidate.meta.get("slug")
    if isinstance(raw, str) and _SLUG_PATTERN.match(raw.strip()):
        return raw.strip()
    title = extract_display_title(candidate.body, "")
    slug = slugify(title)
    return slug or slugify(job_id) or "record"


def _unique_path(directory: Path, date_prefix: str, slug: str) -> Path:
    candidate = directory / f"{date_prefix}-{slug}.md"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = directory / f"{date_prefix}-{slug}-{suffix}.md"
        if not candidate.exists():
            return candidate
        suffix += 1


def _render(meta: dict, body: str) -> str:
    front = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{front}\n---\n\n{body.strip()}\n"


def ingest_records(
    validation: OutboxValidation,
    *,
    observations_dir: Path,
    proposals_dir: Path,
    agent_name: str,
    now: datetime,
    job_id: str,
) -> tuple[IngestedRecord, ...]:
    targets = {
        "observation": Path(observations_dir),
        "proposal": Path(proposals_dir),
    }
    for directory in targets.values():
        directory.mkdir(parents=True, exist_ok=True)

    date_prefix = now.date().isoformat()
    written: list[IngestedRecord] = []

    for candidate in validation.accepted:
        directory = targets[candidate.kind]
        meta = {
            key: value
            for key, value in candidate.meta.items()
            if key not in _STAMPED_FIELDS and key != "slug"
        }
        meta["agent"] = agent_name
        meta["date"] = date_prefix
        meta["status"] = "open"

        path = _unique_path(directory, date_prefix, _record_slug(candidate, job_id))
        atomic_write_text(path, _render(meta, candidate.body))
        written.append(IngestedRecord(kind=candidate.kind, path=path))

    return tuple(written)
