from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from flowgency.integrations import BaseIntegration, REGISTRY
from flowgency.integrations.models import RuntimeCapabilities
from flowgency.configuration.store import ConfigStore
from flowgency.permissions.eligibility import may_write_workspace


class RestrictedCapableIntegration(BaseIntegration):
    name = "restricted-test"
    display_name = "Restricted Test"
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


class UnrestrictedOnlyIntegration(RestrictedCapableIntegration):
    name = "unrestricted-only-test"
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"unrestricted"}),
        path_scopable_tools=frozenset({"read", "search", "write", "shell"}),
    )


@pytest.fixture(autouse=True)
def _register_test_integrations():
    """Expose the test integrations only while this module's tests run.

    Registering at import time leaks them into the shared REGISTRY and breaks
    the integration-contract suite, so add and remove them per test instead.
    """
    added = {
        RestrictedCapableIntegration.name: RestrictedCapableIntegration(),
        UnrestrictedOnlyIntegration.name: UnrestrictedOnlyIntegration(),
    }
    REGISTRY.update(added)
    try:
        yield
    finally:
        for name in added:
            REGISTRY.pop(name, None)




def _config(tmp_path: Path, raw_config, rules, *, integration: str = "restricted-test"):
    raw = deepcopy(raw_config)
    raw["schema_version"] = 1
    workspace = raw["teams"]["newsletter"]["workspace_path"]
    raw["teams"]["newsletter"]["runtime"] = {}
    raw["teams"]["newsletter"]["permissions"] = {
        "mode": "restricted",
        "rules": [dict(r, path=r["path"].replace("<ws>", workspace)) for r in rules],
    }
    raw["teams"]["newsletter"]["agents"][0]["integration"] = integration
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_write_on_the_workspace_confers_eligibility(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["read", "write"]}])

    assert may_write_workspace(config, "newsletter", "builder") is True


def test_read_only_workspace_does_not(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["read"]}])

    assert may_write_workspace(config, "newsletter", "builder") is False


def test_write_on_a_subdirectory_does_not(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        [
            {"path": "<ws>", "tools": ["read"]},
            {"path": "<ws>/scratch", "tools": ["read", "write"]},
        ],
    )

    assert may_write_workspace(config, "newsletter", "builder") is False


def test_omitted_tools_confers_eligibility(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>"}])

    assert may_write_workspace(config, "newsletter", "builder") is True


def test_unknown_team_is_not_eligible(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["write"]}])

    assert may_write_workspace(config, "nope", "builder") is False


def test_unknown_agent_is_not_eligible(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["write"]}])

    assert may_write_workspace(config, "newsletter", "nope") is False


def test_unsupported_runtime_policy_fails_closed(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        [{"path": "<ws>", "tools": ["read", "write"]}],
        integration="unrestricted-only-test",
    )

    assert may_write_workspace(config, "newsletter", "builder") is False
