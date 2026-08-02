from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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
    with pytest.raises(ValidationError):
        PermissionRule(path=Path("/a"), tool=["read"])


def test_permissions_default_to_unrestricted_with_no_rules():
    permissions = RuntimePermissions()

    assert permissions.mode == "unrestricted"
    assert permissions.rules == ()


def test_permissions_reject_an_unknown_mode():
    with pytest.raises(ValidationError):
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

    assert not hasattr(models, "ToolMode"), "ToolMode should have been removed"
    assert not hasattr(models, "SandboxMode"), "SandboxMode should have been removed"
    assert "capabilities" not in models.AgentInstance.model_fields, "AgentInstance.capabilities should be removed"


def test_schema_version_four_is_rejected(config_paths):
    from agency.configuration import ValidationFailed
    from agency.configuration.models import parse_config

    raw = {
        "schema_version": 5,
        "agency": {
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
            "prompt_store": str(config_paths["prompt_store"]),
        },
        "groups": {},
    }

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw, config_paths["config_path"])

    assert any(issue.code == "unsupported-schema-version" for issue in excinfo.value.issues)


def test_schema_version_five_is_accepted(config_paths):
    from agency.configuration.models import validate_config

    raw = {
        "schema_version": 5,
        "agency": {
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
            "prompt_store": str(config_paths["prompt_store"]),
        },
        "groups": {},
    }

    issues = validate_config(raw, config_paths["config_path"])

    assert not any(issue.code == "unsupported-schema-version" for issue in issues)


def test_unsupported_schema_version_hint_mentions_migrate(config_paths):
    from agency.configuration import ValidationFailed
    from agency.configuration.models import parse_config

    raw = {
        "schema_version": 5,
        "agency": {
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
            "prompt_store": str(config_paths["prompt_store"]),
        },
        "groups": {},
    }

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw, config_paths["config_path"])

    issue = next(issue for issue in excinfo.value.issues if issue.code == "unsupported-schema-version")
    assert "config migrate" in issue.corrective_hint
