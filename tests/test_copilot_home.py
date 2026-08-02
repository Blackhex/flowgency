"""Per-job COPILOT_HOME: sandbox settings, credential seeding, env wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agency.integrations.agency.copilot import CopilotIntegration
from agency.integrations.agency.copilot_sandbox import build_sandbox_settings
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
)


# ── helpers ─────────────────────────────────────────────────────────────────


class _FakeCompleted:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _launch(policy, tmp_path, monkeypatch, *, real_home=None):
    """Run Copilot and return (captured, result, launch_dir).

    ``captured`` contains ``args`` and ``kwargs`` from the subprocess.run call.
    """
    import agency.integrations.agency.copilot as copilot_mod

    prompt = tmp_path / "p.prompt"
    prompt.write_text("do the thing", encoding="utf-8")
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeCompleted()

    monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "copilot")

    if real_home is None:
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        (real_home / "config.json").write_text(
            '{"authProvider":"github"}', encoding="utf-8",
        )
    monkeypatch.setenv("COPILOT_HOME", str(real_home))

    launch_dir = tmp_path / "runtime"
    launch_dir.mkdir(parents=True, exist_ok=True)

    result = CopilotIntegration().run(
        IntegrationRunRequest(
            workspace_root=tmp_path,
            launch_dir=launch_dir,
            task_file=prompt,
            timeout=60,
            runtime_policy=policy,
            skill=None,
            skill_arguments=(),
        )
    )
    return captured, result, launch_dir


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _policy(repo: Path) -> EffectiveRuntimePolicy:
    return EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )


# ── settings.json ──────────────────────────────────────────────────────────


def test_prepared_home_contains_settings_json(tmp_path, monkeypatch, repo):
    policy = _policy(repo)
    _captured, _result, launch_dir = _launch(policy, tmp_path, monkeypatch)

    settings_path = launch_dir.parent / ".copilot" / "settings.json"
    assert settings_path.is_file()
    written = json.loads(settings_path.read_text(encoding="utf-8"))
    expected, _ = build_sandbox_settings(policy)
    assert written == expected


# ── config.json ────────────────────────────────────────────────────────────


def test_prepared_home_contains_config_json(tmp_path, monkeypatch, repo):
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / "config.json").write_text('{"tok":"x"}', encoding="utf-8")

    policy = _policy(repo)
    _captured, _result, launch_dir = _launch(
        policy, tmp_path, monkeypatch, real_home=real_home,
    )

    copied = launch_dir.parent / ".copilot" / "config.json"
    assert copied.is_file()
    assert json.loads(copied.read_text(encoding="utf-8")) == {"tok": "x"}


# ── env wiring ─────────────────────────────────────────────────────────────


def test_subprocess_receives_copilot_home_env(tmp_path, monkeypatch, repo):
    policy = _policy(repo)
    captured, _result, launch_dir = _launch(policy, tmp_path, monkeypatch)

    env = captured["kwargs"].get("env")
    assert env is not None
    assert env["COPILOT_HOME"] == str(launch_dir.parent / ".copilot")


def test_home_lives_under_launch_dir(tmp_path, monkeypatch, repo):
    policy = _policy(repo)
    captured, _result, launch_dir = _launch(policy, tmp_path, monkeypatch)

    home = Path(captured["kwargs"]["env"]["COPILOT_HOME"])
    assert home.parent == launch_dir.parent


def test_copilot_home_on_run_result(tmp_path, monkeypatch, repo):
    policy = _policy(repo)
    _captured, result, launch_dir = _launch(policy, tmp_path, monkeypatch)

    assert result.copilot_home == str(launch_dir.parent / ".copilot")


# ── missing credentials fallback ───────────────────────────────────────────


def test_missing_config_falls_back_to_shared_home(tmp_path, monkeypatch, repo):
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()

    policy = _policy(repo)
    captured, result, _launch_dir = _launch(
        policy, tmp_path, monkeypatch, real_home=empty_home,
    )

    assert captured["kwargs"].get("env") is None
    assert result.copilot_home is None


def test_missing_config_surfaces_warning(tmp_path, monkeypatch, repo):
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()

    policy = _policy(repo)
    _captured, result, _launch_dir = _launch(
        policy, tmp_path, monkeypatch, real_home=empty_home,
    )

    assert "sandbox:" in result.stderr
    assert "credentials" in result.stderr


# ── _usage_summary reads from the right home ──────────────────────────────


def test_usage_summary_reads_from_explicit_home(tmp_path):
    session_id = "test-session-001"
    home = tmp_path / "job_home"
    state_dir = home / "session-state" / session_id
    state_dir.mkdir(parents=True)
    shutdown = {
        "type": "session.shutdown",
        "data": {
            "tokenDetails": {
                "input": {"tokenCount": 100},
                "cache_read": {"tokenCount": 50},
                "cache_write": {"tokenCount": 10},
                "output": {"tokenCount": 30},
            },
            "codeChanges": {"linesAdded": 5, "linesRemoved": 2},
            "totalPremiumRequests": 1,
        },
    }
    (state_dir / "events.jsonl").write_text(
        json.dumps(shutdown) + "\n", encoding="utf-8",
    )

    raw = json.dumps({
        "type": "result",
        "sessionId": session_id,
        "usage": {"sessionDurationMs": 10000},
    })

    summary = CopilotIntegration._usage_summary(raw, copilot_home=home)
    assert "Changes" in summary
    assert session_id in summary


def test_usage_summary_ignores_os_environ_when_home_given(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "wrong"))

    session_id = "sess-right"
    right_home = tmp_path / "right"
    state_dir = right_home / "session-state" / session_id
    state_dir.mkdir(parents=True)
    (state_dir / "events.jsonl").write_text(
        json.dumps({
            "type": "session.shutdown",
            "data": {
                "tokenDetails": {},
                "codeChanges": {},
                "totalPremiumRequests": 0,
            },
        }) + "\n",
        encoding="utf-8",
    )

    raw = json.dumps({
        "type": "result",
        "sessionId": session_id,
        "usage": {"sessionDurationMs": 1000},
    })

    summary = CopilotIntegration._usage_summary(raw, copilot_home=right_home)
    assert session_id in summary


def test_usage_summary_resume_line_includes_copilot_home(tmp_path):
    session_id = "sess-home"
    home = tmp_path / "job_home"
    state_dir = home / "session-state" / session_id
    state_dir.mkdir(parents=True)
    (state_dir / "events.jsonl").write_text(
        json.dumps({
            "type": "session.shutdown",
            "data": {
                "tokenDetails": {},
                "codeChanges": {},
                "totalPremiumRequests": 0,
            },
        }) + "\n",
        encoding="utf-8",
    )

    raw = json.dumps({
        "type": "result",
        "sessionId": session_id,
        "usage": {"sessionDurationMs": 1000},
    })

    summary = CopilotIntegration._usage_summary(raw, copilot_home=home)
    assert f"COPILOT_HOME={home}" in summary
    assert f"--resume={session_id}" in summary


def test_usage_summary_no_home_prefix_when_default(tmp_path):
    session_id = "sess-default"
    default_home = tmp_path / ".copilot"
    state_dir = default_home / "session-state" / session_id
    state_dir.mkdir(parents=True)
    (state_dir / "events.jsonl").write_text(
        json.dumps({
            "type": "session.shutdown",
            "data": {
                "tokenDetails": {},
                "codeChanges": {},
                "totalPremiumRequests": 0,
            },
        }) + "\n",
        encoding="utf-8",
    )

    raw = json.dumps({
        "type": "result",
        "sessionId": session_id,
        "usage": {"sessionDurationMs": 1000},
    })

    summary = CopilotIntegration._usage_summary(raw, copilot_home=None)
    assert "COPILOT_HOME=" not in summary
