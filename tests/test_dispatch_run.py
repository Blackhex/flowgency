import pytest
import os
import time
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock
from agency.dispatch.run import check_at_rule, check_every_rule, _run_agent


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


def _make_group(tmp_path):
    """Create a group with one agent dir and one prompt; return (group_path, log_dir)."""
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("do the thing")
    log_dir = group_path / "shared" / "logs" / "2026-07-03"
    log_dir.mkdir(parents=True)
    return group_path, agent_dir, log_dir


def test_run_agent_removes_running_marker_on_success(tmp_path):
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    running_marker = log_dir.parent / ".running-product"

    fake_result = MagicMock(stdout="ok", stderr="", exit_code=0, duration_seconds=1.0)
    fake_integration = MagicMock(supports_execution=True)
    fake_integration.run.return_value = fake_result

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        _run_agent(group_path, "product", "routine.md", 1800, log_dir,
                   {"integration": "claude-code"}, agent_dir=agent_dir)

    assert not running_marker.exists()


def test_run_agent_marker_present_during_run(tmp_path):
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    running_marker = log_dir.parent / ".running-product"
    seen = {}

    fake_integration = MagicMock(supports_execution=True)

    def _run(agent_dir_arg, prompt_path, timeout, *, sandbox_root=None):
        seen["exists"] = running_marker.exists()
        return MagicMock(stdout="ok", stderr="", exit_code=0, duration_seconds=1.0)

    fake_integration.run.side_effect = _run

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        _run_agent(group_path, "product", "routine.md", 1800, log_dir,
                   {"integration": "claude-code"}, agent_dir=agent_dir)

    assert seen["exists"] is True


def test_run_agent_removes_marker_on_exception(tmp_path):
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    running_marker = log_dir.parent / ".running-product"

    fake_integration = MagicMock(supports_execution=True)
    fake_integration.run.side_effect = RuntimeError("boom")

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        with pytest.raises(RuntimeError):
            _run_agent(group_path, "product", "routine.md", 1800, log_dir,
                       {"integration": "claude-code"}, agent_dir=agent_dir)

    assert not running_marker.exists()


def test_run_agent_forwards_sandbox_root(tmp_path):
    """Verify sandbox_root parameter is forwarded to integration.run."""
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    sandbox_path = Path("/repo/root")
    captured = {}

    fake_integration = MagicMock(supports_execution=True)

    def _run(agent_dir_arg, prompt_path, timeout, *, sandbox_root=None):
        captured["sandbox_root"] = sandbox_root
        return MagicMock(stdout="ok", stderr="", exit_code=0, duration_seconds=1.0)

    fake_integration.run.side_effect = _run

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        _run_agent(
            group_path, "product", "routine.md", 1800, log_dir,
            {"integration": "claude-code"}, agent_dir=agent_dir,
            sandbox_root=sandbox_path,
        )

    assert captured["sandbox_root"] == sandbox_path
