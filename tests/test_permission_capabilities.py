from __future__ import annotations

from pathlib import Path

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


def test_scoping_integration_accepts_differing_write_grants():
    assert Scoping().validate_runtime_policy(
        policy(("/a", ("read",)), ("/b", ("read", "write")))
    ) == ()


def test_no_shipped_integration_scopes_write():
    for name in (
        "copilot", "claude-code", "codex", "gemini",
        "aider", "goose", "opencode", "pi", "script",
    ):
        caps = get_integration(name).runtime_capabilities
        assert "write" not in caps.path_scopable_tools, name
