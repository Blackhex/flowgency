from pathlib import Path

import pytest

from agency.configuration import ValidationFailed, ValidationIssue


def _clone_config(raw: dict) -> dict:
    import copy

    return copy.deepcopy(raw)


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


def test_validate_config_canonical_reports_superseded_group_dispatch_agents(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": False,
        "daily_limit": 20,
    }
    canonical_raw_config["groups"]["newsletter"]["dispatch"]["agents"] = {
        "builder": [{"at": "09:00"}]
    }

    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.code == "superseded-group-dispatch-agents" for issue in issues)
    assert any(issue.field == "groups.newsletter.dispatch.agents" for issue in issues)
    assert any(
        issue.corrective_hint
        == "Move schedules into each agent's routines using the standalone migration utility."
        for issue in issues
    )


def test_parse_config_canonical_rejects_superseded_group_dispatch_agents(canonical_raw_config, canonical_paths):
    from agency.configuration.models import parse_config_canonical

    canonical_raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": False,
        "daily_limit": 20,
    }
    canonical_raw_config["groups"]["newsletter"]["dispatch"]["agents"] = {
        "builder": [{"at": "09:00"}]
    }

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.code == "superseded-group-dispatch-agents" for issue in excinfo.value.issues)


def test_accepts_supported_group_dispatch_and_routines(canonical_raw_config, canonical_paths):
    from agency.configuration.models import parse_config_canonical, validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": True,
        "daily_limit": 12,
    }

    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert not any(issue.field == "groups.newsletter.dispatch.agents" for issue in issues)
    assert parsed.groups["newsletter"].dispatch.enabled is True
    assert parsed.groups["newsletter"].dispatch.daily_limit == 12
    assert parsed.groups["newsletter"].agents["builder"].routines[0].schedule.at == "09:00"


def test_rejects_other_unknown_group_dispatch_keys(canonical_raw_config, canonical_paths):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": True,
        "daily_limit": 12,
        "owner": "ops",
    }

    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.code == "invalid-config" for issue in issues)
    assert any(issue.field == "groups.newsletter.dispatch.owner" for issue in issues)


@pytest.mark.parametrize("blueprint_value", [None, "", "   "])
def test_rejects_missing_or_blank_blueprint(canonical_raw_config, canonical_paths, blueprint_value):
    from agency.configuration.models import parse_config_canonical, validate_config_canonical

    agent = canonical_raw_config["groups"]["newsletter"]["agents"][0]
    if blueprint_value is None:
        del agent["blueprint"]
    else:
        agent["blueprint"] = blueprint_value

    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.code == "missing-blueprint" for issue in issues)
    assert any(issue.field == "blueprint" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.code == "missing-blueprint" for issue in excinfo.value.issues)


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


@pytest.mark.parametrize("routine_value", [None, "daily", ["daily"], 42])
def test_rejects_non_mapping_routine_entries(canonical_raw_config, canonical_paths, routine_value):
    from agency.configuration.models import validate_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [routine_value]

    issues = validate_config_canonical(canonical_raw_config, canonical_paths["config_path"])
    assert any(
        issue.code == "invalid-routine-entry" and issue.field == "groups.newsletter.agents[0].routines[0]"
        for issue in issues
    )


@pytest.mark.parametrize("routine_value", [None, "daily", ["daily"], 42])
def test_parse_config_canonical_rejects_non_mapping_routine_entries(canonical_raw_config, canonical_paths, routine_value):
    from agency.configuration.models import parse_config_canonical

    canonical_raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [routine_value]

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(
        issue.code == "invalid-routine-entry" and issue.field == "groups.newsletter.agents[0].routines[0]"
        for issue in excinfo.value.issues
    )


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


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"].append(
                {
                    "name": "builder",
                    "blueprint": "other-blueprint",
                    "integration": "claude-code",
                }
            ),
            id="duplicate-agent-name",
        ),
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"][0].update({"name": "bad name"}),
            id="invalid-agent-identifier",
        ),
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"][0]["default_memory"].update(
                {"scope": "channel", "channel": "missing"}
            ),
            id="missing-channel-reference",
        ),
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"][0].update(
                {"runtime": {"tools": {"mode": "allowlist", "names": [""]}}}
            ),
            id="blank-allowlist-name",
        ),
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"][0].update(
                {"runtime": {"sandbox": {"mode": "unrestricted", "additional_roots": ["tmp"]}}}
            ),
            id="sandbox-contradiction",
        ),
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"][0]["routines"].append(
                {
                    "id": "daily-review",
                    "skill": "daily-review-2",
                    "schedule": {"every": "6h"},
                }
            ),
            id="duplicate-routine-name",
        ),
    ],
)
def test_parse_and_validate_reject_same_semantic_invalid_configs(canonical_raw_config, canonical_paths, mutator):
    from agency.configuration.models import parse_config_canonical, validate_config_canonical

    candidate = _clone_config(canonical_raw_config)
    agent = candidate["groups"]["newsletter"]["agents"][0]
    agent.setdefault("default_memory", {"scope": "agent"})
    mutator(candidate)

    issues = validate_config_canonical(candidate, canonical_paths["config_path"])
    assert issues

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(candidate, canonical_paths["config_path"])

    assert excinfo.value.issues == issues


