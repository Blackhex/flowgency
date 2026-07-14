from pathlib import Path

import pytest

from agency.configuration import ValidationFailed, ValidationIssue


def test_canonical_defaults_are_explicit(canonical_raw_config, canonical_paths):
    from agency.configuration.models import parse_config_canonical

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    group = parsed.groups["newsletter"]

    assert parsed.agency.dispatch.interval == 15
    assert group.runtime.timeout == 1800
    assert group.runtime.sandbox.mode == "unrestricted"
    assert group.runtime.tools.mode == "all"
    assert group.dispatch.enabled is False
    assert group.dispatch.daily_limit == 20


def test_rejects_routine_default_without_routine_context(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "routine"}
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "invalid-memory-scope" for issue in issues)


def test_rejects_superseded_schema_version(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["schema_version"] = 1
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "invalid-schema-version" for issue in issues)


def test_requires_control_plane_paths(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    del canonical_raw_config["groups"]["newsletter"]["path"]
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "missing-group-path" for issue in issues)


def test_parse_config_canonical_raises_validation_failed_for_missing_group_path_with_additional_roots(
    canonical_raw_config, canonical_paths
):
    from agency.configuration.models import parse_config_canonical

    del canonical_raw_config["groups"]["newsletter"]["path"]
    canonical_raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"additional_roots": ["shared"]}
    }

    with pytest.raises(ValidationFailed):
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])


@pytest.mark.parametrize(
    ("agent_entry", "expected_code", "expected_field"),
    [
        (None, "invalid-agent-entry", "agents[0]"),
        ("builder", "invalid-agent-entry", "agents[0]"),
        (["builder"], "invalid-agent-entry", "agents[0]"),
        ({}, "missing-agent-name", "agents[0].name"),
    ],
)
def test_parse_config_canonical_rejects_malformed_agent_entries(
    canonical_raw_config, canonical_paths, agent_entry, expected_code, expected_field
):
    from agency.configuration.models import parse_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"] = [agent_entry]

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == expected_field for issue in excinfo.value.issues)


def test_rejects_duplicate_agent_names(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"].append(
        {
            "name": "builder",
            "blueprint": "builder-blueprint",
            "integration": "claude-code",
        }
    )
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "duplicate-agent-name" for issue in issues)


def test_rejects_duplicate_routine_names(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    agent = canonical_raw_config["groups"]["newsletter"]["agents"][0]
    agent["routines"] = [
        {"id": "daily"},
        {"id": "daily"},
    ]
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "duplicate-routine-name" for issue in issues)


def test_rejects_missing_explicit_integration(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    del canonical_raw_config["groups"]["newsletter"]["agents"][0]["integration"]
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "missing-explicit-integration" for issue in issues)


def test_rejects_channel_memory_reference_without_channel(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "channel"}
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "missing-memory-channel" for issue in issues)


def test_rejects_undeclared_channel_memory_reference(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": "missing",
    }
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "missing-memory-channel" for issue in issues)


def test_accepts_declared_channel_memory_reference(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["memory"]["channels"] = {"ops": {"display_name": "Ops"}}
    canonical_raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": "ops",
    }
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert not any(issue.code == "missing-memory-channel" for issue in issues)


def test_rejects_schedule_without_one_of(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [
        {"id": "daily", "skill": "daily", "schedule": {}},
    ]
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "invalid-dispatch-rule" for issue in issues)


@pytest.mark.parametrize("schedule_value", ["daily", ["at", "09:00"], 42])
def test_rejects_non_mapping_schedule_values(canonical_raw_config, canonical_paths, schedule_value):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [
        {"id": "daily", "skill": "daily", "schedule": schedule_value},
    ]

    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "invalid-dispatch-rule" for issue in issues)


def test_rejects_empty_allowlist(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "tools": {"mode": "allowlist", "names": []}
    }
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "empty-allowlist" for issue in issues)


@pytest.mark.parametrize("names, expected_field", [([""], "runtime.tools.names[0]"), (["   "], "runtime.tools.names[0]")])
def test_rejects_blank_allowlist_names(canonical_raw_config, canonical_paths, names, expected_field):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "tools": {"mode": "allowlist", "names": names}
    }
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "invalid-allowlist-name" and issue.field == expected_field for issue in issues)
    assert not any(issue.code == "empty-allowlist" for issue in issues)


def test_rejects_unrestricted_with_additions(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "unrestricted", "additional_roots": ["/tmp"]}
    }
    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(issue.code == "sandbox-contradiction" for issue in issues)


def test_preserves_supported_workspace_fields(canonical_raw_config, canonical_paths):
    from agency.configuration.models import parse_config_canonical

    canonical_raw_config["groups"]["newsletter"]["workspaces"][0]["extra"] = "kept"
    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert parsed.groups["newsletter"].workspaces[0].extra == "kept"
