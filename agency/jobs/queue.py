"""The global job queue and its worker pool."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import yaml

from agency.fs.locks import ResourceBusyError, exclusive_lock

from .authority import JobStore
from .launcher import JobLauncher, default_launcher
from .models import JobRecord
from .reconciliation import reconcile_jobs
from .store import (
    InvalidJobTransition,
    claim_job,
    is_launchable,
    occupies_slot,
    queue_lock_path,
    read_job,
    transition_job,
)

log = logging.getLogger("agency.jobs.queue")

# The records a drain has to trust before it counts free slots.
SLOT_STATUSES = frozenset({"running", "waiting_for_memory"})

# How long a drain waits for another drainer before leaving the queue to it.
# Whoever holds the lock is doing the same work, so giving up costs nothing but
# a delay until the next drain, and it keeps the dashboard lifespan bounded.
LOCK_TIMEOUT_SECONDS = 5.0


class QueueEntry(NamedTuple):
    team_id: str
    record: JobRecord
    path: Path


class QueueView(NamedTuple):
    running: int
    waiting: tuple[QueueEntry, ...]
    pool: int


def _entries(config, memory_store: Path) -> list[QueueEntry]:
    store = JobStore(memory_store)
    entries: list[QueueEntry] = []
    for team_id in sorted(config.teams):
        team_dir = store.team_root(team_id)
        if not team_dir.is_dir():
            continue
        for path in sorted(team_dir.glob("*.yaml")):
            try:
                entries.append(QueueEntry(team_id, read_job(path), path))
            except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
                log.warning("ignoring malformed job record %s: %s", path, error)
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


def _team_roots(config) -> dict:
    return {
        team_id: {"team_root": str(team.path)}
        for team_id, team in config.teams.items()
    }


def _start(entry: QueueEntry, store: JobStore, launcher: JobLauncher) -> bool:
    """Launch one waiting job and claim its slot. False means it did not start."""
    try:
        record = read_job(entry.path)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        log.warning("ignoring malformed job record %s: %s", entry.path, error)
        return False
    if not is_launchable(record):
        return False
    reference = store.reference(
        entry.team_id,
        record.spec.job_id,
        record.authority_digest,
    )
    try:
        result = launcher.launch(reference)
    except Exception as error:
        log.error("could not launch %s: %s", record.spec.job_id, error)
        try:
            transition_job(
                entry.path,
                "queued",
                "failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                execution_summary=f"Launch error: {error}",
            )
        except InvalidJobTransition:
            log.info("%s left the queue before its failure was recorded",
                     record.spec.job_id)
        return False
    try:
        claim_job(entry.path, result.worker_pid)
    except InvalidJobTransition:
        # The worker beat us to its own record. It is running; never fail it.
        log.info("%s transitioned before it was claimed", record.spec.job_id)
    return True


def drain(
    config,
    *,
    memory_store: Path,
    launcher: JobLauncher | None = None,
    full_reconcile: bool = False,
) -> int:
    """Start waiting jobs, oldest due first, while the pool has room.

    ``full_reconcile`` also sweeps terminal records for pin release and
    decision projection. That is a startup and dispatch-cycle concern; a
    drain only has to trust the records that could be holding a slot.
    """
    selected = launcher or default_launcher()
    store = JobStore(memory_store)
    started = 0
    with ExitStack() as stack:
        try:
            stack.enter_context(
                exclusive_lock(
                    queue_lock_path(store.root),
                    wait=True,
                    timeout=LOCK_TIMEOUT_SECONDS,
                )
            )
        except ResourceBusyError:
            log.info("another drainer holds the queue; leaving the work to it")
            return 0
        reconcile_jobs(
            _team_roots(config),
            memory_store_root=memory_store,
            statuses=None if full_reconcile else SLOT_STATUSES,
        )
        view = queue_snapshot(config, memory_store=memory_store)
        capacity = view.pool - view.running
        for entry in view.waiting:
            if capacity <= 0:
                break
            if not _start(entry, store, selected):
                continue
            capacity -= 1
            started += 1
    return started
