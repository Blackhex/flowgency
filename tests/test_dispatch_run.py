import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agency.dispatch.run import run_dispatch_cycle
from agency.dispatch.schedule import at_marker_path, every_marker_path
from agency.jobs import JobSubmissionError


def _make_group(tmp_path):
    """Create separate workspace and group roots for dispatch tests."""
    workspace_path = tmp_path / "workspaces" / "grp"
    group_root = tmp_path / "groups" / "grp"
    agent_dir = group_root / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENTS.md").write_text("# Product\n", encoding="utf-8")
    prompt_dir = tmp_path / "agent-library" / "builder-blueprint" / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "agent-library" / "builder-blueprint" / "AGENTS.md").write_text("# Product\n", encoding="utf-8")
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    workspace_path.mkdir(parents=True)
    log_dir = group_root / "logs" / "2026-07-03"
    log_dir.mkdir(parents=True)
    return workspace_path, group_root, tmp_path / "config.yaml", log_dir


def _write_config(
    config_path: Path,
    workspace_path: Path,
    group_root: Path,
    *,
    routines: list[dict],
    enabled: bool = True,
) -> None:
    routine_yaml = "".join(
        (
            "          - id: {id}\n"
            "            prompt:\n"
            "              scope: blueprint\n"
            "              name: {prompt_name}\n"
        ).format(**routine)
        + (
            "            arguments:\n"
            + "".join(f"              - {argument}\n" for argument in routine.get("arguments", []))
            if routine.get("arguments")
            else ""
        )
        + (
            "            schedule:\n"
            + (
                f"              at: '{routine['schedule']['at']}'\n"
                if "at" in routine["schedule"]
                else f"              every: {routine['schedule']['every']}\n"
            )
            + (
                f"              catch_up: {routine['schedule']['catch_up']}\n"
                if "catch_up" in routine["schedule"]
                else ""
            )
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
        "schema_version: 6\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_team: test\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "  prompt_store: prompts\n"
        "  dispatch:\n"
        "    interval: 15\n"
        "teams:\n"
        "  test:\n"
        "    name: Test\n"
        f"    workspace_path: {workspace_path.as_posix()}\n"
        f"    path: {group_root.as_posix()}\n"
        "    default_integration: copilot\n"
        "    dispatch:\n"
        f"      enabled: {str(enabled).lower()}\n"
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
        "team_key": request.team_key,
        "agent_name": request.agent_name,
        "trigger": request.trigger,
        "routine_id": request.routine_id,
        "task_input": request.task_input,
        "memory_override": request.memory_override,
        "timeout_override": request.timeout_override,
    }


def test_due_schedule_submits_routine_request_then_touches_marker(tmp_path, monkeypatch):
    workspace_path, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace_path,
        group_root,
        routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}],
    )
    captured = []

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: captured.append(request) or SimpleNamespace(job_id=request.job_id),
    )

    run_dispatch_cycle({}, config_path)

    assert _request_summary(captured[0]) == {
        "team_key": "test",
        "agent_name": "product",
        "trigger": "scheduled_prompt",
        "routine_id": "daily-review",
        "task_input": "",
        "memory_override": None,
        "timeout_override": None,
    }
    assert (group_root / "logs").is_dir()
    assert (group_root / "logs" / ".last-product-daily-review").exists()
    assert not (workspace_path / "shared").exists()


def test_due_schedule_renders_routine_arguments_in_task_input(tmp_path, monkeypatch):
    workspace_path, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace_path,
        group_root,
        routines=[
            {
                "id": "daily-review",
                "prompt_name": "daily-review",
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

    assert captured[0].task_input == ""


def test_schedule_does_not_touch_marker_when_submission_fails(tmp_path, monkeypatch):
    workspace_path, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace_path,
        group_root,
        routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}],
    )

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            JobSubmissionError("no", tmp_path / "job")
        ),
    )

    run_dispatch_cycle({}, config_path)

    assert not (group_root / "logs" / ".last-product-daily-review").exists()


def test_schedule_skips_condition_rules(tmp_path, monkeypatch):
    workspace_path, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace_path,
        group_root,
        routines=[
            {
                "id": "daily-review",
                "prompt_name": "daily-review",
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
    assert not (group_root / "logs" / ".last-product-daily-review").exists()


def test_one_heartbeat_submits_due_work_for_multiple_enabled_groups(tmp_path, monkeypatch):
    first_workspace, first_group, first_config, _ = _make_group(tmp_path / "first")
    second_workspace, second_group, second_config, _ = _make_group(tmp_path / "second")
    _write_config(first_config, first_workspace, first_group, routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}])
    _write_config(second_config, second_workspace, second_group, routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}])
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submitted.append((request.team_key, request.agent_name)),
    )
    run_dispatch_cycle({}, first_config)
    run_dispatch_cycle({}, second_config)
    assert submitted == [("test", "product"), ("test", "product")]


def test_repeated_heartbeat_does_not_duplicate_daily_at_rule(tmp_path, monkeypatch):
    """Prove at rules use consistent date when checking markers, preventing duplication.

    Uses fixed datetime to prevent rare midnight-crossing flakes.
    """
    workspace_path, group_root, config_path, log_dir = _make_group(tmp_path)

    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-03T09:15:00")

    _write_config(
        config_path,
        workspace_path,
        group_root,
        routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"at": "09:00"}}],
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
    enabled_workspace, enabled_group, enabled_config, _ = _make_group(tmp_path / "enabled")
    disabled_workspace, disabled_group, disabled_config, _ = _make_group(tmp_path / "disabled")
    _write_config(enabled_config, enabled_workspace, enabled_group, routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}])
    _write_config(disabled_config, disabled_workspace, disabled_group, routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}], enabled=False)
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda request, launcher=None: submitted.append(request.team_key),
    )
    run_dispatch_cycle({}, enabled_config)
    run_dispatch_cycle({}, disabled_config)
    assert submitted == ["test"]


