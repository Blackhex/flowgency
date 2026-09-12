from __future__ import annotations

from pathlib import Path

from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import JobRecord
from flowgency.jobs.store import read_job


def friendly_status(status: str) -> str:
    return {
        "waiting_for_memory": "Waiting for memory",
        "queued": "Queued",
        "running": "Running",
        "complete": "Complete",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(status, status.replace("_", " ").title())


def status_badge_classes(status: str) -> str:
    return {
        "waiting_for_memory": (
            "bg-amber-100 text-amber-800 dark:bg-amber-900/50 "
            "dark:text-amber-100"
        ),
        "queued": (
            "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-100"
        ),
        "running": (
            "bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-100"
        ),
        "complete": (
            "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 "
            "dark:text-emerald-100"
        ),
        "failed": (
            "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-100"
        ),
        "cancelled": (
            "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-100"
        ),
    }.get(
        status,
        "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-100",
    )


def friendly_trigger(trigger: str) -> str:
    return {
        "scheduled_prompt": "Scheduled routine",
        "manual_prompt": "Manual routine",
        "decision": "Decision",
        "decision_retry": "Decision retry",
    }.get(trigger, trigger.replace("_", " ").title())


def routine_title(routine_id: str | None, prompt_source: dict[str, object] | None) -> str:
    if prompt_source and isinstance(prompt_source.get("title"), str) and prompt_source.get("title"):
        return str(prompt_source["title"])
    if routine_id:
        return routine_id
    return "Ad hoc"


def load_team_jobs(job_store: JobStore | None, team_id: str) -> tuple[tuple[JobRecord, ...], tuple[str, ...]]:
    if job_store is None:
        return (), ()

    trusted_root = job_store.team_root(team_id).resolve(strict=False)
    records: list[JobRecord] = []
    warnings: list[str] = []
    for path in job_store.paths(team_id):
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(trusted_root)
        except ValueError:
            warnings.append(f"Skipped job path outside team root: {path.name}")
            continue
        try:
            record = read_job(path)
        except (OSError, TypeError, ValueError):
            warnings.append(f"Skipped unreadable job record: {path.name}")
            continue
        if record.spec.team_key != team_id:
            warnings.append(f"Skipped mismatched job record: {path.name}")
            continue
        if resolved != job_store.path(team_id, record.spec.job_id):
            warnings.append(f"Skipped misplaced job record: {path.name}")
            continue
        records.append(record)
    return tuple(records), tuple(warnings)


__all__ = [
    "friendly_status",
    "friendly_trigger",
    "load_team_jobs",
    "routine_title",
    "status_badge_classes",
]