"""What the Copilot integration actually puts on the command line.

Copilot has a single global tool allowlist and a set of accessible
directories, so it cannot vary a tool from one path to another. Every
rendering below must therefore grant no more than the policy grants on the
paths it makes reachable: under-granting is acceptable, over-granting is not.
"""

from __future__ import annotations

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


def _launch(policy, tmp_path, monkeypatch, *, enforce_validation=True) -> list[str]:
    """Run Copilot against `policy` and return the argv it would have used."""
    import agency.integrations.agency.copilot as copilot_mod

    prompt = tmp_path / "p.prompt"
    prompt.write_text("do the thing", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        return _FakeCompleted()

    monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "copilot")

    CopilotIntegration().run(
        IntegrationRunRequest(
            workspace_root=tmp_path,
            launch_dir=tmp_path / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=policy,
            skill=None,
            skill_arguments=(),
            enforce_validation=enforce_validation,
        )
    )
    return captured["args"]


def _granted(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "--allow-tool"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


# ── The empty grant ─────────────────────────────────────────────────────────


def test_empty_tool_grant_grants_nothing(tmp_path, monkeypatch, repo):
    """tools: [] is the most restrictive policy expressible. It must not
    produce the most permissive launch."""
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=()),),
    )

    args = _launch(policy, tmp_path, monkeypatch)

    assert "--allow-all-tools" not in args
    assert "--autopilot" not in args
    assert _granted(args) == []
    assert "--add-dir" in args and str(repo) in args


def test_restricted_without_rules_grants_nothing(tmp_path, monkeypatch):
    """Restricted with no rule reaches nothing and grants nothing; it must not
    fall open on either axis."""
    policy = EffectiveRuntimePolicy(timeout=60, mode="restricted", rules=())

    args = _launch(policy, tmp_path, monkeypatch)

    assert "--allow-all-paths" not in args
    assert "--allow-all-tools" not in args
    assert "--autopilot" not in args
    assert _granted(args) == []


# ── tools omitted ───────────────────────────────────────────────────────────


def test_omitted_tools_grants_every_tool(tmp_path, monkeypatch, repo):
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=None),),
    )

    args = _launch(policy, tmp_path, monkeypatch)

    assert "--allow-all-tools" in args
    assert "--autopilot" in args
    assert _granted(args) == []
    assert str(repo) in args


def test_unrestricted_without_rules_grants_everything(tmp_path, monkeypatch):
    policy = EffectiveRuntimePolicy(timeout=60, mode="unrestricted", rules=())

    args = _launch(policy, tmp_path, monkeypatch)

    assert "--allow-all-paths" in args
    assert "--allow-all-tools" in args
    assert "--autopilot" in args


# ── An explicit narrow list ─────────────────────────────────────────────────


def test_explicit_list_is_rendered_exactly(tmp_path, monkeypatch, repo):
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read", "search")),),
    )

    args = _launch(policy, tmp_path, monkeypatch)

    assert _granted(args) == ["read", "search"]
    assert "--allow-all-tools" not in args
    assert "--autopilot" not in args


def test_unrestricted_carve_out_does_not_promote_to_every_tool(tmp_path, monkeypatch, repo):
    """`mode` decides only what happens to an uncovered path. It must not
    widen the grant on a path a rule does cover."""
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="unrestricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )

    args = _launch(policy, tmp_path, monkeypatch)

    assert _granted(args) == ["read"]
    assert "--allow-all-tools" not in args
    assert "--autopilot" not in args


