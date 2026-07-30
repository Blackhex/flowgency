"""The global job queue and its worker pool."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from agency.dispatch.install import get_timer_status
from agency.fs.locks import exclusive_lock

from .authority import JobStore
from .launcher import JobLauncher, default_launcher
from .models import JobRecord
from .reconciliation import reconcile_jobs
from .store import (
    claim_job,
    is_launchable,
    occupies_slot,
    queue_lock_path,
    read_job,
    write_job,
)

log = logging.getLogger("agency.jobs.queue")


class QueueEntry(NamedTuple):
    group_id: str
    record: JobRecord
    path: Path


class QueueView(NamedTuple):
    running: int
    waiting: tuple[QueueEntry, ...]
    pool: int


def _entries(config, memory_store: Path) -> list[QueueEntry]:
    store = JobStore(memory_store)
    entries: list[QueueEntry] = []
    for group_id in sorted(config.groups):
        group_dir = store.group_root(group_id)
        if not group_dir.is_dir():
            continue
        for path in sorted(group_dir.glob("*.yaml")):
            try:
                entries.append(QueueEntry(group_id, read_job(path), path))
            except Exception:
                continue
    return entries


def _order_key(entry: QueueEntry) -> tuple[str, str]:
    record = entry.record
    return (record.due_at or record.spec.created_at or "", record.spec.job_id)


def queue_snapshot(config, *, memory_store: Path) -> QueueView:
    """Current occupancy and the waiting jobs, oldest due first."""
    entries = _entries(config, memory_store)
    waiting = sorted(
        (entry for entry in entries if is_launchable(entry.record)),
        key=_order_key,
    )
    running = sum(1 for entry in entries if occupies_slot(entry.record))
    return QueueView(running, tuple(waiting), config.agency.jobs.pool)


def _group_roots(config) -> dict:
    return {
        group_id: {"group_root": str(group.path)}
        for group_id, group in config.groups.items()
    }


def has_drainer(config, *, memory_store: Path, config_path: Path) -> bool:
    """Whether anything will start a job that is left waiting."""
    if any(occupies_slot(entry.record) for entry in _entries(config, memory_store)):
        return True
    status = get_timer_status(config_path, config.agency.dispatch.interval)
    return bool(status.get("installed") and status.get("enabled"))


def drain(config, *, memory_store: Path, launcher: JobLauncher | None = None) -> int:
    """Start waiting jobs, oldest due first, while the pool has room."""
    selected = launcher or default_launcher()
    store = JobStore(memory_store)
    started = 0
    with exclusive_lock(queue_lock_path(store.root), wait=True):
        reconcile_jobs(_group_roots(config), memory_store_root=memory_store)
        view = queue_snapshot(config, memory_store=memory_store)
        capacity = view.pool - view.running
        for entry in view.waiting:
            if capacity <= 0:
                break
            reference = store.reference(
                entry.group_id,
                entry.record.spec.job_id,
                entry.record.authority_digest,
            )
            try:
                result = selected.launch(reference)
            except Exception as error:
                log.error("could not launch %s: %s", entry.record.spec.job_id, error)
                write_job(
                    entry.path,
                    replace(
                        entry.record,
                        status="failed",
                        execution_summary=f"Launch error: {error}",
                    ),
                )
                continue
            claim_job(entry.path, result.worker_pid)
            capacity -= 1
            started += 1
    return started
