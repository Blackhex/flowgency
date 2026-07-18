import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agency.dispatch.run import check_at_rule, check_every_rule, run_dispatch_cycle
from agency.jobs import JobSubmissionError


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
    """Create a strict-canonical group and config path; return (group_path, config_path, log_dir)."""
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENTS.md").write_text("# Product\n", encoding="utf-8")
    log_dir = group_path / "shared" / "logs" / "2026-07-03"
    log_dir.mkdir(parents=True)
    return group_path, tmp_path / "config.yaml", log_dir


def _write_canonical_config(
    config_path: Path,
    group_path: Path,
    *,
    routines: list[dict],
    enabled: bool = True,
    daily_limit: int = 20,
) -> None:
    routine_yaml = "".join(
        (
            "          - id: {id}\n"
            "            skill: {skill}\n"
        ).format(**routine)
        + (
            "            arguments:\n"
            + "".join(f"              - {argument}\n" for argument in routine.get("arguments", []))
            if routine.get("arguments")
            else ""
        )
        + (
            "            schedule:\n"
            f"              at: '{routine['schedule']['at']}'\n"
            if "at" in routine["schedule"]
            else "            schedule:\n"
            f"              every: {routine['schedule']['every']}\n"
        )
        + (
            f"            memory:\n              scope: {routine['memory']['scope']}\n"
            if routine.get("memory")
            else ""
        )
        + (
            f"            condition: {routine['condition']}\n"
            if routine.get("condition")
            else ""
        )
        + (
            f"            enabled: {str(routine['enabled']).lower()}\n"
            if "enabled" in routine
            else ""
        )
        for routine in routines
    )
    config_path.write_text(
        "agency:\n"
        "  title: Agency\n"
        "  default_group: test\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "  dispatch:\n"
        "    interval: 15\n"
        "groups:\n"
        "  test:\n"
        "    name: Test\n"
        f"    path: {group_path.as_posix()}\n"
        "    default_integration: copilot\n"
        "    dispatch:\n"
        f"      enabled: {str(enabled).lower()}\n"
        f"      daily_limit: {daily_limit}\n"
        "    agents:\n"
        "      - name: product\n"
        "        blueprint: builder-blueprint\n"
        "        integration: copilot\n"
        "        default_memory:\n"
        "          scope: agent\n"
        "        routines:\n"
        f"{routine_yaml}",
        encoding="utf-8",
    )


def _request_summary(request):
    return {
        "group_key": request.group_key,
        "agent_name": request.agent_name,
        "trigger": request.trigger,
        "routine_id": request.routine_id,
        "task_input": request.task_input,
        "memory_override": request.memory_override,
        "timeout_override": request.timeout_override,
    }


def test_due_schedule_submits_routine_request_then_touches_marker(tmp_path, monkeypatch):
    group_path, config_path, _ = _make_group(tmp_path)
    _write_canonical_config(
        config_path,
        group_path,
        routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"every": "1h"}}],
    )
    captured = []

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: captured.append(request) or SimpleNamespace(job_id=request.job_id),
    )

    run_dispatch_cycle({}, config_path)

    assert _request_summary(captured[0]) == {
        "group_key": "test",
        "agent_name": "product",
        "trigger": "scheduled_prompt",
        "routine_id": "daily-review",
        "task_input": "Run routine 'daily-review'.",
        "memory_override": None,
        "timeout_override": None,
    }
    assert (group_path / "shared" / "logs" / ".last-product-daily-review").exists()


def test_due_schedule_renders_routine_arguments_in_task_input(tmp_path, monkeypatch):
    group_path, config_path, _ = _make_group(tmp_path)
    _write_canonical_config(
        config_path,
        group_path,
        routines=[
            {
                "id": "daily-review",
                "skill": "daily-review",
                "arguments": ["--mode=review", "literal value"],
                "schedule": {"every": "1h"},
            }
        ],
    )
    captured = []

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: captured.append(request) or SimpleNamespace(job_id=request.job_id),
    )

    run_dispatch_cycle({}, config_path)

    assert captured[0].task_input == "Run routine 'daily-review' with arguments: --mode=review, literal value."


