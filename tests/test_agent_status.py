from dataclasses import replace
from datetime import datetime, timedelta
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from agency import app as app_module
from agency.app import (
    compute_next_run,
    compute_next_run_detail,
    get_agent_last_run,
    is_agent_running,
    relative_future,
)
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import job_path, write_job


def _group(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    return {"key": "grp", "logs": logs}


def _write_job(tmp_path, status):
    memory_root = tmp_path.parent / "memory" if tmp_path.name == "grp" else tmp_path / "memory"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")
    spec = JobSpec(
        schema_version=3,
        job_id=f"job-{status}",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="grp",
        group_root=str(tmp_path.resolve()),
        agent_name="product",
        workspace_root=str(tmp_path.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="product-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "group": "grp", "agent": "product"},
            canonical_json='{"agent":"product","group":"grp","scope":"agent","version":1}',
            memory_hash="memory-hash-1",
            path=str((memory_root / "memory-hash-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "prompt", "path": "routine.md"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    store = JobStore(memory_root)
    group_store = store.group_root("grp")
    group_store.mkdir(parents=True, exist_ok=True)
    write_job(store.path("grp", spec.job_id), replace(JobRecord.from_spec(spec), status=status))


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_job_reports_agent_running(tmp_path, status):
    g = _group(tmp_path)
    _write_job(tmp_path, status)
    g["job_paths"] = tuple(JobStore(tmp_path / "memory").paths("grp"))
    assert is_agent_running(g, "product", timeout=1800) is True


@pytest.mark.parametrize("status", ["complete", "failed"])
def test_terminal_job_does_not_report_agent_running(tmp_path, status):
    g = _group(tmp_path)
    _write_job(tmp_path, status)
    g["job_paths"] = tuple(JobStore(tmp_path / "memory").paths("grp"))
    assert is_agent_running(g, "product", timeout=1800) is False


def test_no_active_job_reports_agent_not_running(tmp_path):
    g = _group(tmp_path)
    assert is_agent_running(g, "product", timeout=1800) is False


def _group_with_logs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    return {"key": "grp", "logs": logs}


def test_agent_last_run_uses_newest_stdout_mtime(tmp_path):
    g = _group_with_logs(tmp_path)
    day = g["logs"] / "2026-07-11"
    day.mkdir()
    older = day / "product-z-manual_prompt.out"
    newer = day / "product-a-manual_prompt.out"
    newest_stderr = day / "product-newest.err"
    older.write_text("older")
    newer.write_text("")
    newest_stderr.write_text("newer stderr")

    now = time.time()
    os.utime(older, (now - 120, now - 120))
    os.utime(newer, (now - 60, now - 60))
    os.utime(newest_stderr, (now, now))

    result = get_agent_last_run(g, "product")

    assert result == {
        "at": datetime.fromtimestamp(newer.stat().st_mtime),
        "path": str(newer.resolve()),
    }


def test_agent_last_run_ignores_stderr_and_other_agents(tmp_path):
    g = _group_with_logs(tmp_path)
    day = g["logs"] / "2026-07-11"
    day.mkdir()
    (day / "product-failed.err").write_text("failed")
    (day / "editor-manual_prompt.out").write_text("other agent")

    assert get_agent_last_run(g, "product") is None


def test_agent_last_run_stats_each_candidate_once(tmp_path, monkeypatch):
    g = _group_with_logs(tmp_path)
    day = g["logs"] / "2026-07-11"
    day.mkdir()
    candidates = {
        day / "product-older.out",
        day / "product-newer.out",
    }
    for candidate in candidates:
        candidate.write_text(candidate.name)

    stat_calls = {candidate: 0 for candidate in candidates}
    original_stat = Path.stat

    def counting_stat(path, *args, **kwargs):
        if path in stat_calls:
            stat_calls[path] += 1
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting_stat)

    get_agent_last_run(g, "product")

    assert stat_calls == {candidate: 1 for candidate in candidates}


def test_next_run_disabled(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": False, "routines": {"product": [{"id": "r", "every": "6h"}]}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_no_rules(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "routines": {}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_at_future(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    future = (fixed_now + timedelta(hours=2)).strftime("%H:%M")
    cfg = {"enabled": True, "routines": {"product": [{"id": "r", "at": future}]}}
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result.date() == fixed_now.date()
    assert result.strftime("%H:%M") == future


def test_next_run_at_past_rolls_to_tomorrow(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    past = (fixed_now - timedelta(hours=2)).strftime("%H:%M")
    cfg = {"enabled": True, "routines": {"product": [{"id": "r", "at": past}]}}
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result.date() == (fixed_now + timedelta(days=1)).date()


def test_next_run_every_no_marker_due_now(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "routines": {"product": [{"id": "r", "every": "6h"}]}}
    before = datetime.now()
    result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result <= datetime.now() and result >= before - timedelta(seconds=5)


def test_next_run_every_with_marker(tmp_path):
    g = _group_with_logs(tmp_path)
    marker = g["logs"] / ".last-product-r"
    marker.touch()
    two_hours_ago = time.time() - 2 * 3600
    os.utime(marker, (two_hours_ago, two_hours_ago))
    cfg = {"enabled": True, "routines": {"product": [{"id": "r", "every": "6h"}]}}
    result = compute_next_run(g, "product", cfg)
    # marker + 6h => ~4h from now
    assert result is not None
    delta = (result - datetime.now()).total_seconds()
    assert 3.9 * 3600 < delta < 4.1 * 3600


def test_next_run_skips_condition_rule(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "routines": {"product": [
        {"id": "gate", "at": "06:00", "condition": "pre-send"},
    ]}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_returns_soonest(tmp_path):
    g = _group_with_logs(tmp_path)
    soon = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")
    later = (datetime.now() + timedelta(hours=5)).strftime("%H:%M")
    cfg = {"enabled": True, "routines": {"product": [
        {"id": "a", "at": later},
        {"id": "b", "at": soon},
    ]}}
    result = compute_next_run(g, "product", cfg)
    assert result.strftime("%H:%M") == soon


def test_next_run_detail_identifies_winning_rule(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    cfg = {"enabled": True, "routines": {"product": [
        {"id": "later", "at": "17:00"},
        {"id": "soon", "at": "12:30"},
    ]}}

    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        detail = compute_next_run_detail(g, "product", cfg)
        compatible_value = compute_next_run(g, "product", cfg)

    assert detail == {
        "when": fixed_now + timedelta(minutes=30),
        "routine_id": "soon",
        "rule_index": 1,
    }
    assert compatible_value == detail["when"]


def test_next_run_detail_breaks_ties_by_config_order(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    cfg = {"enabled": True, "routines": {"product": [
        {"id": "first", "at": "13:00"},
        {"id": "second", "at": "13:00"},
    ]}}

    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        detail = compute_next_run_detail(g, "product", cfg)

    assert detail["routine_id"] == "first"
    assert detail["rule_index"] == 0


def test_relative_future_none():
    assert relative_future(None) == ""


def test_relative_future_due_now():
    assert relative_future(datetime.now() - timedelta(minutes=1)) == "due now"


def test_relative_future_minutes():
    assert relative_future(datetime.now() + timedelta(minutes=5)) == "5m away"


def test_relative_future_hours():
    fixed_now = datetime(2026, 7, 11, 23, 30)

    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        assert relative_future(fixed_now + timedelta(hours=2, minutes=1)) == "2h away"


def test_relative_future_tomorrow():
    fixed_now = datetime(2026, 7, 11, 12, 0)
    dt = fixed_now + timedelta(days=1)

    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        assert relative_future(dt) == f"tomorrow {dt.strftime('%H:%M')}"


def test_relative_future_under_a_minute():
    assert relative_future(datetime.now() + timedelta(seconds=30)) == "1m away"


def test_collect_agents_includes_running_and_next_run(tmp_path):
    group_path = tmp_path / "grp"
    memory_root = tmp_path / "memory"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    for sub in ("observations", "proposals", "decisions", "locks", "logs"):
        (group_path / sub).mkdir(parents=True)

    stdout_dir = group_path / "logs" / "2026-07-11"
    stdout_dir.mkdir()
    stdout_path = stdout_dir / "product-manual_prompt-job-1.out"
    stdout_path.write_text("")

    g = {
        "key": "grp", "name": "Grp",
        "agents": ["product"],
        "agents_full": [{
            "name": "product",
            "integration": "claude-code",
            "routines": [{"id": "r", "skill": "r", "schedule": {"every": "6h"}}],
        }],
        "observations": group_path / "observations",
        "proposals": group_path / "proposals",
        "decisions": group_path / "decisions",
        "logs": group_path / "logs",
        "job_paths": tuple(JobStore(memory_root).paths("grp")),
        "dispatch": {"enabled": True, "routines": {"product": [{"id": "r", "every": "6h"}]}},
    }

    _write_job(group_path, "running")
    g["job_paths"] = tuple(JobStore(memory_root).paths("grp"))

    agents, _subagents = app_module.collect_agents_with_identity(g)

    product = next(agent for agent in agents if agent["name"] == "product")
    assert product["running"] is True
    assert product["last_run"]["path"] == str(stdout_path.resolve())
    assert product["last_seen"] == product["last_run"]["at"]
    assert product["next_run"] == product["next_run_detail"]["when"]
    assert product["next_run_detail"]["routine_id"] == "r"
    assert product["next_run_detail"]["rule_index"] == 0
