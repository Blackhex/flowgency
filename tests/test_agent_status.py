from dataclasses import replace
from datetime import datetime, timedelta
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from agency import app as app_module
from agency.app import is_agent_running, compute_next_run, relative_future
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import job_path, write_job


def _group(tmp_path):
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    return {"key": "grp", "path": tmp_path, "shared": shared}


def _write_job(tmp_path, status):
    spec = JobSpec.create(
        config_path=tmp_path / "config.yaml",
        group_key="grp",
        agent_name="product",
        trigger="manual_prompt",
        prompt_source={"type": "prompt", "path": "routine.md"},
        prompt_content="# Routine\n",
    )
    write_job(
        job_path(tmp_path, spec.job_id),
        replace(JobRecord.from_spec(spec), status=status),
    )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_job_reports_agent_running(tmp_path, status):
    g = _group(tmp_path)
    _write_job(tmp_path, status)
    assert is_agent_running(g, "product", timeout=1800) is True


@pytest.mark.parametrize("status", ["complete", "failed"])
def test_terminal_job_does_not_report_agent_running(tmp_path, status):
    g = _group(tmp_path)
    _write_job(tmp_path, status)
    assert is_agent_running(g, "product", timeout=1800) is False


def test_no_active_job_reports_agent_not_running(tmp_path):
    g = _group(tmp_path)
    assert is_agent_running(g, "product", timeout=1800) is False


def _group_with_logs(tmp_path):
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    return {"key": "grp", "path": tmp_path, "shared": shared}


def test_next_run_disabled(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": False, "agents": {"product": [{"prompt": "r.md", "every": "6h"}]}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_no_rules(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "agents": {}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_at_future(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    future = (fixed_now + timedelta(hours=2)).strftime("%H:%M")
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "at": future}]}}
    with patch.object(app_module, "datetime", _Frozen):
        result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result.date() == fixed_now.date()
    assert result.strftime("%H:%M") == future


def test_next_run_at_past_rolls_to_tomorrow(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    past = (fixed_now - timedelta(hours=2)).strftime("%H:%M")
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "at": past}]}}
    with patch.object(app_module, "datetime", _Frozen):
        result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result.date() == (fixed_now + timedelta(days=1)).date()


def test_next_run_every_no_marker_due_now(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "every": "6h"}]}}
    before = datetime.now()
    result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result <= datetime.now() and result >= before - timedelta(seconds=5)


def test_next_run_every_with_marker(tmp_path):
    g = _group_with_logs(tmp_path)
    marker = g["shared"] / "logs" / ".last-product-r"
    marker.touch()
    two_hours_ago = time.time() - 2 * 3600
    os.utime(marker, (two_hours_ago, two_hours_ago))
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "every": "6h"}]}}
    result = compute_next_run(g, "product", cfg)
    # marker + 6h => ~4h from now
    assert result is not None
    delta = (result - datetime.now()).total_seconds()
    assert 3.9 * 3600 < delta < 4.1 * 3600


def test_next_run_skips_condition_rule(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "agents": {"product": [
        {"prompt": "gate.md", "at": "06:00", "condition": "pre-send"},
    ]}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_returns_soonest(tmp_path):
    g = _group_with_logs(tmp_path)
    soon = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")
    later = (datetime.now() + timedelta(hours=5)).strftime("%H:%M")
    cfg = {"enabled": True, "agents": {"product": [
        {"prompt": "a.md", "at": later},
        {"prompt": "b.md", "at": soon},
    ]}}
    result = compute_next_run(g, "product", cfg)
    assert result.strftime("%H:%M") == soon


def test_relative_future_none():
    assert relative_future(None) == ""


def test_relative_future_due_now():
    assert relative_future(datetime.now() - timedelta(minutes=1)) == "due now"


def test_relative_future_minutes():
    assert relative_future(datetime.now() + timedelta(minutes=5)) == "5m away"


def test_relative_future_hours():
    fixed_now = datetime(2026, 7, 11, 23, 30)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    with patch.object(app_module, "datetime", _Frozen):
        assert relative_future(fixed_now + timedelta(hours=2, minutes=1)) == "2h away"


def test_relative_future_tomorrow():
    fixed_now = datetime(2026, 7, 11, 12, 0)
    dt = fixed_now + timedelta(days=1)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    with patch.object(app_module, "datetime", _Frozen):
        assert relative_future(dt) == f"tomorrow {dt.strftime('%H:%M')}"


def test_relative_future_under_a_minute():
    assert relative_future(datetime.now() + timedelta(seconds=30)) == "1m away"


def test_collect_agents_includes_running_and_next_run(tmp_path):
    # Minimal group on disk
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    shared = group_path / "shared"
    for sub in ("observations", "proposals", "decisions", "prompts", "logs"):
        (shared / sub).mkdir(parents=True)

    g = {
        "key": "grp", "name": "Grp", "path": group_path,
        "agents": ["product"], "agents_full": [{"name": "product", "integration": "claude-code"}],
        "shared": shared,
    }

    _write_job(group_path, "running")

    groups_cfg = {"grp": {"dispatch": {"enabled": True, "agents": {
        "product": [{"prompt": "r.md", "every": "6h"}]}}}}

    with patch.object(app_module, "GROUPS", groups_cfg):
        agents, _subagents = app_module.collect_agents_with_identity(g)

    product = next(a for a in agents if a["name"] == "product")
    assert product["running"] is True
    assert "next_run" in product

