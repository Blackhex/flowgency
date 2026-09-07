from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flowgency.clock import now as clock_now
from flowgency.configuration import ConfigSnapshot, resolve_team_paths
from flowgency.configuration.models import MemorySelector
from flowgency.health import (
    elapsed_coarse,
    grace_window,
    last_fired_at,
    next_occurrence,
    relative_future,
    routine_schedules,
    schedule_lateness,
)
from flowgency.memory import select_effective_memory


if TYPE_CHECKING:
    from .editor import PreparedRoutines
    from .forms import RoutinesDraft


@dataclass(frozen=True)
class SavedRoutineStatus:
    source_index: int
    original_id: str
    last_fired: str
    next_due: str


@dataclass(frozen=True)
class RoutineSummary:
    key: str
    source_index: int | None
    id: str
    enabled: bool
    prompt_scope: str
    prompt_name: str
    schedule: str
    memory: str
    arguments: tuple[str, ...]
    recovery: str


def routine_status(snapshot, team_id: str, instance) -> list[dict[str, Any]]:
    rows = []
    for status in saved_status(snapshot, team_id, instance.name):
        schedule = routine_schedules(instance.routines)[status.source_index]
        if schedule.conditional:
            spec = "conditional"
        elif schedule.at:
            spec = f"at {schedule.at}"
        elif schedule.every:
            spec = f"every {schedule.every}"
        else:
            spec = "no schedule"
        rows.append(
            {
                "routine_id": status.original_id,
                "enabled": schedule.enabled,
                "schedule": spec,
                "last_fired": status.last_fired,
                "next_due": status.next_due,
            }
        )
    return rows


def saved_status(
    snapshot: ConfigSnapshot,
    team_id: str,
    agent_id: str,
) -> tuple[SavedRoutineStatus, ...]:
    team_cfg = snapshot.config.teams[team_id]
    instance = team_cfg.agents[agent_id]
    logs_root = resolve_team_paths(team_cfg).logs
    now = clock_now()
    grace = grace_window(int(snapshot.config.flowgency.dispatch.interval))
    dispatch_enabled = team_cfg.dispatch.enabled
    rows: list[SavedRoutineStatus] = []
    for index, schedule in enumerate(routine_schedules(instance.routines)):
        rows.append(
            SavedRoutineStatus(
                source_index=index,
                original_id=schedule.routine_id,
                last_fired=marker_stamp(
                    last_fired_at(
                        schedule,
                        logs_root=logs_root,
                        agent_name=instance.name,
                        now=now,
                    )
                ),
                next_due=next_due_text(
                    schedule,
                    logs_root,
                    instance.name,
                    now,
                    grace,
                    dispatch_enabled,
                ),
            )
        )
    return tuple(rows)


def summarize(
    prepared: PreparedRoutines,
    team_id: str,
    agent_id: str,
    draft: RoutinesDraft,
) -> tuple[RoutineSummary, ...]:
    candidate_agent = prepared.config.teams[team_id].agents[agent_id]
    candidate_raw = _find_agent(prepared.candidate, team_id, agent_id)
    raw_routines = candidate_raw.get("routines") if isinstance(candidate_raw.get("routines"), list) else []
    warning_map = {
        issue.field: issue.message
        for issue in prepared.form.warnings
    }
    rows: list[RoutineSummary] = []
    for index, routine in enumerate(candidate_agent.routines):
        raw_row = raw_routines[index] if index < len(raw_routines) and isinstance(raw_routines[index], dict) else {}
        draft_row = draft.routines[index]
        rows.append(
            RoutineSummary(
                key=draft_row.key,
                source_index=draft_row.source_index,
                id=routine.id,
                enabled=routine.enabled,
                prompt_scope=routine.prompt.scope,
                prompt_name=routine.prompt.name,
                schedule=_schedule_text(routine, raw_row, warning_map.get(f"routines.{index}.schedule")),
                memory=_memory_text(candidate_agent.default_memory, routine.memory, prepared.choices.channels),
                arguments=tuple(routine.arguments),
                recovery=_recovery_text(routine, raw_row, warning_map.get(f"routines.{index}.recovery")),
            )
        )
    return tuple(rows)


def marker_stamp(fired_at) -> str:
    if fired_at is None:
        return "never"
    return fired_at.strftime("%Y-%m-%d %H:%M")


def next_due_text(schedule, logs_root: Path, agent_name: str, now, grace, dispatch_enabled: bool = True) -> str:
    if not dispatch_enabled:
        return "dispatch disabled"
    if schedule.conditional or not schedule.enabled:
        return "—"
    lateness = schedule_lateness(
        (schedule,),
        logs_root=logs_root,
        agent_name=agent_name,
        now=now,
        grace=grace,
    )
    if lateness is None:
        nxt = next_occurrence(schedule, logs_root=logs_root, agent_name=agent_name, now=now)
        return relative_future(nxt)
    if lateness.state == "overdue":
        return f"overdue {elapsed_coarse(now - lateness.due_at)}"
    return "due now"


def _find_agent(raw: dict[str, Any], team_id: str, agent_id: str) -> dict[str, Any]:
    team = raw["teams"][team_id]
    for entry in team["agents"]:
        if isinstance(entry, dict) and entry.get("name") == agent_id:
            return entry
    raise KeyError(agent_id)


def _schedule_text(routine, raw_row: dict[str, Any], warning: str | None) -> str:
    if warning:
        raw_schedule = raw_row.get("schedule") if isinstance(raw_row.get("schedule"), dict) else {}
        if raw_schedule.get("at") is not None:
            return f"{raw_schedule['at']} ({warning})"
        if raw_schedule.get("every") is not None:
            return f"{raw_schedule['every']} ({warning})"
        return warning
    if routine.schedule.at:
        return f"at {routine.schedule.at}"
    if routine.schedule.every:
        return f"every {routine.schedule.every}"
    return "no schedule"


def _recovery_text(routine, raw_row: dict[str, Any], warning: str | None) -> str:
    raw_schedule = raw_row.get("schedule") if isinstance(raw_row.get("schedule"), dict) else {}
    if warning:
        value = raw_schedule.get("catch_up")
        return f"{value} ({warning})" if value is not None else warning
    catch_up = routine.schedule.catch_up
    if catch_up is None:
        return "today"
    return str(catch_up)


def _memory_text(
    agent_default: MemorySelector | None,
    routine_memory: MemorySelector | None,
    channels: tuple[tuple[str, str], ...],
) -> str:
    effective = select_effective_memory(None, routine_memory, agent_default)
    label = _memory_label(effective, channels)
    if routine_memory is None and agent_default is not None:
        return f"{label} (Agent default)"
    return label


def _memory_label(
    selector: MemorySelector,
    channels: tuple[tuple[str, str], ...],
) -> str:
    if selector.scope == "run":
        return "Run memory"
    if selector.scope == "routine":
        return "Routine memory"
    if selector.scope == "agent":
        return "Agent memory"
    if selector.scope == "team":
        return "Team memory"
    channel_map = dict(channels)
    display = channel_map.get(selector.channel or "", selector.channel or "Channel")
    return f"Channel: {display}"


__all__ = [
    "RoutineSummary",
    "SavedRoutineStatus",
    "marker_stamp",
    "next_due_text",
    "routine_status",
    "saved_status",
    "summarize",
]