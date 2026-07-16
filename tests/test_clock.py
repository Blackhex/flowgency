from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib

import pytest


def _clock():
    return importlib.import_module("agency.clock")


def test_real_clock_falls_back_to_datetime_now(monkeypatch):
    clock = _clock()
    expected = datetime(2026, 7, 16, 12, 0, 0)

    class StubDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return expected if tz is None else expected.replace(tzinfo=tz)

    monkeypatch.delenv("AGENCY_FIXED_NOW", raising=False)
    monkeypatch.setattr(clock, "datetime", StubDateTime)

    assert clock.now() == expected
    assert clock.now(timezone.utc) == expected.replace(tzinfo=timezone.utc)
    assert clock.today() == expected.date()


def test_fixed_aware_clock_converts_to_requested_timezone(monkeypatch):
    clock = _clock()
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-16T12:00:00+00:00")
    east = timezone(timedelta(hours=2))

    assert clock.now() == datetime(2026, 7, 16, 12, 0, 0)
    assert clock.now(east) == datetime(2026, 7, 16, 14, 0, 0, tzinfo=east)


def test_fixed_naive_clock_preserves_wall_time_and_attaches_requested_timezone(monkeypatch):
    clock = _clock()
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-16T12:00:00")

    assert clock.now() == datetime(2026, 7, 16, 12, 0, 0)
    assert clock.now(timezone.utc) == datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_invalid_fixed_clock_fails_clearly(monkeypatch):
    clock = _clock()
    monkeypatch.setenv("AGENCY_FIXED_NOW", "not-a-date")

    with pytest.raises(ValueError, match="AGENCY_FIXED_NOW must be a valid ISO-8601 datetime"):
        clock.now()


def test_dashboard_time_helpers_use_fixed_clock(monkeypatch):
    clock = _clock()
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-16T12:00:00+00:00")
    from agency.app import build_pipeline_stats, relative_time

    assert relative_time(datetime(2026, 7, 16, 10, 0, 0)) == "2h ago"
    stats = build_pipeline_stats(
        [{"date": "2026-07-10"}, {"date": "2026-07-16"}],
        [],
        [],
    )
    assert stats["observations"]["sparkline"] == [1, 0, 0, 0, 0, 0, 1]