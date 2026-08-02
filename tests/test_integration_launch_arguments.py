"""What the shipped AI CLI integrations put on the command line.

Two of them disable their own CLI's permission model on every run. That model
is the only enforcement those integrations have, so switching it off on an
agent the operator restricted hands the agent more than the policy grants --
and does it silently, which is the worse half.
"""

from __future__ import annotations

import pytest

from agency.integrations import get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
)


CLI_INTEGRATIONS = (
    "aider",
    "claude-code",
    "codex",
    "gemini",
    "goose",
    "opencode",
    "pi",
)


class _FakeCompleted:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _launch(name, policy, tmp_path, monkeypatch) -> list[str]:
    """Run `name` against `policy` and return the argv it would have used."""
    integration = get_integration(name)
    module = type(integration).__module__
    captured: dict[str, list[str]] = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        return _FakeCompleted()

    import importlib

    monkeypatch.setattr(
        importlib.import_module(module).subprocess, "run", fake_run
    )
    monkeypatch.setattr(
        type(integration), "require_executable", lambda self: "tool"
    )

    prompt = tmp_path / "p.prompt"
    prompt.write_text("do the thing", encoding="utf-8")
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir(exist_ok=True)

    integration.run(
        IntegrationRunRequest(
            workspace_root=tmp_path,
            launch_dir=launch_dir,
            task_file=prompt,
            timeout=60,
            runtime_policy=policy,
        )
    )
    return captured["args"]


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _read_only(repo):
    # These integrations declare only `unrestricted`, so a restricted policy
    # never reaches them -- negotiation rejects it first. The case that does
    # reach them, and that this protects, is an unrestricted policy carrying an
    # authored rule that withholds write.
    return EffectiveRuntimePolicy(
        timeout=60,
        mode="unrestricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )


def _writer(repo):
    return EffectiveRuntimePolicy(
        timeout=60,
        mode="unrestricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read", "write")),),
    )


# ── the two bypass flags ────────────────────────────────────────────────────


def test_claude_code_keeps_its_permission_model_for_a_reader(
    tmp_path, monkeypatch, repo
):
    args = _launch("claude-code", _read_only(repo), tmp_path, monkeypatch)

    assert "--dangerously-skip-permissions" not in args


def test_claude_code_skips_permissions_for_a_writer(tmp_path, monkeypatch, repo):
    """The flag is what makes an unattended run possible, so an agent the
    operator did trust with write still gets it."""
    args = _launch("claude-code", _writer(repo), tmp_path, monkeypatch)

    assert "--dangerously-skip-permissions" in args


def test_codex_keeps_its_permission_model_for_a_reader(tmp_path, monkeypatch, repo):
    args = _launch("codex", _read_only(repo), tmp_path, monkeypatch)

    assert "--yolo" not in args


def test_codex_goes_unattended_for_a_writer(tmp_path, monkeypatch, repo):
    args = _launch("codex", _writer(repo), tmp_path, monkeypatch)

    assert "--yolo" in args


def test_the_prompt_survives_withholding_the_flag(tmp_path, monkeypatch, repo):
    """Dropping a flag must not shift the positional arguments around it."""
    claude = _launch("claude-code", _read_only(repo), tmp_path, monkeypatch)
    codex = _launch("codex", _read_only(repo), tmp_path, monkeypatch)

    assert claude[:2] == ["tool", "-p"]
    assert claude[2] == "do the thing"
    assert codex[:2] == ["tool", "exec"]
    assert codex[2] == "do the thing"


def test_an_agent_nobody_restricted_is_still_unattended(tmp_path, monkeypatch):
    """Withholding the flag on the default configuration would break every
    existing unattended run, and would not be protecting anything: with no
    authored rule at all, an unrestricted policy withholds nothing."""
    policy = EffectiveRuntimePolicy(timeout=60, mode="unrestricted", rules=())

    assert "--dangerously-skip-permissions" in _launch(
        "claude-code", policy, tmp_path, monkeypatch
    )
    assert "--yolo" in _launch("codex", policy, tmp_path, monkeypatch)


def test_zone_grants_alone_do_not_restore_the_bypass(tmp_path, monkeypatch, repo):
    """Every job carries generated write grants on its own outbox. Counting
    them would switch the bypass back on for exactly the read-only agents this
    exists to protect."""
    policy = _read_only(repo).with_launch_zones(tmp_path / "launch")

    assert "--dangerously-skip-permissions" not in _launch(
        "claude-code", policy, tmp_path, monkeypatch
    )
    assert "--yolo" not in _launch("codex", policy, tmp_path, monkeypatch)


# ── shared expectations ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", CLI_INTEGRATIONS)
def test_no_generated_zone_path_reaches_the_command_line(
    name, tmp_path, monkeypatch, repo
):
    """The zones are Agency's own bookkeeping. An integration that cannot
    scope a tool to a path must not advertise those paths to the agent as
    though they were part of the operator's grant."""
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir(exist_ok=True)
    policy = _writer(repo).with_launch_zones(launch_dir)

    args = _launch(name, policy, tmp_path, monkeypatch)

    for rule in policy.rules:
        if not rule.generated or rule.path is None:
            continue
        assert not any(str(rule.path) in arg for arg in args)
