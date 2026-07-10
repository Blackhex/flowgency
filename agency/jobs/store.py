"""Atomic YAML persistence for durable agent jobs."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agency.jobs.atomic import atomic_write_text
from agency.jobs.models import JobRecord


class InvalidJobTransition(RuntimeError):
    pass


def job_path(group_path: Path, job_id: str) -> Path:
    return Path(group_path) / "shared" / "jobs" / f"{job_id}.yaml"


def write_job(path: Path, record: JobRecord) -> None:
    content = yaml.safe_dump(record.to_dict(), sort_keys=False)
    atomic_write_text(Path(path), content)


def read_job(path: Path) -> JobRecord:
    with Path(path).open(encoding="utf-8") as job_file:
        return JobRecord.from_dict(yaml.safe_load(job_file))


def transition_job(
    path: Path,
    expected: str,
    status: str,
    **changes: Any,
) -> JobRecord:
    record = read_job(path)
    if record.status != expected:
        raise InvalidJobTransition(
            f"Expected job status {expected!r}, found {record.status!r}"
        )
    updated = replace(record, status=status, **changes)
    write_job(path, updated)
    return updated


def active_jobs(group_path: Path, agent_name: str | None = None) -> list[JobRecord]:
    """Return persisted queued and running jobs, optionally for one agent."""
    jobs_dir = Path(group_path) / "shared" / "jobs"
    records = []
    for path in jobs_dir.glob("*.yaml"):
        try:
            record = read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            continue
        if record.status not in {"queued", "running"}:
            continue
        if agent_name is not None and record.spec.agent_name != agent_name:
            continue
        records.append(record)
    return records
