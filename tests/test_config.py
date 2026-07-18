from pathlib import Path

import pytest

from agency.configuration import ValidationFailed, ValidationIssue


def _clone_config(raw: dict) -> dict:
    import copy

    return copy.deepcopy(raw)


def test_parse_config_accepts_canonical_root(raw_config, config_paths):
    from agency.configuration.models import parse_config

    parsed = parse_config(raw_config, config_paths["config_path"])

    assert parsed.raw == raw_config
    assert parsed.resolved.agency.title == "Agency"


def test_validate_config_rejects_unknown_root_key(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["schema_version"] = 2
    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.field == "schema_version" for issue in issues)


def test_canonical_defaults_are_explicit(raw_config, config_paths):
    from agency.configuration.models import parse_config

    parsed = parse_config(raw_config, config_paths["config_path"])
    group = parsed.groups["newsletter"]

    assert parsed.agency.dispatch.interval == 15
    assert group.runtime.timeout == 1800
    assert group.runtime.sandbox.mode == "unrestricted"
    assert group.runtime.tools.mode == "all"
    assert group.dispatch.enabled is False
    assert group.dispatch.daily_limit == 20


def test_rejects_routine_default_without_routine_context(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "routine"}
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-memory-scope" for issue in issues)


def test_validate_config_reports_superseded_group_dispatch_agents(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": False,
        "daily_limit": 20,
    }
    raw_config["groups"]["newsletter"]["dispatch"]["agents"] = {
        "builder": [{"at": "09:00"}]
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "superseded-group-dispatch-agents" for issue in issues)
    assert any(issue.field == "groups.newsletter.dispatch.agents" for issue in issues)
    assert any(
        issue.corrective_hint
        == "Move schedules into each agent's routines using the standalone migration utility."
        for issue in issues
    )


def test_parse_config_rejects_superseded_group_dispatch_agents(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": False,
        "daily_limit": 20,
    }
    raw_config["groups"]["newsletter"]["dispatch"]["agents"] = {
        "builder": [{"at": "09:00"}]
    }

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "superseded-group-dispatch-agents" for issue in excinfo.value.issues)


def test_accepts_supported_group_dispatch_and_routines(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": True,
        "daily_limit": 12,
    }

    issues = validate_config(raw_config, config_paths["config_path"])
    parsed = parse_config(raw_config, config_paths["config_path"])

    assert not any(issue.field == "groups.newsletter.dispatch.agents" for issue in issues)
    assert parsed.groups["newsletter"].dispatch.enabled is True
    assert parsed.groups["newsletter"].dispatch.daily_limit == 12
    assert parsed.groups["newsletter"].agents["builder"].routines[0].schedule.at == "09:00"


def test_routine_enabled_is_typed_and_defaults_true(raw_config, config_paths):
    from agency.configuration.models import parse_config

    routine = raw_config["groups"]["newsletter"]["agents"][0]["routines"][0]
    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.groups["newsletter"].agents["builder"].routines[0].enabled is True

    routine["enabled"] = False
    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.groups["newsletter"].agents["builder"].routines[0].enabled is False


def test_rejects_other_unknown_group_dispatch_keys(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["dispatch"] = {
        "enabled": True,
        "daily_limit": 12,
        "owner": "ops",
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "invalid-config" for issue in issues)
    assert any(issue.field == "groups.newsletter.dispatch.owner" for issue in issues)


@pytest.mark.parametrize("blueprint_value", [None, "", "   "])
def test_rejects_missing_or_blank_blueprint(raw_config, config_paths, blueprint_value):
    from agency.configuration.models import parse_config, validate_config

    agent = raw_config["groups"]["newsletter"]["agents"][0]
    if blueprint_value is None:
        del agent["blueprint"]
    else:
        agent["blueprint"] = blueprint_value

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "missing-blueprint" for issue in issues)
    assert any(issue.field == "blueprint" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "missing-blueprint" for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("blueprint_value", "expected_code"),
    [
        ("bad blueprint", "invalid-blueprint-name"),
        ("Blueprint", "invalid-blueprint-name"),
        ("builder-blueprint", None),
    ],
)
def test_validates_blueprint_identifiers(raw_config, config_paths, blueprint_value, expected_code):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["blueprint"] = blueprint_value

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code == "invalid-blueprint-name" for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert parsed.groups["newsletter"].agents["builder"].blueprint == blueprint_value
        return

    assert any(issue.code == expected_code and issue.field == "blueprint" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == "blueprint" for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("default_group", "expected_code"),
    [
        ("bad group", "invalid-group-name"),
        ("missing-group", "missing-default-group"),
        ("newsletter-team", None),
    ],
)
def test_validates_default_group_identifier_and_reference(
    raw_config, config_paths, default_group, expected_code
):
    from agency.configuration.models import parse_config, validate_config

    if default_group == "newsletter-team":
        group_config = raw_config["groups"].pop("newsletter")
        raw_config["groups"][default_group] = group_config
    raw_config["agency"]["default_group"] = default_group

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code in {"invalid-group-name", "missing-default-group"} for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert parsed.agency.default_group == default_group
        return

    assert any(issue.code == expected_code and issue.field == "agency.default_group" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(
        issue.code == expected_code and issue.field == "agency.default_group" for issue in excinfo.value.issues
    )


def test_allows_omitted_default_group(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["agency"]["default_group"] = ""

    issues = validate_config(raw_config, config_paths["config_path"])
    parsed = parse_config(raw_config, config_paths["config_path"])

    assert not any(issue.field == "agency.default_group" for issue in issues)
    assert parsed.agency.default_group == ""


def test_validate_config_rejects_unknown_root_key_superseded(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["schema_version"] = 1
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.field == "schema_version" for issue in issues)


def test_requires_control_plane_paths(raw_config, config_paths):
    from agency.configuration.models import validate_config

    del raw_config["groups"]["newsletter"]["path"]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-group-path" for issue in issues)


def test_parse_config_raises_validation_failed_for_missing_group_path_with_additional_roots(
    raw_config, config_paths
):
    from agency.configuration.models import parse_config

    del raw_config["groups"]["newsletter"]["path"]
    raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"additional_roots": ["shared"]}
    }

    with pytest.raises(ValidationFailed):
        parse_config(raw_config, config_paths["config_path"])


