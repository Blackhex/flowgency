"""Schedule primitives shared by the dispatch runner and the dashboard."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import re

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_EVERY = re.compile(r"(\d+)(m|h|d)")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


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
