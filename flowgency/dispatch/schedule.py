"""Schedule primitives shared by the dispatch runner and the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple
import re

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_EVERY = re.compile(r"(\d+)(m|h|d)")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}

DEFAULT_CATCH_UP = "today"
_KEYWORDS = ("none", "today", "always")


class CatchUp(NamedTuple):
    kind: str
    period: timedelta | None


def marker_safe(value: str) -> str:
    return _UNSAFE.sub("-", value).strip(".-") or "item"


def parse_every(value: str | None) -> timedelta | None:
    """Return the period of an ``every`` schedule, or None when malformed."""
    match = _EVERY.fullmatch(value or "")
    if match is None:
        return None
    return timedelta(seconds=int(match.group(1)) * _UNIT_SECONDS[match.group(2)])


def every_marker_path(logs_root: Path, agent_name: str, routine_id: str) -> Path:
    name = f".last-{marker_safe(agent_name)}-{marker_safe(routine_id)}"
    return Path(logs_root) / name


def at_marker_path(
    logs_root: Path,
    agent_name: str,
    routine_id: str,
    day: str,
) -> Path:
    name = f".event-{marker_safe(agent_name)}-{marker_safe(routine_id)}-{day}"
    return Path(logs_root) / day / name


def parse_catch_up(value: str | None) -> CatchUp | None:
    """Return the recovery bound, or None when the value is malformed."""
    text = (value or "").strip() or DEFAULT_CATCH_UP
    if text in _KEYWORDS:
        return CatchUp(text, None)
    period = parse_every(text)
    if period is None:
        return None
    return CatchUp("duration", period)


def catch_up_allows(
    occurrence: datetime,
    now: datetime,
    bound: CatchUp,
    grace: timedelta,
) -> bool:
    """Whether an occurrence is still worth running at ``now``."""
    age = now - occurrence
    if bound.kind == "always":
        return True
    if bound.kind == "today":
        return occurrence.date() == now.date()
    if bound.kind == "duration":
        return age <= bound.period
    return age < grace


def last_at_occurrence(at: str, now: datetime) -> datetime | None:
    """The newest ``at`` occurrence at or before ``now``, or None if malformed."""
    try:
        target = datetime.strptime(at.strip(), "%H:%M").time()
    except (AttributeError, ValueError):
        return None
    today = datetime.combine(now.date(), target)
    if now >= today:
        return today
    return today - timedelta(days=1)


def last_every_occurrence(
    anchor: datetime,
    every: str,
    now: datetime,
) -> datetime | None:
    """The newest ``every`` occurrence at or before ``now``, counted from the anchor."""
    period = parse_every(every)
    if period is None or period.total_seconds() <= 0:
        return None
    elapsed = (now - anchor).total_seconds()
    if elapsed < period.total_seconds():
        return None
    steps = int(elapsed // period.total_seconds())
    return anchor + steps * period