@pytest.mark.parametrize(
    ("agent_entry", "expected_code", "expected_field"),
    [
        (None, "invalid-agent-entry", "agents[0]"),
        ("builder", "invalid-agent-entry", "agents[0]"),
        (["builder"], "invalid-agent-entry", "agents[0]"),
        ({}, "missing-agent-name", "agents[0].name"),
    ],
)
def test_parse_config_rejects_malformed_agent_entries(
    raw_config, config_paths, agent_entry, expected_code, expected_field
):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"]["agents"] = [agent_entry]

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == expected_field for issue in excinfo.value.issues)


def test_rejects_duplicate_agent_names(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"].append(
        {
            "name": "builder",
            "blueprint": "builder-blueprint",
            "integration": "claude-code",
        }
    )
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "duplicate-agent-name" for issue in issues)


@pytest.mark.parametrize(
    ("group_key", "expected_code"),
    [
        ("bad group", "invalid-group-name"),
        ("Newsletter", "invalid-group-name"),
        ("newsletter-team", None),
    ],
)
def test_validates_group_keys_as_stable_identifiers(raw_config, config_paths, group_key, expected_code):
    from agency.configuration.models import parse_config, validate_config

    group_config = raw_config["groups"].pop("newsletter")
    raw_config["groups"][group_key] = group_config
    if raw_config["agency"].get("default_group") == "newsletter":
        raw_config["agency"]["default_group"] = group_key

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code == "invalid-group-name" for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert group_key in parsed.groups
        return

    assert any(issue.code == expected_code and issue.field == "group" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == "group" for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("channel_key", "expected_code"),
    [
        ("ops channel", "invalid-channel-name"),
        ("Ops", "invalid-channel-name"),
        ("ops-channel", None),
    ],
)
def test_validates_memory_channel_keys_as_stable_identifiers(raw_config, config_paths, channel_key, expected_code):
    from agency.configuration.models import parse_config, validate_config

    raw_config["memory"]["channels"] = {channel_key: {"display_name": "Ops"}}
    raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": channel_key,
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code == "invalid-channel-name" for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert channel_key in parsed.memory.channels
        return

    assert any(issue.code == expected_code and issue.field == "channel" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == "channel" for issue in excinfo.value.issues)


def test_rejects_duplicate_routine_names(raw_config, config_paths):
    from agency.configuration.models import validate_config

    agent = raw_config["groups"]["newsletter"]["agents"][0]
    agent["routines"] = [
        {"id": "daily"},
        {"id": "daily"},
    ]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "duplicate-routine-name" for issue in issues)


def test_rejects_missing_explicit_integration(raw_config, config_paths):
    from agency.configuration.models import validate_config

    del raw_config["groups"]["newsletter"]["agents"][0]["integration"]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-explicit-integration" for issue in issues)


@pytest.mark.parametrize("default_integration_value", [None, "", "   "])
def test_rejects_missing_or_blank_group_default_integration(
    raw_config, config_paths, default_integration_value
):
    from agency.configuration.models import parse_config, validate_config

    if default_integration_value is None:
        del raw_config["groups"]["newsletter"]["default_integration"]
    else:
        raw_config["groups"]["newsletter"]["default_integration"] = default_integration_value

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "missing-default-integration" for issue in issues)
    assert any(issue.field == "default_integration" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "missing-default-integration" for issue in excinfo.value.issues)


