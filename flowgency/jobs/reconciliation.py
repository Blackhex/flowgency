"""Conservative startup reconciliation for durable running jobs."""

import logging
import os
from types import SimpleNamespace
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from flowgency.configuration.store import ConfigStore
from flowgency.blueprints.cache import release_pin
from .authority import JobStore
from .execution import _merge_result_metadata, _ticket_cleanup_metadata, project_decision
from flowgency.memory.recovery import recover_publications
from flowgency.jobs.processes import ProcessStopEvidence
from flowgency.tickets.access import TicketAccessRegistry
from flowgency.tickets.service import TicketService
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.library import WorkflowLibrary
from .store import InvalidJobTransition, read_job, transition_job
from .tickets import TicketJobCoordinator


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


def _ticket_cleanup_evidence(record) -> ProcessStopEvidence | None:
    metadata = record.result_metadata or {}
    cleanup = metadata.get("ticket_cleanup")
    if not isinstance(cleanup, dict):
        return None
    pending = cleanup.get("pending_cleanup") or ()
    retryable = cleanup.get("requires_retry") is True or cleanup.get("status") in {
        "pending",
        "partial",
        "error",
    }
    if cleanup.get("confirmed") is not True or (not pending and not retryable):
        return None
    if cleanup.get("job_id") != record.spec.job_id:
        return None
    generation = cleanup.get("generation")
    reason = cleanup.get("reason")
    if not isinstance(generation, str) or not generation:
        return None
    if not isinstance(reason, str) or not reason:
        reason = "confirmed"
    return ProcessStopEvidence(
        job_id=record.spec.job_id,
        generation=generation,
        confirmed=True,
        reason=reason,
    )


def _ticket_coordinator(job_store: JobStore, team_id: str, team: dict):
    config_path = team.get("config_path")
    if not config_path:
        return None
    config_store = ConfigStore(Path(config_path))
    workflow_library = config_store.load().config.flowgency.workflow_library
    if workflow_library is None:
        return None
    registry = TicketAccessRegistry(job_store)
    service = TicketService(
        config_store,
        WorkflowLibrary(Path(workflow_library)),
        resolve_storage,
        registry.validate_context,
        clock=lambda: datetime.now(timezone.utc),
    )
    return TicketJobCoordinator(
        service=service,
        job_store=job_store,
        config_store=config_store,
        submitter=lambda request: None,
    )


def _reconcile_ticket_reservations(job_store: JobStore, team_id: str, team: dict) -> None:
    reservation_root = job_store.team_root(team_id) / "ticket-runs"
    if not reservation_root.is_dir() or not any(reservation_root.glob("*.json")):
        return
    try:
        coordinator = _ticket_coordinator(job_store, team_id, team)
    except Exception as error:
        logger.warning(
            "Skipping ticket reservation recovery for team %s: %s",
            team_id,
            error,
        )
        return
    if coordinator is None:
        return
    try:
        reservations = coordinator.iter_reservations(team_id)
    except Exception as error:
        logger.warning(
            "Failed to read ticket reservations for team %s: %s",
            team_id,
            error,
        )
        return
    for reservation in reservations:
        try:
            coordinator.reconcile_pending_submission(
                team_id,
                reservation.target.ref,
                reservation.operation_id,
            )
        except Exception as error:
            logger.warning(
                "Failed to reconcile durable ticket reservation %s for team %s: %s",
                reservation.operation_id,
                team_id,
                error,
            )


def _record_dead_worker_ticket_cleanup(job_store: JobStore, team_id: str, path: Path, record) -> None:
    authority = job_store.reference(team_id, record.spec.job_id, record.authority_digest)
    registry = TicketAccessRegistry(job_store)
    try:
        targets = registry.read_original_targets(authority)
    except Exception:
        targets = ()
    try:
        registry.revoke(authority)
    except Exception:
        pass
    if not targets:
        return
    generation = next((target.generation for target in targets if target.generation), "")
    _merge_result_metadata(
        path,
        {
            "ticket_cleanup": _ticket_cleanup_metadata(
                ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=generation,
                    confirmed=False,
                    reason="worker-missing",
                ),
                SimpleNamespace(
                    cleared=(),
                    pending_cleanup=tuple(target.ref for target in targets),
                ),
            )
        },
    )


def _retry_ticket_cleanup(job_store: JobStore, team_id: str, record) -> None:
    evidence = _ticket_cleanup_evidence(record)
    if evidence is None:
        return
    authority = job_store.reference(team_id, record.spec.job_id, record.authority_digest)
    config_store = ConfigStore(Path(record.spec.config_path))
    try:
        workflow_library = config_store.load().config.flowgency.workflow_library
    except Exception:
        workflow_library = None
    library_root = Path(workflow_library) if workflow_library is not None else Path(record.spec.team_root)
    registry = TicketAccessRegistry(job_store)
    service = TicketService(
        config_store,
        WorkflowLibrary(library_root),
        resolve_storage,
        registry.validate_context,
        clock=lambda: datetime.now(timezone.utc),
    )
    coordinator = TicketJobCoordinator(
        service=service,
        job_store=job_store,
        config_store=config_store,
        submitter=lambda request: None,
    )
    cleanup = coordinator.cleanup(authority, evidence)
    _merge_result_metadata(
        authority.path,
        {"ticket_cleanup": _ticket_cleanup_metadata(evidence, cleanup)},
    )


def reconcile_jobs(
    teams: dict,
    *,
    memory_store_root: Path,
    statuses: frozenset[str] | None = None,
) -> ReconciliationResult:
    """Fail running jobs only when their worker is confirmed absent.

    ``statuses`` narrows the sweep to the records a caller actually cares
    about. Passing the active statuses skips the terminal pass, whose pin
    release and decision projection rewrite every terminal record ever
    written and belong to startup rather than to every drain.
    """
    failed = 0
    left_running = 0
    job_store = JobStore(memory_store_root)
    job_stores = {
        team_id: {
            "job_store": (job_store.root / team_id),
            "team_root": team["team_root"],
        }
        for team_id, team in sorted(teams.items())
        if team.get("team_root")
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
    for team_id, team in teams.items():
        if not team.get("team_root"):
            continue
        _reconcile_ticket_reservations(job_store, team_id, team)
        records: list[tuple[Path, object]] = []
        for path in job_store.paths(team_id):
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
            if statuses is not None and record.status not in statuses:
                continue
            if recovery_unavailable or record.spec.job_id in blocked_job_ids:
                logger.warning(
                    "Skipping reconciliation for job %s because memory "
                    "recovery requires manual intervention",
                    record.spec.job_id,
                )
                continue
            if record.status == "cancelled":
                try:
                    _retry_ticket_cleanup(job_store, team_id, record)
                except Exception as error:
                    logger.warning(
                        "Failed to retry ticket cleanup for job %s: %s",
                        record.spec.job_id,
                        error,
                    )
                continue
            if record.status in {"complete", "failed"}:
                try:
                    _retry_ticket_cleanup(job_store, team_id, record)
                    record = read_job(path)
                except Exception as error:
                    logger.warning(
                        "Failed to retry ticket cleanup for job %s: %s",
                        record.spec.job_id,
                        error,
                    )
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
            _record_dead_worker_ticket_cleanup(job_store, team_id, path, record)
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