def test_parse_and_validate_share_same_valid_result(canonical_raw_config, canonical_paths):
    from agency.configuration.models import parse_config_canonical, validate_config_canonical

    candidate = _clone_config(canonical_raw_config)

    issues = validate_config_canonical(candidate, canonical_paths["config_path"])
    parsed = parse_config_canonical(candidate, canonical_paths["config_path"])

    assert issues == ()
    assert parsed.schema_version == 2
    assert parsed.groups["newsletter"].agents["builder"].routines[0].id == "daily-review"


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_field"),
    [
        ("agency", [], "agency"),
        ("memory", [], "memory"),
        ("groups", [], "groups"),
    ],
)
def test_parse_config_canonical_rejects_malformed_top_level_mappings(
    canonical_raw_config, canonical_paths, field_name, bad_value, expected_field
):
    from agency.configuration.models import parse_config_canonical

    canonical_raw_config[field_name] = bad_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("group_value", "expected_field"),
    [
        ([], "groups.newsletter"),
        ("newsletter", "groups.newsletter"),
    ],
)
def test_parse_config_canonical_rejects_malformed_group_records(canonical_raw_config, canonical_paths, group_value, expected_field):
    from agency.configuration.models import parse_config_canonical

    canonical_raw_config["groups"]["newsletter"] = group_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("path", "bad_value", "expected_field"),
    [
        (["memory", "channels"], [], "memory.channels"),
        (["memory", "channels", "support"], [], "memory.channels.support"),
        (["groups", "newsletter", "runtime"], [], "groups.newsletter.runtime"),
        (["groups", "newsletter", "runtime", "sandbox"], [], "groups.newsletter.runtime.sandbox"),
        (["groups", "newsletter", "runtime", "tools"], [], "groups.newsletter.runtime.tools"),
        (["groups", "newsletter", "dispatch"], [], "groups.newsletter.dispatch"),
        (["groups", "newsletter", "workspaces"], {}, "groups.newsletter.workspaces"),
        (["groups", "newsletter", "agents"], {}, "groups.newsletter.agents"),
        (["groups", "newsletter", "agents", 0, "identity"], [], "groups.newsletter.agents[0].identity"),
        (["groups", "newsletter", "agents", 0, "capabilities"], [], "groups.newsletter.agents[0].capabilities"),
        (["groups", "newsletter", "agents", 0, "runtime"], [], "groups.newsletter.agents[0].runtime"),
        (["groups", "newsletter", "agents", 0, "runtime", "sandbox"], [], "groups.newsletter.agents[0].runtime.sandbox"),
        (["groups", "newsletter", "agents", 0, "runtime", "tools"], [], "groups.newsletter.agents[0].runtime.tools"),
        (["groups", "newsletter", "agents", 0, "default_memory"], [], "groups.newsletter.agents[0].default_memory"),
        (["groups", "newsletter", "agents", 0, "routines"], {}, "groups.newsletter.agents[0].routines"),
        (["groups", "newsletter", "agents", 0, "routines", 0, "schedule"], [], "groups.newsletter.agents[0].routines[0].schedule"),
        (["groups", "newsletter", "agents", 0, "routines", 0, "memory"], [], "groups.newsletter.agents[0].routines[0].memory"),
    ],
)
def test_parse_config_canonical_rejects_malformed_nested_shapes(canonical_raw_config, canonical_paths, path, bad_value, expected_field):
    from agency.configuration.models import parse_config_canonical

    target = canonical_raw_config
    for segment in path[:-1]:
        if isinstance(segment, str) and segment not in target:
            next_segment = path[path.index(segment) + 1]
            target[segment] = [] if isinstance(next_segment, int) else {}
        target = target[segment]
    target[path[-1]] = bad_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)
