import pytest
import os
import time
from datetime import datetime
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


def _enabled_config(group_path):
    return {
        "agency": {"dispatch": {"interval": 15}},
        "groups": {
            "test": {
                "path": str(group_path),
                "agents": ["product"],
                "dispatch": {
                    "enabled": True,
                    "agents": {"product": [{"prompt": "routine.md", "every": "1h"}]},
                },
            }
        },
    }


def test_due_schedule_submits_snapshot_then_touches_marker(tmp_path, monkeypatch):
    group_path, _, _ = _make_group(tmp_path)
    config_path = tmp_path / "config.yaml"
    config = _enabled_config(group_path)
    captured = []

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda spec, launcher=None: captured.append(spec) or object(),
    )

    run_dispatch_cycle(config, config_path)

    assert captured[0].trigger == "scheduled_prompt"
    assert captured[0].prompt_content == "do the thing"
    assert captured[0].timeout_override is None
    assert (group_path / "shared" / "logs" / ".last-product-routine").exists()


def test_schedule_does_not_touch_marker_when_submission_fails(tmp_path, monkeypatch):
    group_path, _, _ = _make_group(tmp_path)
    config = _enabled_config(group_path)

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            JobSubmissionError("no", tmp_path / "job")
        ),
    )

    run_dispatch_cycle(config, tmp_path / "config.yaml")

    assert not (group_path / "shared" / "logs" / ".last-product-routine").exists()


def test_schedule_does_not_touch_marker_when_spec_validation_fails(tmp_path, monkeypatch):
    group_path, _, _ = _make_group(tmp_path)
    (group_path / "shared" / "prompts" / "routine.md").write_text("\n")
    config = _enabled_config(group_path)
    submit_calls = []

    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda spec, launcher=None: submit_calls.append(spec) or object(),
    )

    run_dispatch_cycle(config, tmp_path / "config.yaml")

    assert submit_calls == []
    assert not (group_path / "shared" / "logs" / ".last-product-routine").exists()


def test_one_heartbeat_submits_due_work_for_multiple_enabled_groups(tmp_path, monkeypatch):
    first_path, _, _ = _make_group(tmp_path / "first")
    second_path, _, _ = _make_group(tmp_path / "second")
    config = {
        "agency": {"dispatch": {"interval": 15}},
        "groups": {
            "first": _enabled_config(first_path)["groups"]["test"],
            "second": _enabled_config(second_path)["groups"]["test"],
        },
    }
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda spec, launcher=None: submitted.append((spec.group_key, spec.agent_name)),
    )
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    assert submitted == [("first", "product"), ("second", "product")]


def test_repeated_heartbeat_does_not_duplicate_daily_at_rule(tmp_path, monkeypatch):
    group_path, _, _ = _make_group(tmp_path)
    config = _enabled_config(group_path)
    config["groups"]["test"]["dispatch"]["agents"]["product"] = [
        {"prompt": "routine.md", "at": datetime.now().strftime("%H:%M")},
    ]
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda spec, launcher=None: submitted.append(spec),
    )
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    assert len(submitted) == 1


def test_disabled_group_is_skipped_in_multi_group_config(tmp_path, monkeypatch):
    enabled_path, _, _ = _make_group(tmp_path / "enabled")
    disabled_path, _, _ = _make_group(tmp_path / "disabled")
    disabled_group = _enabled_config(disabled_path)["groups"]["test"]
    disabled_group["dispatch"]["enabled"] = False
    config = {
        "agency": {"dispatch": {"interval": 15}},
        "groups": {
            "enabled": _enabled_config(enabled_path)["groups"]["test"],
            "disabled": disabled_group,
        },
    }
    submitted = []
    monkeypatch.setattr(
        "agency.dispatch.run.submit_job",
        lambda spec, launcher=None: submitted.append(spec.group_key),
    )
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    assert submitted == ["enabled"]