def test_differing_grants_render_the_intersection(tmp_path, monkeypatch):
    """Copilot's allowlist is global, so the only sound rendering of two rules
    that disagree is what both permit. This is the rendering used when the
    sandbox cannot scope the differing tool; when it can, the tool is granted
    globally and the sandbox holds it to the paths."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=a, tools=("read", "write")),
            ResolvedPermissionRule(path=b, tools=("read",)),
        ),
    )

    args = _launch(policy, tmp_path, monkeypatch, enforce_validation=False)

    assert _granted(args) == ["read"]


# ── Generated launch-zone rules ─────────────────────────────────────────────


def test_zone_grants_do_not_widen_the_allowlist(tmp_path, monkeypatch, repo):
    """With no sandbox to confine it, a global write grant would reach every
    path, so the zones stay advisory. The fake CLI here reports an
    unparseable version, which is what withholds the scoping claim.
    An operator who granted read must launch with read."""
    authored = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )
    policy = authored.with_launch_zones(tmp_path / "runtime")

    args = _launch(policy, tmp_path, monkeypatch)

    assert _granted(args) == ["read"]
    assert "write" not in args
    assert "--allow-all-tools" not in args


def test_zone_paths_are_not_added_as_roots(tmp_path, monkeypatch, repo):
    authored = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )
    policy = authored.with_launch_zones(tmp_path / "runtime")

    args = _launch(policy, tmp_path, monkeypatch)

    added = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
    assert added == [str(repo)]


def test_zones_alone_do_not_open_an_empty_restricted_policy(tmp_path, monkeypatch):
    """A restricted policy with no authored rule is still an empty grant once
    the zones are attached."""
    policy = EffectiveRuntimePolicy(
        timeout=60, mode="restricted", rules=()
    ).with_launch_zones(tmp_path / "runtime")

    args = _launch(policy, tmp_path, monkeypatch)

    assert "--allow-all-paths" not in args
    assert "--allow-all-tools" not in args
    assert "--autopilot" not in args
    assert _granted(args) == []


# ── Tools the sandbox can scope ─────────────────────────────────────────────


def _with_sandbox(monkeypatch, tmp_path, *, confines=True):
    """Make the scoping claim and the sandbox's presence explicit, not ambient.

    Otherwise both depend on the CLI installed on the machine running the suite
    and on whether it has ever been logged in.
    """
    monkeypatch.setattr(CopilotIntegration, "_cli_version", lambda self: "1.0.78-2")
    home = (tmp_path / "job_home") if confines else None
    monkeypatch.setattr(
        CopilotIntegration,
        "_prepare_copilot_home",
        lambda self, request, settings: (home, None if confines else "no credentials"),
    )


def test_a_zone_write_is_granted_when_the_sandbox_confines_it(tmp_path, monkeypatch, repo):
    """The tool gate decides whether write may be used, the sandbox decides
    where it lands. Vetoing write here would make the sandbox's own outbox
    grant unreachable, which is the whole point of the zones."""
    _with_sandbox(monkeypatch, tmp_path)
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    ).with_launch_zones(tmp_path / "runtime")

    args = _launch(policy, tmp_path, monkeypatch)

    assert _granted(args) == ["read", "write"]
    assert "--allow-all-tools" not in args
    # The paths stay scoped: write is only reachable where the sandbox allows.
    added = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
    assert added == [str(repo)]


def test_a_scoped_write_is_not_granted_without_a_sandbox(tmp_path, monkeypatch, repo):
    """Falling back to the shared home writes no settings, so nothing would
    confine a global write grant. The claim must lapse with the sandbox."""
    _with_sandbox(monkeypatch, tmp_path, confines=False)
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    ).with_launch_zones(tmp_path / "runtime")

    args = _launch(policy, tmp_path, monkeypatch)

    assert _granted(args) == ["read"]


def test_write_is_not_granted_when_no_rule_grants_it(tmp_path, monkeypatch, repo):
    """A scopable tool is still only granted where some rule asked for it."""
    _with_sandbox(monkeypatch, tmp_path)
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )

    args = _launch(policy, tmp_path, monkeypatch)

    assert _granted(args) == ["read"]


def test_an_authored_scoped_write_survives_the_intersection(tmp_path, monkeypatch):
    """Two rules disagreeing on write used to render as read-only. The sandbox
    can now hold write to the path that was granted it, so it is granted."""
    _with_sandbox(monkeypatch, tmp_path)
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=a, tools=("read", "write")),
            ResolvedPermissionRule(path=b, tools=("read",)),
        ),
    )

    args = _launch(policy, tmp_path, monkeypatch, enforce_validation=False)

    assert _granted(args) == ["read", "write"]
