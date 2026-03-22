import pytest
import os
import time
from pathlib import Path
from datetime import datetime
from agency.dispatch.run import check_at_rule, check_every_rule


def _epoch(time_str: str) -> float:
    """Helper: convert HH:MM to today's epoch."""
    today = datetime.now().strftime("%Y-%m-%d")
    return datetime.strptime(f"{today} {time_str}", "%Y-%m-%d %H:%M").timestamp()


def test_check_at_rule_within_window():
    assert check_at_rule("09:00", now_epoch=_epoch("09:01"), interval=15) is True


def test_check_at_rule_outside_window():
    assert check_at_rule("09:00", now_epoch=_epoch("09:20"), interval=15) is False


def test_check_at_rule_before_target():
    assert check_at_rule("09:00", now_epoch=_epoch("08:59"), interval=15) is False


def test_check_every_rule_no_marker(tmp_path):
    marker = tmp_path / ".last-test"
    assert check_every_rule(marker, "6h") is True


def test_check_every_rule_elapsed(tmp_path):
    marker = tmp_path / ".last-test"
    marker.write_text("")
    old_time = time.time() - (7 * 3600)
    os.utime(marker, (old_time, old_time))
    assert check_every_rule(marker, "6h") is True


def test_check_every_rule_not_elapsed(tmp_path):
    marker = tmp_path / ".last-test"
    marker.write_text("")
    assert check_every_rule(marker, "6h") is False


def test_check_every_rule_minutes(tmp_path):
    marker = tmp_path / ".last-test"
    marker.write_text("")
    old_time = time.time() - (35 * 60)
    os.utime(marker, (old_time, old_time))
    assert check_every_rule(marker, "30m") is True
