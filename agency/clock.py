from __future__ import annotations

import os
from datetime import date, datetime, tzinfo


def _fixed_datetime() -> datetime | None:
    raw = os.environ.get("AGENCY_FIXED_NOW")
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            "AGENCY_FIXED_NOW must be a valid ISO-8601 datetime"
        ) from exc


def now(tz: tzinfo | None = None) -> datetime:
    fixed = _fixed_datetime()
    if fixed is None:
        return datetime.now(tz)
    if tz is None:
        return fixed.replace(tzinfo=None)
    if fixed.tzinfo is None:
        return fixed.replace(tzinfo=tz)
    return fixed.astimezone(tz)


def today() -> date:
    return now().date()