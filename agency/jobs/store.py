"""Atomic YAML persistence for durable agent jobs."""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agency.fs.locks import exclusive_lock
from agency.jobs.atomic import atomic_write_text
from agency.jobs.models import JobRecord


class InvalidJobTransition(RuntimeError):
    pass


VALID_TRANSITIONS = {
    "queued": {"waiting_for_memory", "running", "failed", "cancelled"},
    "waiting_for_memory": {"running", "failed", "cancelled", "complete"},
    "running": {"complete", "failed"},
    "complete": set(),
    "failed": set(),
    "cancelled": set(),
}


def job_path(group_path: Path, job_id: str) -> Path:
    return Path(group_path) / "shared" / "jobs" / f"{job_id}.yaml"


def group_operation_lock_path(group_path: Path) -> Path:
    return Path(group_path) / "shared" / "jobs" / ".operations.lock"


def canonical_group_operation_lock_paths(
    *group_paths: Path,
) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for group_path in group_paths:
        lock_path = group_operation_lock_path(group_path).resolve(strict=False)
        unique[str(lock_path).lower()] = lock_path
    return tuple(unique[key] for key in sorted(unique))


def acquire_group_operation_locks(*group_paths: Path) -> ExitStack:
    stack = ExitStack()
    try:
        for lock_path in canonical_group_operation_lock_paths(*group_paths):
            stack.enter_context(exclusive_lock(lock_path, wait=True))
    except Exception:
        stack.close()
        raise
    return stack


def write_job(path: Path, record: JobRecord) -> None:
    content = yaml.safe_dump(record.to_dict(), sort_keys=False)
    atomic_write_text(Path(path), content)


def job_lock_path(path: Path) -> Path:
    return Path(f"{path}.lock")


def read_job(path: Path) -> JobRecord:
    with Path(path).open(encoding="utf-8") as job_file:
        return JobRecord.from_dict(yaml.safe_load(job_file))


def transition_job(
    path: Path,
    expected: str,
    status: str,
    **changes: Any,
) -> JobRecord:
    with exclusive_lock(job_lock_path(path), wait=True):
        record = read_job(path)
        if record.status != expected:
            raise InvalidJobTransition(
                f"Expected job status {expected!r}, found {record.status!r}"
            )
        if status not in VALID_TRANSITIONS.get(expected, set()):
            raise InvalidJobTransition(
                f"Invalid job transition {expected!r} -> {status!r}"
            )
        updated = replace(record, status=status, **changes)
        write_job(path, updated)
        return updated


def cancel_job(path: Path) -> JobRecord:
    with exclusive_lock(job_lock_path(path), wait=True):
        record = read_job(path)
        if record.status not in {"queued", "waiting_for_memory"}:
            raise InvalidJobTransition(
                "Only queued or waiting_for_memory jobs can be cancelled"
            )
        updated = replace(record, status="cancelled")
        write_job(path, updated)
        return updated


def active_jobs(
    group_path: Path,
    agent_name: str | None = None,
) -> list[JobRecord]:
    """Return persisted active jobs, optionally for one agent."""
    jobs_dir = Path(group_path) / "shared" / "jobs"
    records = []
    for path in jobs_dir.glob("*.yaml"):
        try:
            record = read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            continue
        if record.status not in {"queued", "waiting_for_memory", "running"}:
            continue
        if agent_name is not None and record.spec.agent_name != agent_name:
            continue
        records.append(record)
    return records
