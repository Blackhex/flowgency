"""Atomic YAML persistence for durable agent jobs."""

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agency.jobs.models import JobRecord


class InvalidJobTransition(RuntimeError):
    pass


def job_path(group_path: Path, job_id: str) -> Path:
    return Path(group_path) / "shared" / "jobs" / f"{job_id}.yaml"


def write_job(path: Path, record: JobRecord) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
            yaml.safe_dump(record.to_dict(), temp_file, sort_keys=False)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


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
