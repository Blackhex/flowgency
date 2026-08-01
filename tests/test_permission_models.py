from __future__ import annotations

from pathlib import Path

import pytest

from agency.configuration.models import (
    CONFIG_SCHEMA_VERSION,
    PermissionRule,
    RuntimePermissions,
)


def test_schema_version_is_five():
    assert CONFIG_SCHEMA_VERSION == 5


def test_omitted_tools_means_every_tool():
    assert PermissionRule(path=Path("/a")).tools is None


def test_empty_tools_is_distinct_from_omitted():
    assert PermissionRule(path=Path("/a"), tools=()).tools == ()


def test_rule_without_a_path_is_valid():
    assert PermissionRule(tools=("fetch",)).path is None


def test_unknown_rule_key_is_rejected():
    with pytest.raises(Exception):
        PermissionRule(path=Path("/a"), tool=["read"])


def test_permissions_default_to_unrestricted_with_no_rules():
    permissions = RuntimePermissions()

    assert permissions.mode == "unrestricted"
    assert permissions.rules == ()


def test_permissions_reject_an_unknown_mode():
    with pytest.raises(Exception):
        RuntimePermissions(mode="sandboxed")


def test_superseded_models_are_gone():
    import agency.configuration.models as models

    for name in (
        "GroupRuntimeSandbox",
        "AgentRuntimeSandbox",
        "RuntimeTools",
        "AgentCapabilities",
    ):
        assert not hasattr(models, name), f"{name} should have been removed"
