from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from flowgency.configuration import ConfigSnapshot, ResolvedTeamPaths
from flowgency.jobs.models import JobRecord
from flowgency.tickets.errors import TicketStorageError, WorkflowUnavailable
from flowgency.tickets.views import event_job_id
from flowgency.web.dependencies import FlowgencyServices
from flowgency.web.job_presentation import (
    friendly_status,
    friendly_trigger,
    load_team_jobs,
    routine_title,
    status_badge_classes,
)
from flowgency.web.logs import job_log_links
from flowgency.workflows.configuration import resolve_workflow_binding


def _normalize_activity_time(raw: datetime | str | None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        value = str(raw).strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _preferred_activity_time(record: JobRecord) -> datetime | None:
    if record.completed_at is not None:
        return _normalize_activity_time(record.completed_at)
    if record.started_at is not None:
        return _normalize_activity_time(record.started_at)
    return _normalize_activity_time(record.spec.created_at)


def _duration_label(value: float | int | None) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    total_seconds = int(value)
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _job_icon(status: str) -> str:
    return {
        "waiting_for_memory": "clock-3",
        "queued": "clock-3",
        "running": "loader-circle",
        "complete": "check",
        "failed": "x",
        "cancelled": "ban",
    }.get(status, "circle")


def _job_entry(team_id: str, agent_id: str, record: JobRecord, paths: ResolvedTeamPaths) -> dict[str, Any]:
    log_links = job_log_links(team_id, agent_id, record, paths.logs)
    metadata = [friendly_trigger(record.spec.trigger)]
    duration_label = _duration_label(record.duration_seconds)
    if duration_label:
        metadata.append(duration_label)
    metadata.append(record.spec.job_id[:8])

    no_logs_label = None
    if not log_links:
        no_logs_label = "No logs available" if record.status in {"complete", "failed", "cancelled"} else "No logs yet"

    at = _preferred_activity_time(record)
    return {
        "identity": f"job:{record.spec.job_id}",
        "kind": "job",
        "at": at,
        "title": routine_title(record.spec.routine_id, record.spec.prompt_source),
        "href": None,
        "summary": record.execution_summary or "",
        "metadata": tuple(metadata),
        "status_label": friendly_status(record.status),
        "status_classes": status_badge_classes(record.status),
        "icon": _job_icon(record.status),
        "log_links": log_links,
        "no_logs_label": no_logs_label,
    }


def _event_data_text(event: Any, key: str) -> str | None:
    data = getattr(event, "data", None)
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _ticket_event_title(event) -> str:
    if event.kind == "transitioned":
        source_state = _event_data_text(event, "source_state_name")
        destination_state = _event_data_text(event, "destination_state_name")
        if source_state and destination_state:
            return f"{source_state} -> {destination_state}"
        return event.summary
    return {
        "opened": "Ticket created",
        "started-work": "Started work",
        "ended-work": "Ended active work",
        "reported": "Report added",
    }.get(event.kind, event.summary)


def _ticket_icon(event) -> str:
    return {
        "opened": "plus",
        "started-work": "play",
        "ended-work": "square",
        "transitioned": "arrow-right",
        "reported": "message-square-text",
    }.get(event.kind, "square")


def _ticket_metadata(workflow_name: str, ticket_number: int, event: Any, title: str) -> tuple[str, ...]:
    metadata = [workflow_name]
    if event.kind == "transitioned":
        factual_label = (event.summary or "").strip()
        if factual_label and factual_label != title:
            metadata.append(factual_label)
    metadata.append(f"#{ticket_number}")
    return tuple(metadata)


def _event_summary(event, title: str) -> str:
    summary = (event.summary or "").strip()
    if event.kind == "transitioned":
        return ""
    return "" if summary == title else summary


def _ticket_entry(team_id: str, agent_id: str, workflow_name: str, ticket_record: Any, event: Any, job_index: dict[str, JobRecord], paths: ResolvedTeamPaths) -> dict[str, Any]:
    job_id = event_job_id(event)
    matching_job = None if job_id is None else job_index.get(job_id)
    log_links = () if matching_job is None else job_log_links(team_id, agent_id, matching_job, paths.logs)
    no_logs_label = None
    if not log_links:
        if matching_job is None:
            no_logs_label = "No logs available"
        else:
            no_logs_label = "No logs available" if matching_job.status in {"complete", "failed", "cancelled"} else "No logs yet"

    title = _ticket_event_title(event)
    at = _normalize_activity_time(event.at)
    return {
        "identity": f"ticket:{ticket_record.ref.workflow_id}:{ticket_record.id}:{event.id}",
        "kind": "ticket",
        "at": at,
        "title": title,
        "href": f"/{team_id}/workflows/{ticket_record.ref.workflow_id}?ticket={ticket_record.id}",
        "subject": ticket_record.title,
        "summary": _event_summary(event, title),
        "metadata": _ticket_metadata(workflow_name, ticket_record.number, event, title),
        "status_label": "",
        "status_classes": "",
        "icon": _ticket_icon(event),
        "log_links": log_links,
        "no_logs_label": no_logs_label,
    }


def _finalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    at = entry["at"]
    return {
        **entry,
        "time_label": at.strftime("%H:%M") if at is not None else "Unknown time",
        "date_label": at.date().isoformat() if at is not None else "Unknown date",
        "time_datetime": at.isoformat(timespec="seconds") if at is not None else None,
    }


def build_agent_activity(
    snapshot: ConfigSnapshot,
    team_id: str,
    agent_id: str,
    paths: ResolvedTeamPaths,
    services: FlowgencyServices,
) -> dict[str, Any]:
    records, warnings = load_team_jobs(services.job_store, team_id)
    team_cfg = snapshot.config.teams[team_id]
    selected_records = [
        record
        for record in records
        if record.spec.team_key == team_id and record.spec.agent_name == agent_id
    ]
    job_index = {record.spec.job_id: record for record in selected_records}
    entries = [
        _finalize_entry(_job_entry(team_id, agent_id, record, paths))
        for record in selected_records
    ]

    if services.tickets is not None:
        for workflow_id, workflow in team_cfg.workflows.items():
            try:
                binding = resolve_workflow_binding(snapshot, team_id, workflow_id)
                provider = services.tickets.storage_factory(binding.storage)
                ticket_records = provider.list(team_id, workflow_id)
            except (KeyError, TicketStorageError, WorkflowUnavailable, OSError, TypeError, ValueError) as exc:
                warnings = (*warnings, f"Skipped unreadable workflow activity: {workflow.name} ({exc})")
                continue
            for ticket_record in ticket_records:
                for event in ticket_record.events:
                    if event.actor != agent_id:
                        continue
                    entries.append(
                        _finalize_entry(
                            _ticket_entry(
                                team_id,
                                agent_id,
                                workflow.name,
                                ticket_record,
                                event,
                                job_index,
                                paths,
                            )
                        )
                    )

    entries.sort(
        key=lambda entry: (
            entry["at"] is not None,
            entry["at"].timestamp() if entry["at"] is not None else 0,
            entry["identity"],
        ),
        reverse=True,
    )
    entries = entries[:50]

    groups: list[dict[str, Any]] = []
    for entry in entries:
        if groups and groups[-1]["date_label"] == entry["date_label"]:
            groups[-1]["entries"].append(entry)
        else:
            groups.append({"date_label": entry["date_label"], "entries": [entry]})

    return {
        "activity_groups": tuple(groups),
        "activity_count": len(entries),
        "activity_warnings": tuple(warnings),
    }


__all__ = ["build_agent_activity"]