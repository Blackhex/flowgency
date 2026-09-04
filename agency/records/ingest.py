"""Ingest validated outbox records into the team's pipeline directories."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from agency.fs.atomic import atomic_write_text

from .frontmatter import extract_display_title, slugify
from .validation import OutboxValidation, RecordCandidate

_SLUG_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,58}[a-z0-9])?$")
_MAX_COLLISION_SUFFIX = 200

# Agency stamps identity and lifecycle itself, and keeps only the author fields
# it understands. Anything else an untrusted agent writes is discarded rather
# than persisted into records the web layer reads.
_ALLOWED_AUTHOR_FIELDS = {
    "observation": frozenset(
        {
            "category",
            "float",
            "linked_observations",
            "linked_proposal",
            "ttl_days",
        }
    ),
    "proposal": frozenset(
        {
            "execution_agent",
            "feedback_received",
            "feedback_requested",
            "observations",
            "questions",
            "ttl_days",
        }
    ),
}


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


def _reserve(directory: Path, name: str) -> Path | None:
    """Atomically claim `name` in `directory`, or return None if it is taken.

    The confinement check runs before the file is created, so a future widening
    of `_SLUG_PATTERN` cannot create anything outside `directory`.
    """
    candidate = directory / name
    if candidate.parent != directory:
        raise ValueError(f"record destination escaped its directory: {candidate}")
    try:
        fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    os.close(fd)
    return candidate


def _unique_path(directory: Path, date_prefix: str, slug: str) -> Path:
    reserved = _reserve(directory, f"{date_prefix}-{slug}.md")
    if reserved is not None:
        return reserved

    for suffix in range(2, _MAX_COLLISION_SUFFIX + 1):
        reserved = _reserve(directory, f"{date_prefix}-{slug}-{suffix}.md")
        if reserved is not None:
            return reserved

    raise RuntimeError(
        f"cannot create record in {directory}: "
        f"all {_MAX_COLLISION_SUFFIX} collision suffixes exhausted for {date_prefix}-{slug}"
    )


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
        allowed = _ALLOWED_AUTHOR_FIELDS[candidate.kind]
        meta = {
            key: value
            for key, value in candidate.meta.items()
            if key in allowed
        }
        meta["agent"] = agent_name
        meta["date"] = date_prefix
        meta["status"] = "open" if candidate.kind == "observation" else "proposed"
        if candidate.kind == "proposal":
            meta["origin_agent"] = agent_name

        path = _unique_path(directory, date_prefix, _record_slug(candidate, job_id))
        try:
            atomic_write_text(path, _render(meta, candidate.body))
        except Exception:
            # Clean up the empty placeholder if the write fails
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        written.append(IngestedRecord(kind=candidate.kind, path=path))

    return tuple(written)
