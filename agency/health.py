"""Agent health signals derived from schedules and job outcomes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from agency.dispatch.schedule import at_marker_path, every_marker_path, parse_every

OVERDUE = "overdue"
DUE = "due"


class RoutineSchedule(NamedTuple):
    routine_id: str
    at: str | None
    every: str | None
    enabled: bool
    conditional: bool


def grace_window(dispatch_interval: int) -> timedelta:
    return timedelta(minutes=dispatch_interval + 2)


def routine_schedules(routines: Iterable[object]) -> tuple[RoutineSchedule, ...]:
    """Normalize config routines, given as models or mappings, for scheduling."""
    schedules = []
    for routine in routines:
        schedule = _field(routine, "schedule") or {}
        schedules.append(
            RoutineSchedule(
                routine_id=str(_field(routine, "id") or ""),
                at=_optional_text(_field(schedule, "at")),
                every=_optional_text(_field(schedule, "every")),
                enabled=_field(routine, "enabled") is not False,
                conditional=bool(_field(routine, "condition")),
            )
        )
    return tuple(schedules)


def schedule_state(
    schedules: Iterable[RoutineSchedule],
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    """Return the strongest lateness across an agent's routines."""
    state = None
    for schedule in schedules:
        current = _routine_state(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
        if current == OVERDUE:
            return OVERDUE
        if current == DUE:
            state = DUE
    return state


def evaluate_agent_health(
    *,
    has_run: bool,
    last_job_failed: bool,
    schedule: str | None,
) -> str:
    if last_job_failed or schedule == OVERDUE:
        return "red"
    if schedule == DUE:
        return "amber"
    if not has_run:
        return "gray"
    return "green"


def _routine_state(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    if not schedule.enabled or schedule.conditional or not schedule.routine_id:
        return None
    if schedule.at:
        return _at_state(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    if schedule.every:
        return _every_state(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    return None


def _at_state(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    day = now.strftime("%Y-%m-%d")
    try:
        occurrence = datetime.strptime(f"{day} {schedule.at}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if now < occurrence:
        return None
    marker = at_marker_path(logs_root, agent_name, schedule.routine_id, day)
    if marker.exists():
        return None
    return _lateness(now, occurrence, grace)


def _every_state(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    period = parse_every(schedule.every)
    if period is None:
        return None
    marker = every_marker_path(logs_root, agent_name, schedule.routine_id)
    try:
        fired_at = datetime.fromtimestamp(marker.stat().st_mtime)
    except OSError:
        return None
    return _lateness(now, fired_at + period, grace)


def _lateness(now: datetime, due_at: datetime, grace: timedelta) -> str | None:
    if now < due_at:
        return None
    return OVERDUE if now > due_at + grace else DUE


def _field(source: object, name: str):
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