def test_rejects_invalid_group_allowlist(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["runtime"] = {
        "tools": {"mode": "allowlist", "names": ["", "  ", "ops"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "invalid-allowlist-name" for issue in issues)
    assert any(issue.field == "runtime.tools.names[0]" for issue in issues)
    assert not any(issue.code == "empty-allowlist" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert excinfo.value.issues == issues


def test_rejects_group_additional_roots(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["shared"], "additional_roots": ["tmp"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "invalid-config" and issue.field == "groups.newsletter.runtime.sandbox.additional_roots" for issue in issues)


def test_rejects_agent_roots(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["tmp"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "invalid-config" and issue.field == "groups.newsletter.agents.builder.runtime.sandbox.roots" for issue in issues)


def test_validates_group_sandbox_semantics(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["runtime"] = {"sandbox": {"mode": "unrestricted", "roots": ["tmp"]}}

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "sandbox-contradiction" and issue.field == "runtime.sandbox.roots" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "sandbox-contradiction" and issue.field == "runtime.sandbox.roots" for issue in excinfo.value.issues)


def test_accepts_restricted_group_roots(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["shared"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])
    assert not any(issue.code == "invalid-field-shape" for issue in issues)

    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.groups["newsletter"].runtime.sandbox.mode == "restricted"
    assert parsed.groups["newsletter"].runtime.sandbox.roots[0].name == "shared"


def test_accepts_agent_additions(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "restricted", "additional_roots": ["tmp"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])
    assert not any(issue.code == "invalid-field-shape" for issue in issues)

    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.groups["newsletter"].agents["builder"].runtime.sandbox.additional_roots[0].name == "tmp"


def test_rejects_channel_memory_reference_without_channel(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "channel"}
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-memory-channel" for issue in issues)


def test_rejects_undeclared_channel_memory_reference(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": "missing",
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-memory-channel" for issue in issues)


def test_accepts_declared_channel_memory_reference(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["memory"]["channels"] = {"ops": {"display_name": "Ops"}}
    raw_config["groups"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": "ops",
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert not any(issue.code == "missing-memory-channel" for issue in issues)


@pytest.mark.parametrize("scope_path", [
    ["groups", "newsletter", "agents", 0, "default_memory"],
    ["groups", "newsletter", "agents", 0, "routines", 0, "memory"],
])
@pytest.mark.parametrize("channel_value", ["support", "   "])
def test_rejects_non_channel_memory_selectors_with_channel(raw_config, config_paths, scope_path, channel_value):
    from agency.configuration.models import parse_config, validate_config

    target = raw_config
    for segment in scope_path[:-1]:
        if isinstance(segment, str) and segment not in target:
            next_segment = scope_path[scope_path.index(segment) + 1]
            target[segment] = [] if isinstance(next_segment, int) else {}
        target = target[segment]
    target[scope_path[-1]] = {"scope": "agent", "channel": channel_value}

    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-memory-selector-shape" for issue in issues)
    assert any(issue.field.endswith(".channel") for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "invalid-memory-selector-shape" for issue in excinfo.value.issues)


def test_rejects_schedule_without_one_of(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [
        {"id": "daily", "skill": "daily", "schedule": {}},
    ]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-dispatch-rule" for issue in issues)


@pytest.mark.parametrize("schedule_value", ["daily", ["at", "09:00"], 42])
def test_rejects_non_mapping_schedule_values(raw_config, config_paths, schedule_value):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [
        {"id": "daily", "skill": "daily", "schedule": schedule_value},
    ]

    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-dispatch-rule" for issue in issues)


@pytest.mark.parametrize("routine_value", [None, "daily", ["daily"], 42])
def test_rejects_non_mapping_routine_entries(raw_config, config_paths, routine_value):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [routine_value]

    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(
        issue.code == "invalid-routine-entry" and issue.field == "groups.newsletter.agents[0].routines[0]"
        for issue in issues
    )


@pytest.mark.parametrize("routine_value", [None, "daily", ["daily"], 42])
def test_parse_config_rejects_non_mapping_routine_entries(raw_config, config_paths, routine_value):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"]["agents"][0]["routines"] = [routine_value]

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(
        issue.code == "invalid-routine-entry" and issue.field == "groups.newsletter.agents[0].routines[0]"
        for issue in excinfo.value.issues
    )


def test_rejects_empty_allowlist(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "tools": {"mode": "allowlist", "names": []}
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "empty-allowlist" for issue in issues)


@pytest.mark.parametrize("names, expected_field", [([""], "runtime.tools.names[0]"), (["   "], "runtime.tools.names[0]")])
def test_rejects_blank_allowlist_names(raw_config, config_paths, names, expected_field):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "tools": {"mode": "allowlist", "names": names}
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-allowlist-name" and issue.field == expected_field for issue in issues)
    assert any(issue.code == "empty-allowlist" for issue in issues)


def test_rejects_unrestricted_with_additions(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "unrestricted", "additional_roots": ["/tmp"]}
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "sandbox-contradiction" for issue in issues)


def test_parse_validate_parity_for_sandbox_ownership(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    candidate = _clone_config(raw_config)
    candidate["groups"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["shared"], "additional_roots": ["tmp"]}
    }
    candidate["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["tmp"]}
    }

    issues = validate_config(candidate, config_paths["config_path"])

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(candidate, config_paths["config_path"])

    assert excinfo.value.issues == issues


def test_preserves_supported_workspace_fields(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"]["workspaces"][0]["extra"] = "kept"
    parsed = parse_config(raw_config, config_paths["config_path"])
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
            lambda raw: raw["groups"].update({"bad group": raw["groups"].pop("newsletter")}),
            id="invalid-group-identifier",
        ),
        pytest.param(
            lambda raw: raw["memory"].update({"channels": {"bad channel": {"display_name": "Ops"}}}),
            id="invalid-channel-identifier",
        ),
        pytest.param(
            lambda raw: raw["groups"]["newsletter"]["agents"][0].update({"blueprint": "bad blueprint"}),
            id="invalid-blueprint-identifier",
        ),
        pytest.param(
            lambda raw: raw["agency"].update({"default_group": "missing-group"}),
            id="missing-default-group-reference",
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
def test_parse_and_validate_reject_same_semantic_invalid_configs(raw_config, config_paths, mutator):
    from agency.configuration.models import parse_config, validate_config

    candidate = _clone_config(raw_config)
    agent = candidate["groups"]["newsletter"]["agents"][0]
    agent.setdefault("default_memory", {"scope": "agent"})
    mutator(candidate)

    issues = validate_config(candidate, config_paths["config_path"])
    assert issues

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(candidate, config_paths["config_path"])

    assert excinfo.value.issues == issues


def test_parse_and_validate_share_same_valid_result(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    candidate = _clone_config(raw_config)

    issues = validate_config(candidate, config_paths["config_path"])
    parsed = parse_config(candidate, config_paths["config_path"])

    assert issues == ()
    assert parsed.groups["newsletter"].agents["builder"].routines[0].id == "daily-review"


def test_parse_config_preserves_routine_arguments_order_and_text(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"]["agents"][0]["routines"][0]["arguments"] = [
        "--mode=review",
        "literal  value  with  spaces",
        "--flag=",
    ]

    parsed = parse_config(raw_config, config_paths["config_path"])

    assert parsed.groups["newsletter"].agents["builder"].routines[0].arguments == (
        "--mode=review",
        "literal  value  with  spaces",
        "--flag=",
    )


@pytest.mark.parametrize(
    ("bad_arguments", "expected_field"),
    [
        ("--mode=review", "groups.newsletter.agents[0].routines[0].arguments"),
        (["--ok", 3], "groups.newsletter.agents[0].routines[0].arguments[1]"),
        (["--ok", ""], "groups.newsletter.agents[0].routines[0].arguments[1]"),
    ],
)
def test_parse_config_rejects_malformed_routine_arguments(
    raw_config, config_paths, bad_arguments, expected_field
):
    from agency.configuration.models import parse_config, validate_config

    raw_config["groups"]["newsletter"]["agents"][0]["routines"][0]["arguments"] = bad_arguments

    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.field == expected_field for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_field"),
    [
        ("agency", [], "agency"),
        ("memory", [], "memory"),
        ("groups", [], "groups"),
    ],
)
def test_parse_config_rejects_malformed_top_level_mappings(
    raw_config, config_paths, field_name, bad_value, expected_field
):
    from agency.configuration.models import parse_config

    raw_config[field_name] = bad_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("group_value", "expected_field"),
    [
        ([], "groups.newsletter"),
        ("newsletter", "groups.newsletter"),
    ],
)
def test_parse_config_rejects_malformed_group_records(raw_config, config_paths, group_value, expected_field):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"] = group_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

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
def test_parse_config_rejects_malformed_nested_shapes(raw_config, config_paths, path, bad_value, expected_field):
    from agency.configuration.models import parse_config

    target = raw_config
    for segment in path[:-1]:
        if isinstance(segment, str) and segment not in target:
            next_segment = path[path.index(segment) + 1]
            target[segment] = [] if isinstance(next_segment, int) else {}
        target = target[segment]
    target[path[-1]] = bad_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)
