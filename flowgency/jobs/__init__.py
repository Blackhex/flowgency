from .artifacts import JobArtifact, retain_failed_stage
from .authority import JobAuthorityError, JobAuthorityRef, JobStore
from .launch_view import create_launch_view
from .launcher import (
    DetachedProcessLauncher,
    JobLauncher,
    LaunchResult,
    SystemdRunLauncher,
    default_launcher,
)
from .models import (
    BlueprintRef,
    JobHandle,
    JobRecord,
    JobRequest,
    JobSpec,
    MemoryBinding,
    PromptSnapshot,
    RuntimePolicySnapshot,
    TicketJobTarget,
)
from .processes import ProcessStopEvidence, may_clear_active_work
from .store import (
    active_jobs,
    cancel_job,
    team_operation_lock_path,
    latest_executed_job,
    latest_terminal_job,
)


def reconcile_jobs(teams: dict, *, memory_store_root):
    from .reconciliation import reconcile_jobs as _reconcile_jobs

    return _reconcile_jobs(teams, memory_store_root=memory_store_root)


def drain(config, *, memory_store, launcher=None, full_reconcile=False, config_path=None):
    from .queue import drain as _drain

    return _drain(
        config,
        memory_store=memory_store,
        launcher=launcher,
        full_reconcile=full_reconcile,
        config_path=config_path,
    )


def queue_snapshot(config, *, memory_store):
    from .queue import queue_snapshot as _queue_snapshot

    return _queue_snapshot(config, memory_store=memory_store)


def __getattr__(name):
    if name in {"JobSubmissionError", "submit_job_request"}:
        from .submission import JobSubmissionError, submit_job_request

        return {
            "JobSubmissionError": JobSubmissionError,
            "submit_job_request": submit_job_request,
        }[name]
    if name in {"JobValidationError", "resolve_job_request"}:
        from .resolution import JobValidationError, resolve_job_request

        return {
            "JobValidationError": JobValidationError,
            "resolve_job_request": resolve_job_request,
        }[name]
    if name in {"CleanupResult", "TicketJobCoordinator"}:
        from .tickets import CleanupResult, TicketJobCoordinator

        return {
            "CleanupResult": CleanupResult,
            "TicketJobCoordinator": TicketJobCoordinator,
        }[name]
    raise AttributeError(name)


__all__ = [
    "DetachedProcessLauncher",
    "JobAuthorityError",
    "JobAuthorityRef",
    "SystemdRunLauncher",
    "JobStore",
    "active_jobs",
    "BlueprintRef",
    "cancel_job",
    "drain",
    "team_operation_lock_path",
    "create_launch_view",
    "default_launcher",
    "JobArtifact",
    "JobHandle",
    "JobLauncher",
    "JobRequest",
    "JobRecord",
    "JobSpec",
    "JobSubmissionError",
    "JobValidationError",
    "LaunchResult",
    "latest_executed_job",
    "latest_terminal_job",
    "MemoryBinding",
    "PromptSnapshot",
    "ProcessStopEvidence",
    "queue_snapshot",
    "reconcile_jobs",
    "resolve_job_request",
    "retain_failed_stage",
    "RuntimePolicySnapshot",
    "submit_job_request",
    "TicketJobCoordinator",
    "TicketJobTarget",
    "CleanupResult",
    "may_clear_active_work",
]
