"""Atomic YAML persistence for durable agent jobs."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
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


def team_operation_lock_path(team_root: Path) -> Path:
    return Path(team_root) / "locks" / ".operations.lock"


def canonical_team_operation_lock_paths(
    *team_roots: Path,
) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for team_root in team_roots:
        lock_path = team_operation_lock_path(team_root).resolve(strict=False)
        unique[os.path.normcase(str(lock_path))] = lock_path
    return tuple(unique[key] for key in sorted(unique))


def acquire_team_operation_locks(*team_roots: Path) -> ExitStack:
    stack = ExitStack()
    try:
        for lock_path in canonical_team_operation_lock_paths(*team_roots):
            stack.enter_context(exclusive_lock(lock_path, wait=True))
    except Exception:
        stack.close()
        raise
    return stack


@contextmanager
def revision_bound_team_operation(
    config_store: ConfigStore,
    *,
    team_ids: tuple[str, ...] = (),
    proposed_paths: tuple[Path, ...] = (),
    all_teams: bool = False,
    expected_revision: str | None = None,
):
    initial = config_store.load()
    relevant_ids = (
        tuple(sorted(initial.config.teams))
        if all_teams
        else tuple(sorted(set(team_ids)))
    )
    initial_identity = _team_path_identity(initial, relevant_ids)
    lock_paths = tuple(initial_identity.values()) + tuple(proposed_paths)
    with acquire_team_operation_locks(*lock_paths):
        locked = config_store.load()
        locked_ids = (
            tuple(sorted(locked.config.teams))
            if all_teams
            else relevant_ids
        )
        if (
            locked.revision != initial.revision
            or locked_ids != relevant_ids
            or _team_path_identity(locked, locked_ids) != initial_identity
        ):
            raise ConfigConflictError(
                "config team paths changed while acquiring operation locks"
            )
        if (
            expected_revision is not None
            and locked.revision != expected_revision
        ):
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        yield locked


def _team_path_identity(
    snapshot: ConfigSnapshot,
    team_ids: tuple[str, ...],
) -> dict[str, Path]:
    identity: dict[str, Path] = {}
    for team_id in team_ids:
        try:
            identity[team_id] = (
                snapshot.config.teams[team_id].path.resolve()
            )
        except KeyError as exc:
            raise ValueError(f"Unknown team: {team_id}") from exc
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


def worker_alive(pid: int | None) -> "bool | None":
    # Lazy import breaks the store -> reconciliation -> store circular dependency.
    import agency.jobs.reconciliation as _rec
    return _rec.worker_alive(pid)


def queue_lock_path(store_root: "Path") -> "Path":
    return Path(store_root) / ".queue.lock"


# A launched job keeps its slot for this long before the drain may start it
# again. It covers a launcher that reports no pid, such as systemd-run, whose
# worker has not yet transitioned the record.
LAUNCH_GRACE_SECONDS = 300


def _is_launched(record: JobRecord) -> bool:
    return record.launched_at is not None or record.worker_pid is not None


def _launch_abandoned(record: JobRecord) -> bool:
    """Whether a launched job has provably failed to become a live worker."""
    if record.worker_pid is not None:
        return worker_alive(record.worker_pid) is False
    if record.launched_at is None:
        return False
    try:
        launched = datetime.fromisoformat(record.launched_at)
    except (TypeError, ValueError):
        return True
    if launched.tzinfo is None:
        launched = launched.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - launched).total_seconds()
    return age > LAUNCH_GRACE_SECONDS


def occupies_slot(record: JobRecord) -> bool:
    """Whether this record is holding one of the pool's slots."""
    if record.status in {"running", "waiting_for_memory"}:
        return True
    if record.status != "queued":
        return False
    return _is_launched(record) and not _launch_abandoned(record)


def is_launchable(record: JobRecord) -> bool:
    """Whether the drain may start this record now."""
    if record.status != "queued":
        return False
    return not _is_launched(record) or _launch_abandoned(record)


def claim_job(path: Path, worker_pid: "int | None") -> JobRecord:
    """Record that a queued job has been launched, without changing its status.

    The stamp is what holds the slot. ``worker_pid`` stays optional because a
    launcher that hands the process to an init system cannot report one.
    """
    with exclusive_lock(job_lock_path(path), wait=True):
        record = read_job(path)
        if record.status != "queued":
            raise InvalidJobTransition(
                f"Only queued jobs can be claimed, found {record.status!r}"
            )
        updated = replace(
            record,
            worker_pid=worker_pid,
            launched_at=datetime.now(timezone.utc).isoformat(),
        )
        write_job(path, updated)
        return updated


ACTIVE_STATUSES = frozenset({"queued", "waiting_for_memory", "running"})
TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})


def _iter_job_records(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None,
):
    if isinstance(job_paths, Path):
        paths = tuple(sorted(job_paths.glob("*.yaml"))) if job_paths.is_dir() else ()
    else:
        paths = tuple(job_paths)
    for path in paths:
        try:
            record = read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            continue
        if agent_name is not None and record.spec.agent_name != agent_name:
            continue
        yield record


def active_jobs(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> list[JobRecord]:
    """Return persisted active jobs, optionally for one agent."""
    return [
        record
        for record in _iter_job_records(job_paths, agent_name)
        if record.status in ACTIVE_STATUSES
    ]


def _terminal_sort_key(record: JobRecord) -> tuple[str, str]:
    stamp = record.completed_at or record.started_at or record.spec.created_at
    return (stamp or "", record.spec.job_id)


def _latest_by_status(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None,
    statuses: frozenset[str],
) -> "JobRecord | None":
    records = [
        record
        for record in _iter_job_records(job_paths, agent_name)
        if record.status in statuses
    ]
    if not records:
        return None
    return max(records, key=_terminal_sort_key)


def latest_terminal_job(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> "JobRecord | None":
    """Return the newest finished job record, optionally for one agent."""
    return _latest_by_status(job_paths, agent_name, TERMINAL_STATUSES)


EXECUTED_STATUSES = frozenset({"complete", "failed"})


def latest_executed_job(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> "JobRecord | None":
    """Return the newest complete or failed record; cancellations are inert."""
    return _latest_by_status(job_paths, agent_name, EXECUTED_STATUSES)
