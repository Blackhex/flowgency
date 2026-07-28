from datetime import timedelta
from pathlib import Path

import pytest

from agency.dispatch.schedule import (
    at_marker_path,
    every_marker_path,
    marker_safe,
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
