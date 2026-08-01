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
    ResolvedToolPolicy,
    RuntimeCapabilities,
)
from agency.jobs.models import RuntimePolicySnapshot


class EnforcingIntegration(BaseIntegration):
    name = "enforcing"
    display_name = "Enforcing"
    supports_execution = True
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"all", "allowlist"}),
        enforces_write_boundary=True,
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


class NonEnforcingIntegration(EnforcingIntegration):
    name = "nonenforcing"
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"all", "allowlist"}),
    )


def policy(*, writable, roots=(Path("/ws").resolve(),), mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=60,
        sandbox_mode=mode,
        sandbox_roots=roots,
        tools=ResolvedToolPolicy(mode="all", names=()),
        writable_roots=roots if writable else (),
        writes_narrowed=not writable,
    )


def _config(tmp_path: Path, raw_config, *, write: bool):
    raw = deepcopy(raw_config)
    raw["groups"]["newsletter"]["agents"][0]["capabilities"] = {"write": write}
    raw["groups"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": []}
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_capabilities_write_true_makes_the_workspace_writable(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, write=True)

    resolved = resolve_effective_policy(
        config, "newsletter", "builder", integration=EnforcingIntegration()
    )

    assert resolved.writable_roots == resolved.sandbox_roots
    assert resolved.writable_roots != ()


def test_capabilities_write_false_yields_no_writable_roots(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, write=False)

    resolved = resolve_effective_policy(
        config, "newsletter", "builder", integration=EnforcingIntegration()
    )

    assert resolved.writable_roots == ()
    assert resolved.sandbox_roots != ()


def test_read_only_policy_narrows_writes():
    assert policy(writable=False).narrows_writes is True


def test_writable_policy_does_not_narrow_writes():
    assert policy(writable=True).narrows_writes is False


def test_unrestricted_read_only_policy_still_narrows_writes():
    unrestricted = EffectiveRuntimePolicy(
        timeout=60,
        sandbox_mode="unrestricted",
        sandbox_roots=(),
        tools=ResolvedToolPolicy(mode="all", names=()),
        writable_roots=(),
        writes_narrowed=True,
    )

    assert unrestricted.narrows_writes is True


def test_non_enforcing_integration_rejects_a_narrowed_policy():
    issues = NonEnforcingIntegration().validate_runtime_policy(policy(writable=False))

    assert [issue.code for issue in issues] == ["unsupported-write-boundary"]
    message = issues[0].message
    assert "nonenforcing" in message
    assert "capabilities.write" in message


def test_non_enforcing_integration_accepts_a_writable_policy():
    assert NonEnforcingIntegration().validate_runtime_policy(policy(writable=True)) == ()


def test_enforcing_integration_accepts_a_narrowed_policy():
    assert EnforcingIntegration().validate_runtime_policy(policy(writable=False)) == ()


def test_resolve_rejects_a_read_only_agent_on_a_non_enforcing_integration(
    tmp_path, raw_config
):
    config = _config(tmp_path, raw_config, write=False)

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(
            config, "newsletter", "builder", integration=NonEnforcingIntegration()
        )

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-write-boundary"
    ]


def test_snapshot_round_trips_writable_roots():
    original = policy(writable=True)

    restored = RuntimePolicySnapshot.from_effective_policy(original).to_effective_policy()

    assert restored.mode == "restricted"
    assert restored.narrows_writes is False


def test_snapshot_round_trips_a_narrowed_policy():
    original = policy(writable=False)

    restored = RuntimePolicySnapshot.from_effective_policy(original).to_effective_policy()

    assert restored.mode == "restricted"
    assert restored.narrows_writes is False


def test_a_narrowed_policy_serializes_the_flag():
    payload = RuntimePolicySnapshot.from_effective_policy(policy(writable=False)).to_dict()

    assert "writes_narrowed" not in payload
    assert payload["mode"] == "restricted"


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

    assert restored.spec.runtime_policy.to_effective_policy().narrows_writes is False
    assert restored.spec.writable_agents is None
