"""Tests for effective runtime policy resolution under the permission model."""

from pathlib import Path

import pytest

from agency.configuration import parse_config
from agency.configuration.effective import resolve_effective_policy
from agency.configuration.issues import ValidationFailed
from agency.integrations import BaseIntegration
from agency.integrations.models import RuntimeCapabilities


class _PermissiveIntegration(BaseIntegration):
    name = "copilot"
    display_name = "Copilot"
    supports_execution = True
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"restricted", "unrestricted"}),
        path_scopable_tools=frozenset({"read", "search", "write", "shell"}),
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


_INTEGRATION = _PermissiveIntegration()


def test_group_rules_are_present_in_effective_policy(raw_config, config_paths):
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [
                {"path": ws, "tools": ["read", "search"]},
            ],
        },
    }
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert policy.mode == "restricted"
    assert len(policy.rules) == 1
    assert policy.rules[0].tools == ("read", "search")


def test_agent_rules_are_additive_to_group(raw_config, config_paths):
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [
                {"path": ws, "tools": ["read"]},
            ],
        },
    }
    agent = team["agents"][0]
    agent["integration"] = "copilot"
    agent["runtime"] = {
        "permissions": {
            "rules": [
                {"path": ws, "tools": ["write"]},
            ],
        },
    }

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    # Same path: tools are unioned.
    assert len(policy.rules) == 1
    assert set(policy.rules[0].tools) == {"read", "write"}


def test_distinct_paths_remain_separate_rules(raw_config, config_paths):
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    other = str(config_paths["config_dir"] / "other")
    (config_paths["config_dir"] / "other").mkdir(exist_ok=True)
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [
                {"path": ws, "tools": ["read"]},
            ],
        },
    }
    agent = team["agents"][0]
    agent["integration"] = "copilot"
    agent["runtime"] = {
        "permissions": {
            "rules": [
                {"path": other, "tools": ["write"]},
            ],
        },
    }

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert len(policy.rules) == 2


def test_unrestricted_policy_has_empty_rules_by_default(raw_config, config_paths):
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {"mode": "unrestricted"},
    }
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert policy.mode == "unrestricted"
    assert policy.rules == ()


def test_timeout_override_precedence_is_job_then_agent_then_group(raw_config, config_paths):
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {"timeout": 900, "permissions": {"mode": "unrestricted"}}
    agent = team["agents"][0]
    agent["integration"] = "copilot"
    agent["runtime"] = {"timeout": 1200}

    parsed = parse_config(raw_config, config_paths["config_path"])

    inherited = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )
    overridden = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder",
        timeout_override=1800, integration=_INTEGRATION
    )

    assert inherited.timeout == 1200
    assert overridden.timeout == 1800


def test_mode_inherits_from_group_when_agent_omits(raw_config, config_paths):
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {"permissions": {"mode": "restricted"}}
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert policy.mode == "restricted"


def test_agent_mode_overrides_group(raw_config, config_paths):
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {"permissions": {"mode": "restricted"}}
    agent = team["agents"][0]
    agent["integration"] = "copilot"
    agent["runtime"] = {"permissions": {"mode": "unrestricted"}}

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert policy.mode == "unrestricted"


def test_pathless_rule_tools_union(raw_config, config_paths):
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [{"tools": ["read"]}],
        },
    }
    agent = team["agents"][0]
    agent["integration"] = "copilot"
    agent["runtime"] = {
        "permissions": {"rules": [{"tools": ["write"]}]},
    }

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    pathless = [r for r in policy.rules if r.path is None]
    assert len(pathless) == 1
    assert set(pathless[0].tools) == {"read", "write"}


def test_unsupported_mode_raises_validation_failed(raw_config, config_paths):
    """An integration that doesn't support restricted rejects the policy."""

    class _UnrestrictedOnly(BaseIntegration):
        name = "limited"
        display_name = "Limited"
        supports_execution = True
        runtime_capabilities = RuntimeCapabilities(
            permission_modes=frozenset({"unrestricted"}),
        )

        def identity_filename(self):
            return "AGENTS.md"

        def parse_identity(self, agent_dir):
            return None

        def write_identity(self, agent_dir, identity):
            raise NotImplementedError

        def run(self, request):
            raise NotImplementedError

    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {"permissions": {"mode": "restricted"}}
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(
            parsed.resolved, "newsletter", "builder", integration=_UnrestrictedOnly()
        )

    assert any(i.code == "unsupported-permission-mode" for i in excinfo.value.issues)


def test_relative_rule_path_resolves_against_group_workspace(raw_config, config_paths):
    """M4: relative rule paths must resolve against workspace_path, not CWD."""
    ws = raw_config["teams"]["newsletter"]["workspace_path"]
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [{"path": "subdir", "tools": ["read"]}],
        },
    }
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    expected = Path(ws) / "subdir"
    assert len(policy.rules) == 1
    assert policy.rules[0].path == expected.resolve(strict=False)


def test_relative_rule_path_is_stable_across_cwd_changes(raw_config, config_paths, monkeypatch, tmp_path):
    """M4: the resolved path must not change when the process CWD changes."""
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [{"path": "src", "tools": ["read"]}],
        },
    }
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])

    monkeypatch.chdir(tmp_path)
    policy_a = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    policy_b = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert policy_a.rules[0].path == policy_b.rules[0].path


def test_absolute_rule_path_is_unchanged(raw_config, config_paths):
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [{"path": ws, "tools": ["read"]}],
        },
    }
    team["agents"][0]["integration"] = "copilot"

    parsed = parse_config(raw_config, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "newsletter", "builder", integration=_INTEGRATION
    )

    assert policy.rules[0].path == Path(ws).resolve(strict=False)
