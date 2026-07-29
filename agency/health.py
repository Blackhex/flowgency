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


class Lateness(NamedTuple):
    routine_id: str
    state: str
    due_at: datetime



def grace_window(dispatch_interval: int) -> timedelta:
    return timedelta(minutes=dispatch_interval + 2)


def elapsed_coarse(delta: timedelta) -> str:
    """Render a duration as a single unit, for the fleet cards."""
    minutes = max(int(delta.total_seconds()) // 60, 0)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def elapsed_precise(delta: timedelta) -> str:
    """Render a duration with its next smaller unit, for queue sentences."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return _compound(seconds // 60, "m", seconds % 60, "s")
    if seconds < 86400:
        return _compound(seconds // 3600, "h", seconds % 3600 // 60, "m")
    return _compound(seconds // 86400, "d", seconds % 86400 // 3600, "h")


def _compound(major: int, major_unit: str, minor: int, minor_unit: str) -> str:
    if minor == 0:
        return f"{major}{major_unit}"
    return f"{major}{major_unit} {minor}{minor_unit}"


def last_fired_at(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
) -> datetime | None:
    """When a routine's marker says it last fired, or None."""
    if schedule.at:
        marker = at_marker_path(
            logs_root, agent_name, schedule.routine_id, now.strftime("%Y-%m-%d")
        )
    elif schedule.every:
        marker = every_marker_path(logs_root, agent_name, schedule.routine_id)
    else:
        return None
    try:
        return datetime.fromtimestamp(marker.stat().st_mtime)
    except OSError:
        return None



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


def schedule_lateness(
    schedules: Iterable[RoutineSchedule],
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
    """Return the strongest lateness across an agent's routines."""
    best: Lateness | None = None
    for schedule in schedules:
        current = _routine_lateness(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
        if current is None:
            continue
        if best is None or _rank(current) < _rank(best):
            best = current
    return best


def schedule_state(
    schedules: Iterable[RoutineSchedule],
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    """Return the strongest lateness across an agent's routines."""
    lateness = schedule_lateness(
        schedules,
        logs_root=logs_root,
        agent_name=agent_name,
        now=now,
        grace=grace,
    )
    return lateness.state if lateness is not None else None


def _rank(lateness: Lateness) -> tuple[int, datetime]:
    return (0 if lateness.state == OVERDUE else 1, lateness.due_at)


def _routine_lateness(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
    if not schedule.enabled or schedule.conditional or not schedule.routine_id:
        return None
    if schedule.at:
        return _at_lateness(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    if schedule.every:
        return _every_lateness(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    return None


def _at_lateness(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
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
    return _as_lateness(schedule.routine_id, now, occurrence, grace)


def _every_lateness(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
    period = parse_every(schedule.every)
    if period is None:
        return None
    marker = every_marker_path(logs_root, agent_name, schedule.routine_id)
    try:
        fired_at = datetime.fromtimestamp(marker.stat().st_mtime)
    except OSError:
        return None
    return _as_lateness(schedule.routine_id, now, fired_at + period, grace)


def _as_lateness(
    routine_id: str,
    now: datetime,
    due_at: datetime,
    grace: timedelta,
) -> Lateness | None:
    if now < due_at:
        return None
    state = OVERDUE if now > due_at + grace else DUE
    return Lateness(routine_id=routine_id, state=state, due_at=due_at)


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


class AgentHealth(NamedTuple):
    color: str
    kind: str
    routine_id: str | None
    due_at: datetime | None
    late: timedelta | None


def describe_agent_health(
    *,
    has_run: bool,
    last_job_failed: bool,
    lateness: Lateness | None,
    now: datetime,
) -> AgentHealth:
    """Pair the health colour with the reason that produced it."""
    color = evaluate_agent_health(
        has_run=has_run,
        last_job_failed=last_job_failed,
        schedule=lateness.state if lateness is not None else None,
    )
    if last_job_failed:
        return AgentHealth(color, "job_failed", None, None, None)
    if lateness is not None:
        return AgentHealth(
            color,
            lateness.state,
            lateness.routine_id,
            lateness.due_at,
            now - lateness.due_at,
        )
    if not has_run:
        return AgentHealth(color, "never_run", None, None, None)
    return AgentHealth(color, "healthy", None, None, None)


def _field(source: object, name: str):
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