def test_disabled_routine_is_never_submitted_or_marked(tmp_path, monkeypatch):
    workspace_path, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace_path,
        group_root,
        routines=[
            {
                "id": "daily-review",
                "prompt_name": "daily-review",
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
    assert not (group_root / "logs" / ".last-product-daily-review").exists()


class _RecordingLauncher:
    def __init__(self):
        self.launched = []

    def launch(self, authority):
        self.launched.append(authority.job_id)
        return SimpleNamespace(worker_pid=4321)


def _patch_submit(monkeypatch, launcher):
    """Patch submit_job_request to invoke the launcher without real infra."""
    def _submit(req, _launcher_arg=None, _l=launcher):
        _l.launch(SimpleNamespace(job_id=req.routine_id))
        return SimpleNamespace(job_id=req.routine_id)
    monkeypatch.setattr("agency.dispatch.run.submit_job_request", _submit)


def test_missed_morning_occurrence_recovers_later_the_same_day(
    tmp_path, monkeypatch
):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00"}}],
    )
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    launcher = _RecordingLauncher()
    _patch_submit(monkeypatch, launcher)

    run_dispatch_cycle(None, config_path, launcher)

    assert len(launcher.launched) == 1
    marker = at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-29"
    )
    assert marker.exists()


def test_recovery_marks_the_occurrence_day_not_today(tmp_path, monkeypatch):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00", "catch_up": "always"}}],
    )
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T03:00:00")
    launcher = _RecordingLauncher()
    _patch_submit(monkeypatch, launcher)

    run_dispatch_cycle(None, config_path, launcher)

    assert len(launcher.launched) == 1
    assert at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-28"
    ).exists()
    assert not at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-29"
    ).exists()


def test_default_bound_forgets_yesterdays_occurrence(tmp_path, monkeypatch):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00"}}],
    )
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T03:00:00")
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda req, launcher=None: submitted.append(req.routine_id),
    )

    run_dispatch_cycle(None, config_path, _RecordingLauncher())

    assert submitted == []
    assert not at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-28"
    ).exists()


def test_an_already_marked_occurrence_does_not_run_again(tmp_path, monkeypatch):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00"}}],
    )
    marker = at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-29"
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    stamp = marker.stat().st_mtime
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda req, launcher=None: submitted.append(req.routine_id),
    )

    run_dispatch_cycle(None, config_path, _RecordingLauncher())

    assert submitted == []
    assert marker.stat().st_mtime == stamp


def test_every_marker_anchors_on_the_occurrence_not_the_launch(
    tmp_path, monkeypatch
):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "audit", "prompt_name": "daily-review",
                   "schedule": {"every": "6h", "catch_up": "always"}}],
    )
    marker = every_marker_path(group_root / "logs", "product", "audit")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    anchor = datetime(2026, 7, 29, 3, 0).timestamp()
    os.utime(marker, (anchor, anchor))
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda req, launcher=None: SimpleNamespace(job_id=req.routine_id),
    )

    run_dispatch_cycle(None, config_path, _RecordingLauncher())

    assert datetime.fromtimestamp(marker.stat().st_mtime) == datetime(
        2026, 7, 29, 9, 0
    )


def test_a_routine_that_is_merely_not_due_is_not_reported_as_broken(
    tmp_path, monkeypatch, caplog
):
    """`every` returns no occurrence both when not due and when unreadable."""
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "audit", "prompt_name": "daily-review",
                   "schedule": {"every": "6h"}}],
    )
    marker = every_marker_path(group_root / "logs", "product", "audit")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    anchor = datetime(2026, 7, 29, 9, 0).timestamp()
    os.utime(marker, (anchor, anchor))
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T10:00:00")

    with caplog.at_level("WARNING", logger="agency.dispatch.run"):
        run_dispatch_cycle(None, config_path, _RecordingLauncher())

    assert "no usable schedule" not in caplog.text


def test_a_routine_with_an_unreadable_period_is_still_reported(
    tmp_path, monkeypatch, caplog
):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "audit", "prompt_name": "daily-review",
                   "schedule": {"every": "6h"}}],
    )
    monkeypatch.setattr(
        "agency.dispatch.run.parse_every", lambda value: None
    )
    marker = every_marker_path(group_root / "logs", "product", "audit")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    with caplog.at_level("WARNING", logger="agency.dispatch.run"):
        run_dispatch_cycle(None, config_path, _RecordingLauncher())

    assert "no usable schedule" in caplog.text


def test_a_dispatch_cycle_drains_before_it_evaluates_routines(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr("agency.dispatch.run.drain", lambda *a, **k: order.append("drain"))
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job_request",
        lambda *a, **k: order.append("submit"),
    )
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path, workspace, group_root,
        routines=[{"id": "daily-review", "prompt_name": "daily-review", "schedule": {"every": "1h"}}],
    )
    run_dispatch_cycle(None, config_path, _RecordingLauncher())
    assert order.index("drain") < order.index("submit")
