"""Tests for the permission-based executor eligibility model.

These replace the former capabilities.write / writable_roots / writes_narrowed
tests.  The new model derives executor eligibility from whether the agent's
effective policy grants the 'write' tool on the group's workspace_path.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.issues import ValidationFailed
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    ResolvedPermissionRule,
    RuntimeCapabilities,
)
from agency.jobs.models import RuntimePolicySnapshot
from agency.permissions.eligibility import may_execute_decisions


# ── Integration fixtures ─────────────────────────────────────────────────────


class PermissiveIntegration(BaseIntegration):
    """Accepts both modes and any path-tool scoping."""

    name = "permissive"
    display_name = "Permissive"
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


class RestrictedOnlyIntegration(PermissiveIntegration):
    """Only supports unrestricted mode."""

    name = "restricted-only"
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"unrestricted"}),
    )


# ── Config helpers ───────────────────────────────────────────────────────────


def _config_with_rules(
    tmp_path: Path,
    raw_config,
    *,
    team_rules=None,
    agent_rules=None,
    mode="unrestricted",
):
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": mode,
            "rules": team_rules or [],
        },
    }
    agent = team["agents"][0]
    if agent_rules is not None:
        agent["runtime"] = {"permissions": {"rules": agent_rules}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


# ── Eligibility tests ────────────────────────────────────────────────────────


def test_agent_with_write_on_workspace_is_eligible(tmp_path, raw_config):
    """An agent whose rules grant write on the workspace_path may execute."""
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    config = _config_with_rules(
        tmp_path,
        raw_config,
        agent_rules=[{"path": ws, "tools": ["read", "write"]}],
    )
    assert may_execute_decisions(config, "newsletter", "builder") is True


def test_agent_without_write_on_workspace_is_ineligible(tmp_path, raw_config):
    """An agent whose rules omit write on the workspace_path is ineligible."""
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    config = _config_with_rules(
        tmp_path,
        raw_config,
        agent_rules=[{"path": ws, "tools": ["read", "search"]}],
    )
    assert may_execute_decisions(config, "newsletter", "builder") is False


def test_agent_with_no_workspace_rule_is_ineligible(tmp_path, raw_config):
    """An agent with no rule matching the workspace_path is ineligible."""
    config = _config_with_rules(
        tmp_path,
        raw_config,
        agent_rules=[{"path": str(tmp_path / "somewhere-else"), "tools": ["write"]}],
    )
    assert may_execute_decisions(config, "newsletter", "builder") is False


def test_agent_with_null_tools_on_workspace_is_eligible(tmp_path, raw_config):
    """tools=None means all tools including write - eligible."""
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    config = _config_with_rules(
        tmp_path,
        raw_config,
        agent_rules=[{"path": ws, "tools": None}],
    )
    assert may_execute_decisions(config, "newsletter", "builder") is True


# ── Integration validation tests ─────────────────────────────────────────────


def test_integration_rejects_unsupported_mode(tmp_path, raw_config):
    """An integration that doesn't support restricted mode rejects it."""
    config = _config_with_rules(tmp_path, raw_config, mode="restricted")

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(
            config, "newsletter", "builder", integration=RestrictedOnlyIntegration()
        )

    codes = [issue.code for issue in excinfo.value.issues]
    assert "unsupported-permission-mode" in codes


def test_permissive_integration_accepts_restricted_mode(tmp_path, raw_config):
    """An integration that supports both modes validates successfully."""
    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    config = _config_with_rules(
        tmp_path,
        raw_config,
        mode="restricted",
        agent_rules=[{"path": ws, "tools": ["read", "write"]}],
    )
    policy = resolve_effective_policy(
        config, "newsletter", "builder", integration=PermissiveIntegration()
    )
    assert policy.mode == "restricted"


# ── Snapshot round-trip tests ────────────────────────────────────────────────


def test_snapshot_round_trips_rules():
    """Snapshot serializes and restores rules faithfully."""
    original = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=Path("/ws"), tools=("read", "write")),
            ResolvedPermissionRule(path=None, tools=("read",)),
        ),
    )

    snapshot = RuntimePolicySnapshot.from_effective_policy(original)
    restored = snapshot.to_effective_policy()

    assert restored.mode == "restricted"
    assert len(restored.rules) == 2
    assert restored.rules[0].tools == ("read", "write")
    assert restored.rules[1].path is None
    assert restored.rules[1].tools == ("read",)


def test_snapshot_dict_does_not_contain_deprecated_fields():
    """Serialized payload has no writes_narrowed or writable_roots."""
    original = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=Path("/ws"), tools=("read",)),),
    )
    payload = RuntimePolicySnapshot.from_effective_policy(original).to_dict()

    assert "writes_narrowed" not in payload
    assert "writable_roots" not in payload
    assert payload["mode"] == "restricted"
    assert len(payload["rules"]) == 1


def test_a_job_spec_persisted_before_the_write_boundary_still_loads(tmp_path):
    """Pre-upgrade job payloads must keep their shape, digest, and meaning."""
    from agency.jobs.models import JobRecord
    from test_job_execution import queued_job

    _, spec = queued_job(tmp_path)
    payload = JobRecord.from_spec(spec).to_dict()

    assert "writable_roots" not in payload["spec"]["runtime_policy"]
    assert "writes_narrowed" not in payload["spec"]["runtime_policy"]
    assert "writable_agents" not in payload["spec"]

    restored = JobRecord.from_dict(payload)

    assert restored.spec.runtime_policy.to_effective_policy().mode == "unrestricted"
    assert restored.spec.writable_agents is None


