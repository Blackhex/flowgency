from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.issues import ValidationFailed
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration, get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    ResolvedPermissionRule,
    RuntimeCapabilities,
)


class Scoping(BaseIntegration):
    name = "scoping"
    display_name = "Scoping"
    supports_execution = True
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"restricted", "unrestricted"}),
        path_scopable_tools=frozenset({"write"}),
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


class Flat(Scoping):
    name = "flat"
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"unrestricted"}),
    )


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=60,
        mode=mode,
        rules=tuple(
            ResolvedPermissionRule(path=Path(p), tools=t) for p, t in rules
        ),
    )


def test_flat_integration_rejects_an_unsupported_mode():
    issues = Flat().validate_runtime_policy(policy(("/a", ("read",))))

    assert "unsupported-permission-mode" in {i.code for i in issues}


def test_flat_integration_rejects_differing_tool_grants():
    unrestricted = policy(
        ("/a", ("read",)), ("/b", ("read", "write")), mode="unrestricted"
    )

    issues = Flat().validate_runtime_policy(unrestricted)

    assert [i.code for i in issues] == ["unsupported-tool-scoping"]
    assert "write" in issues[0].message
    assert "flat" in issues[0].message


def test_flat_integration_accepts_uniform_grants():
    uniform = policy(("/a", ("read",)), ("/b", ("read",)), mode="unrestricted")

    assert Flat().validate_runtime_policy(uniform) == ()


def test_flat_integration_rejects_an_unnameable_difference():
    # /a grants everything and /b grants nothing. No tool name expresses that
    # difference, so the sentinel stands in for it — what must not happen is
    # negotiation reporting "nothing to scope".
    mixed = policy(("/a", None), ("/b", ()), mode="unrestricted")

    issues = Flat().validate_runtime_policy(mixed)

    assert [i.code for i in issues] == ["unsupported-tool-scoping"]
    assert "any tool" in issues[0].message


def test_scoping_integration_accepts_differing_write_grants():
    assert Scoping().validate_runtime_policy(
        policy(("/a", ("read",)), ("/b", ("read", "write")))
    ) == ()


def test_only_copilot_scopes_write_and_only_when_detection_succeeds(monkeypatch):
    # Copilot enforces per-path write through its sandbox; the other eight have
    # no boundary to offer, so a scoped write grant must still be refused.
    copilot = get_integration("copilot")
    # Stubbed so the assertion holds whether or not a CLI is installed here.
    monkeypatch.setattr(type(copilot), "_cli_version", lambda self: "1.0.78-2")
    copilot.invalidate_capability_cache()
    try:
        assert "write" in copilot.runtime_capabilities.path_scopable_tools
    finally:
        copilot.invalidate_capability_cache()

    # Detection failing must cost the claim, not be assumed away.
    monkeypatch.setattr(type(copilot), "_cli_version", lambda self: None)
    copilot.invalidate_capability_cache()
    try:
        assert "write" not in copilot.runtime_capabilities.path_scopable_tools
    finally:
        copilot.invalidate_capability_cache()

    for name in (
        "claude-code", "codex", "gemini",
        "aider", "goose", "opencode", "pi", "script",
    ):
        caps = get_integration(name).runtime_capabilities
        assert "write" not in caps.path_scopable_tools, name


# ── Rejection through resolve_effective_policy ──────────────────────────────


def _resolved_config(tmp_path, raw_config, *, integration, group_mode, group_rules, agent_rules):
    raw = deepcopy(raw_config)
    raw["schema_version"] = 6
    raw["teams"]["newsletter"]["runtime"] = {
        "permissions": {"mode": group_mode, "rules": group_rules}
    }
    agent = raw["teams"]["newsletter"]["agents"][0]
    agent.pop("capabilities", None)
    agent["integration"] = integration
    agent["runtime"] = {"permissions": {"rules": agent_rules}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_restricted_mode_rejected_through_resolution(tmp_path, raw_config):
    # claude-code only supports "unrestricted"; a restricted policy must be
    # rejected by resolve_effective_policy, not just the direct validator.
    config = _resolved_config(
        tmp_path,
        raw_config,
        integration="claude-code",
        group_mode="restricted",
        group_rules=[{"path": "C:/ws", "tools": ["read"]}],
        agent_rules=[],
    )

    with pytest.raises(ValidationFailed) as exc_info:
        resolve_effective_policy(config, "newsletter", "builder")

    assert "unsupported-permission-mode" in {i.code for i in exc_info.value.issues}


def test_scoped_tools_rejected_through_resolution(tmp_path, raw_config):
    # claude-code has empty path_scopable_tools; rules that grant "write" on
    # only one path produce scoped_tools={"write"}, which must be rejected by
    # resolve_effective_policy, not just the direct validator.
    config = _resolved_config(
        tmp_path,
        raw_config,
        integration="claude-code",
        group_mode="unrestricted",
        group_rules=[{"path": "C:/ws", "tools": ["read"]}],
        agent_rules=[{"path": "C:/ws/tests", "tools": ["read", "write"]}],
    )

    with pytest.raises(ValidationFailed) as exc_info:
        resolve_effective_policy(config, "newsletter", "builder")

    assert "unsupported-tool-scoping" in {i.code for i in exc_info.value.issues}


# ── Generated rules excluded from negotiation ───────────────────────────────


def test_scoped_tools_ignores_generated_rules():
    # Generated rules differ in tools (read vs read+write), but since they are
    # generated they must not appear in scoped_tools.
    p = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=Path("/ws"), tools=("read",)),
            ResolvedPermissionRule(path=Path("/launch/instructions"), tools=("read",), generated=True),
            ResolvedPermissionRule(path=Path("/launch/outbox"), tools=("read", "write"), generated=True),
            ResolvedPermissionRule(path=Path("/launch/memory"), tools=("read", "write"), generated=True),
        ),
    )

    assert p.scoped_tools == frozenset()


def test_scoped_tools_still_reports_differing_authored_rules():
    # Authored rules that genuinely differ must still be reported.
    p = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=Path("/ws"), tools=("read",)),
            ResolvedPermissionRule(path=Path("/ws/tests"), tools=("read", "write")),
            ResolvedPermissionRule(path=Path("/launch/outbox"), tools=("read", "write"), generated=True),
        ),
    )

    assert "write" in p.scoped_tools


@pytest.mark.parametrize("name", [
    "copilot", "claude-code", "script",
    "codex", "gemini", "aider", "goose", "opencode", "pi",
])
def test_zoned_policy_passes_validation_for_shipped_integration(name, tmp_path):
    """A fully zoned effective policy must pass validate_runtime_policy for
    every shipped integration. This is the test that would have caught C3."""
    integration = get_integration(name)
    authored = EffectiveRuntimePolicy(
        timeout=60,
        mode="unrestricted",
        rules=(
            ResolvedPermissionRule(path=tmp_path / "workspace", tools=("read",)),
        ),
    )
    zoned = authored.with_launch_zones(tmp_path / "launch")

    issues = integration.validate_runtime_policy(zoned)

    assert issues == (), f"{name} rejected zoned policy: {[i.code for i in issues]}"


def test_zoned_restricted_policy_passes_validation_for_copilot(tmp_path):
    # Copilot is the only shipped integration that declares `restricted`, and
    # restricted + zones is the combination it actually runs under.
    integration = get_integration("copilot")
    authored = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=tmp_path / "workspace", tools=("read",)),
        ),
    )
    zoned = authored.with_launch_zones(tmp_path / "launch")

    assert integration.validate_runtime_policy(zoned) == ()


