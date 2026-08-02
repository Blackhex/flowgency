"""Copilot credentials follow executor eligibility, and the launch env is reduced."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agency.integrations.agency.copilot import CopilotIntegration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
)


class _FakeCompleted:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _launch(policy, workspace, tmp_path, monkeypatch):
    """Run Copilot against a stubbed subprocess and return (captured, job_home)."""
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
    # Pin the detected capability rather than inheriting whichever CLI is
    # installed on the machine running the suite: a policy that varies write
    # between paths is only legal when the sandbox can scope it.
    monkeypatch.setattr(CopilotIntegration, "_cli_version", lambda self: "1.0.78-2")

    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / "config.json").write_text('{"authProvider":"github"}', encoding="utf-8")
    monkeypatch.setenv("COPILOT_HOME", str(real_home))

    launch_dir = tmp_path / "launch" / "runtime"
    launch_dir.mkdir(parents=True, exist_ok=True)

    CopilotIntegration().run(
        IntegrationRunRequest(
            workspace_root=workspace,
            launch_dir=launch_dir,
            task_file=prompt,
            timeout=60,
            runtime_policy=policy,
            skill=None,
            skill_arguments=(),
        )
    )
    return captured, launch_dir.parent / ".copilot"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(timeout=60, mode=mode, rules=tuple(rules))


def _settings(job_home: Path) -> dict:
    return json.loads((job_home / "settings.json").read_text(encoding="utf-8"))


# ── credentials ─────────────────────────────────────────────────────────────


def test_reader_gets_no_git_or_github_credentials(tmp_path, monkeypatch, workspace):
    policy = _policy(ResolvedPermissionRule(path=workspace, tools=("read",)))

    _captured, job_home = _launch(policy, workspace, tmp_path, monkeypatch)

    settings = _settings(job_home)
    assert settings["gitAuth"] is False
    assert settings["ghAuth"] is False


def test_workspace_writer_gets_git_and_github_credentials(
    tmp_path, monkeypatch, workspace
):
    policy = _policy(ResolvedPermissionRule(path=workspace, tools=("read", "write")))

    _captured, job_home = _launch(policy, workspace, tmp_path, monkeypatch)

    settings = _settings(job_home)
    assert settings["gitAuth"] is True
    assert settings["ghAuth"] is True


def test_write_on_a_subdirectory_is_not_enough(tmp_path, monkeypatch, workspace):
    policy = _policy(
        ResolvedPermissionRule(path=workspace, tools=("read",)),
        ResolvedPermissionRule(path=workspace / "scratch", tools=("read", "write")),
    )

    _captured, job_home = _launch(policy, workspace, tmp_path, monkeypatch)

    settings = _settings(job_home)
    assert settings["gitAuth"] is False
    assert settings["ghAuth"] is False


# ── launch environment ──────────────────────────────────────────────────────


def test_launch_environment_drops_unrelated_credentials(
    tmp_path, monkeypatch, workspace
):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-the-agent")
    monkeypatch.setenv("NPM_TOKEN", "not-for-the-agent")
    monkeypatch.setenv("GITHUB_TOKEN", "not-for-the-agent")
    policy = _policy(ResolvedPermissionRule(path=workspace, tools=("read",)))

    captured, _job_home = _launch(policy, workspace, tmp_path, monkeypatch)

    env = captured["kwargs"]["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "NPM_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_launch_environment_keeps_the_essentials(tmp_path, monkeypatch, workspace):
    policy = _policy(ResolvedPermissionRule(path=workspace, tools=("read",)))

    captured, job_home = _launch(policy, workspace, tmp_path, monkeypatch)

    # Windows folds the case of environment names, so compare folded.
    env = {name.upper(): value for name, value in captured["kwargs"]["env"].items()}
    assert env["COPILOT_HOME"] == str(job_home)
    assert env["PATH"] == os.environ["PATH"]
    required = (
        ("SYSTEMROOT", "COMSPEC", "TEMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
        if os.name == "nt"
        else ("HOME",)
    )
    for name in required:
        if name in os.environ:
            assert env[name] == os.environ[name]


def test_launch_environment_injects_no_empty_values(tmp_path, monkeypatch, workspace):
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("NUMBER_OF_PROCESSORS", raising=False)
    policy = _policy(ResolvedPermissionRule(path=workspace, tools=("read",)))

    captured, _job_home = _launch(policy, workspace, tmp_path, monkeypatch)

    env = {name.upper() for name in captured["kwargs"]["env"]}
    assert "LANG" not in env
    assert "NUMBER_OF_PROCESSORS" not in env
