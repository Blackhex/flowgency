"""Conservative startup reconciliation for durable running jobs."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agency.blueprints.cache import release_pin
from .authority import JobStore
from .execution import project_decision
from agency.memory.recovery import recover_publications
from .store import InvalidJobTransition, read_job, transition_job


logger = logging.getLogger(__name__)


def _release_job_pin(record) -> None:
    release_pin(
        record.spec.blueprint.cache_root,
        record.spec.blueprint.cache_ref,
        record.spec.job_id,
    )


@dataclass(frozen=True)
class ReconciliationResult:
    failed: int = 0
    left_running: int = 0


def worker_alive(pid: int | None) -> bool | None:
    """Return worker liveness, or None when process absence is not confirmed."""
    if not pid or pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        try:
            return win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE
        finally:
            handle.Close()
    except Exception:
        return None


def reconcile_jobs(
    groups: dict,
    *,
    memory_store_root: Path,
) -> ReconciliationResult:
    """Fail running jobs only when their worker is confirmed absent."""
    failed = 0
    left_running = 0
    job_store = JobStore(memory_store_root)
    job_stores = {
        group_id: {
            "job_store": (job_store.root / group_id),
            "group_path": group["path"],
        }
        for group_id, group in sorted(groups.items())
        if group.get("path")
    }
    blocked_job_ids: set[str] = set()
    recovery_unavailable = False
    try:
        recovery = recover_publications(memory_store_root, job_stores)
        blocked_job_ids.update(recovery.blocked_job_ids)
        for error in recovery.errors:
            logger.warning(
                "Memory recovery requires manual intervention: %s",
                error,
            )
    except Exception as error:
        recovery_unavailable = True
        logger.warning(
            "Global memory recovery failed and requires manual intervention: %s",
            error,
        )
    for group_id, group in groups.items():
        if not group.get("path"):
            continue
        records: list[tuple[Path, object]] = []
        for path in job_store.paths(group_id):
            try:
                record = read_job(path)
            except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
                logger.warning("Ignoring malformed job record %s: %s", path, error)
                continue
            records.append((path, record))
        for path, _ in records:
            try:
                record = read_job(path)
            except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
                logger.warning("Ignoring malformed job record %s: %s", path, error)
                continue
            if recovery_unavailable or record.spec.job_id in blocked_job_ids:
                logger.warning(
                    "Skipping reconciliation for job %s because memory "
                    "recovery requires manual intervention",
                    record.spec.job_id,
                )
                continue
            if record.status in {"complete", "failed"}:
                try:
                    _release_job_pin(record)
                except Exception:
                    pass
                try:
                    project_decision(record)
                except Exception as error:
                    logger.warning(
                        "Failed to project terminal job %s to its decision: %s",
                        record.spec.job_id,
                        error,
                    )
                continue
            if record.status not in {"running", "waiting_for_memory"}:
                continue
            if worker_alive(record.worker_pid) is not False:
                left_running += 1
                continue

            summary = f"Worker process (PID {record.worker_pid}) was not found."
            try:
                record = transition_job(
                    path,
                    record.status,
                    "failed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    execution_summary=summary,
                )
            except InvalidJobTransition:
                continue
            failed += 1
            try:
                _release_job_pin(record)
            except Exception:
                pass
            try:
                project_decision(record)
            except Exception as error:
                logger.warning(
                    "Failed to project reconciled job %s to its decision: %s",
                    record.spec.job_id,
                    error,
                )
    return ReconciliationResult(failed=failed, left_running=left_running)
