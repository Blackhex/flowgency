from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agency.dispatch.schedule import (
    DEFAULT_CATCH_UP,
    at_marker_path,
    catch_up_allows,
    every_marker_path,
    last_at_occurrence,
    last_every_occurrence,
    marker_safe,
    parse_catch_up,
    parse_every,
)


def test_marker_safe_replaces_unsafe_runs_with_a_single_hyphen():
    assert marker_safe("daily review/now") == "daily-review-now"


def test_marker_safe_keeps_dots_underscores_and_hyphens():
    assert marker_safe("a.b_c-d") == "a.b_c-d"


def test_marker_safe_strips_leading_and_trailing_dots_and_hyphens():
    assert marker_safe("--weekly..") == "weekly"


def test_marker_safe_falls_back_to_item_when_nothing_survives():
    assert marker_safe("///") == "item"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("6h", timedelta(hours=6)),
        ("7d", timedelta(days=7)),
        ("0m", timedelta(0)),
    ],
)
def test_parse_every_reads_each_unit(value, expected):
    assert parse_every(value) == expected


@pytest.mark.parametrize("value", [None, "", "soon", "6", "h", "6 h", "6w", "-1d"])
def test_parse_every_rejects_malformed_intervals(value):
    assert parse_every(value) is None


def test_every_marker_path_sits_at_the_logs_root():
    path = every_marker_path(Path("/logs"), "product", "authority-audit")
    assert path == Path("/logs/.last-product-authority-audit")


def test_at_marker_path_sits_in_the_day_directory_and_repeats_the_day():
    path = at_marker_path(Path("/logs"), "product", "suite-health", "2026-07-28")
    assert path == Path("/logs/2026-07-28/.event-product-suite-health-2026-07-28")


def test_marker_paths_sanitize_both_identifiers():
    every = every_marker_path(Path("/logs"), "Team Lead", "diff review")
    at = at_marker_path(Path("/logs"), "Team Lead", "diff review", "2026-07-28")
    assert every.name == ".last-Team-Lead-diff-review"
    assert at.name == ".event-Team-Lead-diff-review-2026-07-28"


def test_absent_catch_up_is_today():
    assert DEFAULT_CATCH_UP == "today"
    assert parse_catch_up(None).kind == "today"
    assert parse_catch_up("").kind == "today"


def test_catch_up_keywords_parse():
    assert parse_catch_up("none").kind == "none"
    assert parse_catch_up("today").kind == "today"
    assert parse_catch_up("always").kind == "always"


def test_catch_up_duration_parses_with_the_every_grammar():
    bound = parse_catch_up("36h")
    assert bound.kind == "duration"
    assert bound.period == timedelta(hours=36)


def test_malformed_catch_up_is_rejected():
    assert parse_catch_up("sometimes") is None
    assert parse_catch_up("24") is None
    assert parse_catch_up("-1h") is None


def test_none_allows_only_inside_the_grace_window():
    grace = timedelta(minutes=17)
    occurrence = datetime(2026, 7, 29, 8, 0)
    bound = parse_catch_up("none")
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 8, 10), bound, grace) is True
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 8, 30), bound, grace) is False


def test_today_allows_only_the_current_calendar_day():
    grace = timedelta(minutes=17)
    bound = parse_catch_up("today")
    occurrence = datetime(2026, 7, 29, 8, 0)
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 20, 0), bound, grace) is True
    assert catch_up_allows(occurrence, datetime(2026, 7, 30, 3, 0), bound, grace) is False


def test_always_allows_any_age():
    grace = timedelta(minutes=17)
    bound = parse_catch_up("always")
    occurrence = datetime(2026, 7, 20, 8, 0)
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 20, 0), bound, grace) is True


def test_duration_allows_up_to_and_including_the_bound():
    grace = timedelta(minutes=17)
    bound = parse_catch_up("24h")
    occurrence = datetime(2026, 7, 29, 8, 0)
    assert catch_up_allows(occurrence, datetime(2026, 7, 30, 8, 0), bound, grace) is True
    assert catch_up_allows(occurrence, datetime(2026, 7, 30, 8, 1), bound, grace) is False


def test_at_occurrence_is_today_once_the_time_has_passed():
    now = datetime(2026, 7, 29, 11, 57)
    assert last_at_occurrence("08:00", now) == datetime(2026, 7, 29, 8, 0)


def test_at_occurrence_is_yesterday_before_the_time():
    now = datetime(2026, 7, 29, 3, 0)
    assert last_at_occurrence("08:00", now) == datetime(2026, 7, 28, 8, 0)


def test_at_occurrence_at_exactly_the_target_is_today():
    now = datetime(2026, 7, 29, 8, 0)
    assert last_at_occurrence("08:00", now) == datetime(2026, 7, 29, 8, 0)


def test_malformed_at_has_no_occurrence():
    assert last_at_occurrence("25:00", datetime(2026, 7, 29, 8, 0)) is None


def test_every_occurrence_steps_from_the_anchor():
    anchor = datetime(2026, 7, 26, 9, 0)
    now = datetime(2026, 7, 29, 11, 57)
    assert last_every_occurrence(anchor, "6h", now) == datetime(2026, 7, 29, 9, 0)


def test_every_occurrence_is_none_before_the_first_period_elapses():
    anchor = datetime(2026, 7, 29, 9, 0)
    now = datetime(2026, 7, 29, 11, 0)
    assert last_every_occurrence(anchor, "6h", now) is None


def test_malformed_every_has_no_occurrence():
    anchor = datetime(2026, 7, 29, 9, 0)
    assert last_every_occurrence(anchor, "soon", datetime(2026, 7, 30, 9, 0)) is None

