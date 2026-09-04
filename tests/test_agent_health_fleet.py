from dataclasses import replace
from datetime import datetime, timedelta
import os
from unittest.mock import patch

from agency import app as app_module
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import write_job

NOW = datetime(2026, 7, 28, 12, 0, 0)


def _team(tmp_path, *, routines, dispatch_enabled=True):
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
    team_data = _team(tmp_path, routines=routines, dispatch_enabled=dispatch_enabled)
    team_data["observations"].mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": now.isoformat()}):
        agents, _ = app_module.collect_agents_with_identity(team_data)
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


# ── Helpers for job-record tests ──────────────────────────────────────────────


def _spec(tmp_path, job_id, created_at="2026-07-20T00:00:00+00:00"):
    config_path = tmp_path / "config.yaml"
    if not config_path.exists():
        config_path.write_text("schema_version: 6\nteams: {}\n", encoding="utf-8")
    return JobSpec(
        schema_version=5,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key="grp",
        team_root=str(tmp_path.resolve()),
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
            cache_path=str((tmp_path / "cache" / "entry.py").resolve()),
        ),
        routine_id="daily-review",
        skill=None,
        skill_arguments=(),
        task_input="# Task\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "team": "grp", "agent": "product"},
            canonical_json='{"scope":"agent"}',
            memory_hash="mem-1",
            path=str((tmp_path / "memory" / "mem-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "prompt", "path": "routine.md"},
        timeout_override=None,
        created_at=created_at,
    )


def _write_job(tmp_path, job_id, *, status, completed_at=None, created_at="2026-07-20T00:00:00+00:00"):
    spec = _spec(tmp_path, job_id, created_at=created_at)
    record = replace(JobRecord.from_spec(spec), status=status, completed_at=completed_at)
    store = JobStore(tmp_path / "memory")
    store.team_root("grp").mkdir(parents=True, exist_ok=True)
    write_job(store.path("grp", job_id), record)


# ── Job-record health tests ───────────────────────────────────────────────────


def test_newest_failed_record_makes_agent_red(tmp_path):
    _write_job(tmp_path, "job-1", status="failed", completed_at="2026-07-28T10:00:00+00:00")
    assert _health(tmp_path, routines=[]) == "red"


def test_complete_record_newer_than_failed_is_not_red(tmp_path):
    _write_job(tmp_path, "job-old", status="failed", completed_at="2026-07-27T10:00:00+00:00")
    _write_job(tmp_path, "job-new", status="complete", completed_at="2026-07-28T10:00:00+00:00")
    assert _health(tmp_path, routines=[]) != "red"


def test_cancelled_newer_than_failed_keeps_red(tmp_path):
    _write_job(tmp_path, "job-fail", status="failed", completed_at="2026-07-27T10:00:00+00:00")
    _write_job(tmp_path, "job-cancel", status="cancelled", completed_at="2026-07-28T10:00:00+00:00")
    assert _health(tmp_path, routines=[]) == "red"


def test_complete_record_with_no_log_file_is_green(tmp_path):
    _write_job(tmp_path, "job-1", status="complete", completed_at="2026-07-28T10:00:00+00:00")
    assert _health(tmp_path, routines=[]) == "green"


def test_complete_followed_by_cancelled_is_green(tmp_path):
    _write_job(tmp_path, "job-old", status="complete", completed_at="2026-07-27T10:00:00+00:00")
    _write_job(tmp_path, "job-new", status="cancelled", completed_at="2026-07-28T10:00:00+00:00")
    assert _health(tmp_path, routines=[]) == "green"
