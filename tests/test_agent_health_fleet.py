from datetime import datetime, timedelta
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agency import app as app_module
from agency.jobs.authority import JobStore

NOW = datetime(2026, 7, 28, 12, 0, 0)


def _group(tmp_path, *, routines, dispatch_enabled=True):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    memory_root = tmp_path / "memory"
    return {
        "key": "grp",
        "name": "Grp",
        "logs": logs,
        "observations": tmp_path / "observations",
        "proposals": tmp_path / "proposals",
        "decisions": tmp_path / "decisions",
        "agents": ["product"],
        "agents_full": [
            {
                "name": "product",
                "blueprint": "product-blueprint",
                "integration": "script",
                "routines": routines,
            }
        ],
        "dispatch": {"enabled": dispatch_enabled},
        "dispatch_interval": 15,
        "runtime": {"timeout": 1800},
        "job_paths": tuple(JobStore(memory_root).paths("grp")),
    }


def _health(tmp_path, *, routines, dispatch_enabled=True, now=NOW):
    group = _group(tmp_path, routines=routines, dispatch_enabled=dispatch_enabled)
    group["observations"].mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": now.isoformat()}):
        agents, _ = app_module.collect_agents_with_identity(group)
    return agents[0]["health"]


def _routine(routine_id="r", at=None, every=None):
    schedule = {"at": at} if at else {"every": every}
    return {
        "id": routine_id,
        "prompt": {"scope": "blueprint", "name": "daily-review"},
        "schedule": schedule,
    }


def test_agent_that_never_ran_without_routines_is_gray(tmp_path):
    assert _health(tmp_path, routines=[]) == "gray"


def test_agent_that_never_ran_before_its_scheduled_time_is_gray(tmp_path):
    assert _health(tmp_path, routines=[_routine(at="18:00")]) == "gray"


def test_agent_with_a_missed_occurrence_is_red(tmp_path):
    assert _health(tmp_path, routines=[_routine(at="08:00")]) == "red"


def test_a_missed_occurrence_is_ignored_when_dispatch_is_disabled(tmp_path):
    assert _health(tmp_path, routines=[_routine(at="08:00")], dispatch_enabled=False) == "gray"


def test_a_fired_occurrence_leaves_the_agent_gray(tmp_path):
    logs = tmp_path / "logs"
    day = NOW.strftime("%Y-%m-%d")
    (logs / day).mkdir(parents=True, exist_ok=True)
    (logs / day / f".event-product-r-{day}").touch()
    assert _health(tmp_path, routines=[_routine(at="08:00")]) == "gray"


def test_an_agent_with_a_recent_log_is_green(tmp_path):
    logs = tmp_path / "logs"
    day = NOW.strftime("%Y-%m-%d")
    (logs / day).mkdir(parents=True, exist_ok=True)
    log_file = logs / day / "product-manual_prompt-job-1.out"
    log_file.write_text("", encoding="utf-8")
    assert _health(tmp_path, routines=[]) == "green"


def test_an_agent_whose_last_run_is_ancient_is_still_green_without_a_schedule(tmp_path):
    logs = tmp_path / "logs"
    day = "2026-01-01"
    (logs / day).mkdir(parents=True, exist_ok=True)
    log_file = logs / day / "product-manual_prompt-job-1.out"
    log_file.write_text("", encoding="utf-8")
    stamp = (NOW - timedelta(days=120)).timestamp()
    os.utime(log_file, (stamp, stamp))
    assert _health(tmp_path, routines=[]) == "green"