def test_schedule_does_not_touch_marker_when_submission_fails(tmp_path, monkeypatch):
    group_path, config_path, _ = _make_group(tmp_path)
    _write_canonical_config(
        config_path,
        group_path,
        routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"every": "1h"}}],
    )

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            JobSubmissionError("no", tmp_path / "job")
        ),
    )

    run_dispatch_cycle({}, config_path)

    assert not (group_path / "shared" / "logs" / ".last-product-daily-review").exists()


def test_schedule_skips_condition_rules(tmp_path, monkeypatch):
    group_path, config_path, _ = _make_group(tmp_path)
    _write_canonical_config(
        config_path,
        group_path,
        routines=[
            {
                "id": "daily-review",
                "skill": "daily-review",
                "schedule": {"every": "1h"},
                "condition": "pre-send",
            }
        ],
    )
    submit_calls = []

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submit_calls.append(request) or object(),
    )

    run_dispatch_cycle({}, config_path)

    assert submit_calls == []
    assert not (group_path / "shared" / "logs" / ".last-product-daily-review").exists()


def test_check_every_rule_days(tmp_path):
    marker = tmp_path / ".last-test"
    marker.write_text("")
    old_time = time.time() - (2 * 24 * 3600)
    os.utime(marker, (old_time, old_time))
    assert check_every_rule(marker, "1d") is True


def test_one_heartbeat_submits_due_work_for_multiple_enabled_groups(tmp_path, monkeypatch):
    first_path, first_config, _ = _make_group(tmp_path / "first")
    second_path, second_config, _ = _make_group(tmp_path / "second")
    _write_canonical_config(first_config, first_path, routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"every": "1h"}}])
    _write_canonical_config(second_config, second_path, routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"every": "1h"}}])
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submitted.append((request.group_key, request.agent_name)),
    )
    run_dispatch_cycle({}, first_config)
    run_dispatch_cycle({}, second_config)
    assert submitted == [("test", "product"), ("test", "product")]


def test_repeated_heartbeat_does_not_duplicate_daily_at_rule(tmp_path, monkeypatch):
    """Prove at rules use consistent date when checking markers, preventing duplication.

    Uses fixed datetime to prevent rare midnight-crossing flakes.
    """
    group_path, config_path, log_dir = _make_group(tmp_path)

    # Fixed time: 2026-07-03 09:15:00 (within window of 09:00 at rule)
    fixed_dt = datetime(2026, 7, 3, 9, 15, 0)

    # Monkeypatch datetime.now() in dispatch.run module
    class MockDatetime:
        @staticmethod
        def now():
            return fixed_dt

        @staticmethod
        def fromtimestamp(ts):
            return datetime.fromtimestamp(ts)

        @staticmethod
        def strptime(date_string, format):
            return datetime.strptime(date_string, format)

    monkeypatch.setattr("agency.dispatch.run.datetime", MockDatetime)

    _write_canonical_config(
        config_path,
        group_path,
        routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"at": "09:00"}}],
    )
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submitted.append(request),
    )
    run_dispatch_cycle({}, config_path)
    run_dispatch_cycle({}, config_path)
    assert len(submitted) == 1

    # Verify the event marker was created in the correct date subdirectory
    event_marker = log_dir / ".event-product-daily-review-2026-07-03"
    assert event_marker.exists()


def test_disabled_group_is_skipped_in_multi_group_config(tmp_path, monkeypatch):
    enabled_path, enabled_config, _ = _make_group(tmp_path / "enabled")
    disabled_path, disabled_config, _ = _make_group(tmp_path / "disabled")
    _write_canonical_config(enabled_config, enabled_path, routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"every": "1h"}}])
    _write_canonical_config(disabled_config, disabled_path, routines=[{"id": "daily-review", "skill": "daily-review", "schedule": {"every": "1h"}}], enabled=False)
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submitted.append(request.group_key),
    )
    run_dispatch_cycle({}, enabled_config)
    run_dispatch_cycle({}, disabled_config)
    assert submitted == ["test"]


def test_disabled_routine_is_never_submitted_or_marked(tmp_path, monkeypatch):
    group_path, config_path, _ = _make_group(tmp_path)
    _write_canonical_config(
        config_path,
        group_path,
        routines=[
            {
                "id": "daily-review",
                "skill": "daily-review",
                "schedule": {"every": "1h"},
                "enabled": False,
            }
        ],
    )
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submitted.append(request),
    )

    run_dispatch_cycle({}, config_path)

    assert submitted == []
    assert not (group_path / "shared" / "logs" / ".last-product-daily-review").exists()
