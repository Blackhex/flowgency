"""Atomic YAML persistence for durable agent jobs."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
import os
from pathlib import Path
import time
from typing import Any

import yaml

from agency.fs.locks import exclusive_lock
from agency.configuration.store import (
    ConfigConflictError,
    ConfigSnapshot,
    ConfigStore,
)
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


_WINDOWS_READ_RETRIES = 200
_WINDOWS_READ_DELAY_SECONDS = 0.01


def job_path(jobs_dir: Path, job_id: str) -> Path:
    return Path(jobs_dir) / f"{job_id}.yaml"


def group_operation_lock_path(group_root: Path) -> Path:
    return Path(group_root) / "locks" / ".operations.lock"


def canonical_group_operation_lock_paths(
    *group_roots: Path,
) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for group_root in group_roots:
        lock_path = group_operation_lock_path(group_root).resolve(strict=False)
        unique[os.path.normcase(str(lock_path))] = lock_path
    return tuple(unique[key] for key in sorted(unique))


def acquire_group_operation_locks(*group_roots: Path) -> ExitStack:
    stack = ExitStack()
    try:
        for lock_path in canonical_group_operation_lock_paths(*group_roots):
            stack.enter_context(exclusive_lock(lock_path, wait=True))
    except Exception:
        stack.close()
        raise
    return stack


@contextmanager
def revision_bound_group_operation(
    config_store: ConfigStore,
    *,
    group_ids: tuple[str, ...] = (),
    proposed_paths: tuple[Path, ...] = (),
    all_groups: bool = False,
    expected_revision: str | None = None,
):
    initial = config_store.load()
    relevant_ids = (
        tuple(sorted(initial.config.groups))
        if all_groups
        else tuple(sorted(set(group_ids)))
    )
    initial_identity = _group_path_identity(initial, relevant_ids)
    lock_paths = tuple(initial_identity.values()) + tuple(proposed_paths)
    with acquire_group_operation_locks(*lock_paths):
        locked = config_store.load()
        locked_ids = (
            tuple(sorted(locked.config.groups))
            if all_groups
            else relevant_ids
        )
        if (
            locked.revision != initial.revision
            or locked_ids != relevant_ids
            or _group_path_identity(locked, locked_ids) != initial_identity
        ):
            raise ConfigConflictError(
                "config group paths changed while acquiring operation locks"
            )
        if (
            expected_revision is not None
            and locked.revision != expected_revision
        ):
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        yield locked


def _group_path_identity(
    snapshot: ConfigSnapshot,
    group_ids: tuple[str, ...],
) -> dict[str, Path]:
    identity: dict[str, Path] = {}
    for group_id in group_ids:
        try:
            identity[group_id] = (
                snapshot.config.groups[group_id].path.resolve()
            )
        except KeyError as exc:
            raise ValueError(f"Unknown group: {group_id}") from exc
    return identity


def write_job(path: Path, record: JobRecord) -> None:
    if record.authority_digest != record.spec.immutable_digest():
        raise ValueError("immutable job authority digest mismatch")
    content = yaml.safe_dump(record.to_dict(), sort_keys=False)
    atomic_write_text(Path(path), content)


def job_lock_path(path: Path) -> Path:
    return Path(f"{path}.lock")


def _read_job_payload(path: Path) -> str:
    if os.name != "nt":
        with Path(path).open(encoding="utf-8") as job_file:
            return job_file.read()

    last_error = None
    for attempt in range(_WINDOWS_READ_RETRIES):
        try:
            with Path(path).open(encoding="utf-8") as job_file:
                return job_file.read()
        except PermissionError as error:
            last_error = error
            if getattr(error, "winerror", None) != 5:
                raise
            if attempt == _WINDOWS_READ_RETRIES - 1:
                raise
            time.sleep(_WINDOWS_READ_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
    raise RuntimeError("unreachable")


def read_job(path: Path, *, expected_digest: str | None = None) -> JobRecord:
    record = JobRecord.from_dict(yaml.safe_load(_read_job_payload(Path(path))))
    if expected_digest is not None and record.authority_digest != expected_digest:
        raise ValueError("immutable job authority does not match launch reference")
    return record


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
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> list[JobRecord]:
    """Return persisted active jobs, optionally for one agent."""
    if isinstance(job_paths, Path):
        paths = tuple(sorted(job_paths.glob("*.yaml"))) if job_paths.is_dir() else ()
    else:
        paths = tuple(job_paths)
    records = []
    for path in paths:
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
