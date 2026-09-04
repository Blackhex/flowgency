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
    assert parsed.resolved.schema_version == 6


def test_schema_five_requires_prompt_store_and_scoped_routine(raw_config, config_paths):
    from agency.configuration.models import PromptSelector, parse_config

    parsed = parse_config(raw_config, config_paths["config_path"])

    routine = parsed.resolved.teams["newsletter"].agents["builder"].routines[0]
    assert routine.prompt == PromptSelector(scope="blueprint", name="daily-review")
    assert not hasattr(routine, "skill")


def test_schema_four_is_rejected_with_rewrite_hint(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["schema_version"] = 4

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert excinfo.value.issues[0].code == "unsupported-schema-version"
    assert "schema_version 6" in excinfo.value.issues[0].corrective_hint


def test_parse_config_requires_schema_version_six(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.resolved.schema_version == 6

    for value in (None, 1, 2, 3, 4, 5):
        candidate = _clone_config(raw_config)
        if value is None:
            candidate.pop("schema_version")
        else:
            candidate["schema_version"] = value
        issues = validate_config(candidate, config_paths["config_path"])
        assert any(issue.field == "schema_version" for issue in issues)


def test_current_defaults_are_explicit(raw_config, config_paths):
    from agency.configuration.models import parse_config

    parsed = parse_config(raw_config, config_paths["config_path"])

    assert parsed.resolved.schema_version == 6
    assert parsed.resolved.agency.default_team == "newsletter"
    team = parsed.teams["newsletter"]
    assert team.runtime.timeout == 1800
    assert team.runtime.permissions.mode == "unrestricted"
    assert team.dispatch.enabled is False


@pytest.mark.parametrize(
    ("raw_change", "required_code"),
    [
        (lambda raw: raw.__setitem__("schema_version", 5), "unsupported-schema-version"),
        (lambda raw: raw.__setitem__("groups", raw.pop("teams")), "invalid-config"),
    ],
)
def test_v6_rejects_prior_group_control_plane(raw_config, config_paths, raw_change, required_code):
    from agency.configuration.models import validate_config

    raw_change(raw_config)

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == required_code for issue in issues)


def test_configuration_exports_team_not_group_apis():
    import agency.configuration as configuration

    assert hasattr(configuration, "TeamSettingsPatch")
    assert hasattr(configuration, "ResolvedTeamPaths")
    assert hasattr(configuration, "resolve_team_paths")
    assert not hasattr(configuration, "GroupSettingsPatch")
    assert not hasattr(configuration, "ResolvedGroupPaths")
    assert not hasattr(configuration, "resolve_group_paths")


def test_rejects_routine_default_without_routine_context(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "routine"}
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-memory-scope" for issue in issues)


def test_validate_config_reports_group_dispatch_agents_not_supported(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["dispatch"] = {
        "enabled": False,
    }
    raw_config["teams"]["newsletter"]["dispatch"]["agents"] = {
        "builder": [{"at": "09:00"}]
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "team-dispatch-agents-not-supported" for issue in issues)
    assert any(issue.field == "teams.newsletter.dispatch.agents" for issue in issues)
    assert any(
        issue.corrective_hint
        == "Move schedules into each agent's routines on the configured instances."
        for issue in issues
    )


def test_parse_config_rejects_group_dispatch_agents_not_supported(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["teams"]["newsletter"]["dispatch"] = {
        "enabled": False,
    }
    raw_config["teams"]["newsletter"]["dispatch"]["agents"] = {
        "builder": [{"at": "09:00"}]
    }

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "team-dispatch-agents-not-supported" for issue in excinfo.value.issues)


def test_accepts_supported_group_dispatch_and_routines(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["teams"]["newsletter"]["dispatch"] = {
        "enabled": True,
    }

    issues = validate_config(raw_config, config_paths["config_path"])
    parsed = parse_config(raw_config, config_paths["config_path"])

    assert not any(issue.field == "teams.newsletter.dispatch.agents" for issue in issues)
    assert parsed.teams["newsletter"].dispatch.enabled is True
    assert parsed.teams["newsletter"].agents["builder"].routines[0].schedule.at == "09:00"


def test_routine_enabled_is_typed_and_defaults_true(raw_config, config_paths):
    from agency.configuration.models import parse_config

    routine = raw_config["teams"]["newsletter"]["agents"][0]["routines"][0]
    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.teams["newsletter"].agents["builder"].routines[0].enabled is True

    routine["enabled"] = False
    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.teams["newsletter"].agents["builder"].routines[0].enabled is False


def test_rejects_other_unknown_group_dispatch_keys(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["dispatch"] = {
        "enabled": True,
        "owner": "ops",
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "invalid-config" for issue in issues)
    assert any(issue.field == "teams.newsletter.dispatch.owner" for issue in issues)


@pytest.mark.parametrize("blueprint_value", [None, "", "   "])
def test_rejects_missing_or_blank_blueprint(raw_config, config_paths, blueprint_value):
    from agency.configuration.models import parse_config, validate_config

    agent = raw_config["teams"]["newsletter"]["agents"][0]
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

    raw_config["teams"]["newsletter"]["agents"][0]["blueprint"] = blueprint_value

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code == "invalid-blueprint-name" for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert parsed.teams["newsletter"].agents["builder"].blueprint == blueprint_value
        return

    assert any(issue.code == expected_code and issue.field == "blueprint" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == "blueprint" for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("default_team", "expected_code"),
    [
        ("bad group", "invalid-team-name"),
        ("missing-group", "missing-default-team"),
        ("newsletter-team", None),
    ],
)
def test_validates_default_team_identifier_and_reference(
    raw_config, config_paths, default_team, expected_code
):
    from agency.configuration.models import parse_config, validate_config

    if default_team == "newsletter-team":
        team_config = raw_config["teams"].pop("newsletter")
        raw_config["teams"][default_team] = team_config
    raw_config["agency"]["default_team"] = default_team

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code in {"invalid-team-name", "missing-default-team"} for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert parsed.agency.default_team == default_team
        return

    assert any(issue.code == expected_code and issue.field == "agency.default_team" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(
        issue.code == expected_code and issue.field == "agency.default_team" for issue in excinfo.value.issues
    )


def test_allows_omitted_default_team(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["agency"]["default_team"] = ""

    issues = validate_config(raw_config, config_paths["config_path"])
    parsed = parse_config(raw_config, config_paths["config_path"])

    assert not any(issue.field == "agency.default_team" for issue in issues)
    assert parsed.agency.default_team == ""


def test_team_requires_workspace_and_state_paths(raw_config, config_paths):
    from agency.configuration.models import validate_config

    for field in ("workspace_path", "path"):
        candidate = _clone_config(raw_config)
        del candidate["teams"]["newsletter"][field]
        issues = validate_config(candidate, config_paths["config_path"])
        assert any(
            issue.field == f"teams.newsletter.{field}"
            for issue in issues
        )


def test_relative_group_paths_resolve_from_config_directory(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["teams"]["newsletter"]["workspace_path"] = "workspace"
    raw_config["teams"]["newsletter"]["path"] = "groups/newsletter"
    parsed = parse_config(raw_config, config_paths["config_path"])
    group = parsed.teams["newsletter"]
    assert group.workspace_path == (config_paths["config_dir"] / "workspace").resolve()
    assert group.path == (config_paths["config_dir"] / "groups/newsletter").resolve()


def test_parse_config_raises_validation_failed_for_missing_group_path_with_additional_roots(
    raw_config, config_paths
):
    from agency.configuration.models import parse_config

    del raw_config["teams"]["newsletter"]["workspace_path"]
    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"additional_roots": ["editorial"]}
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

    raw_config["teams"]["newsletter"]["agents"] = [agent_entry]

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == expected_field for issue in excinfo.value.issues)


def test_rejects_duplicate_agent_names(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"].append(
        {
            "name": "builder",
            "blueprint": "builder-blueprint",
            "integration": "claude-code",
        }
    )
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "duplicate-agent-name" for issue in issues)


@pytest.mark.parametrize(
    ("team_key", "expected_code"),
    [
        ("bad group", "invalid-team-name"),
        ("Newsletter", "invalid-team-name"),
        ("newsletter-team", None),
    ],
)
def test_validates_team_keys_as_stable_identifiers(raw_config, config_paths, team_key, expected_code):
    from agency.configuration.models import parse_config, validate_config

    team_config = raw_config["teams"].pop("newsletter")
    raw_config["teams"][team_key] = team_config
    if raw_config["agency"].get("default_team") == "newsletter":
        raw_config["agency"]["default_team"] = team_key

    issues = validate_config(raw_config, config_paths["config_path"])

    if expected_code is None:
        assert not any(issue.code == "invalid-team-name" for issue in issues)
        parsed = parse_config(raw_config, config_paths["config_path"])
        assert team_key in parsed.teams
        return

    assert any(issue.code == expected_code and issue.field == "team" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == expected_code and issue.field == "team" for issue in excinfo.value.issues)


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
    raw_config["teams"]["newsletter"]["agents"][0]["default_memory"] = {
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

    agent = raw_config["teams"]["newsletter"]["agents"][0]
    agent["routines"] = [
        {"id": "daily"},
        {"id": "daily"},
    ]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "duplicate-routine-name" for issue in issues)


def test_rejects_missing_explicit_integration(raw_config, config_paths):
    from agency.configuration.models import validate_config

    del raw_config["teams"]["newsletter"]["agents"][0]["integration"]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-explicit-integration" for issue in issues)


@pytest.mark.parametrize("default_integration_value", [None, "", "   "])
def test_rejects_missing_or_blank_group_default_integration(
    raw_config, config_paths, default_integration_value
):
    from agency.configuration.models import parse_config, validate_config

    if default_integration_value is None:
        del raw_config["teams"]["newsletter"]["default_integration"]
    else:
        raw_config["teams"]["newsletter"]["default_integration"] = default_integration_value

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "missing-default-integration" for issue in issues)
    assert any(issue.field == "default_integration" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "missing-default-integration" for issue in excinfo.value.issues)


def test_rejects_invalid_group_allowlist(raw_config, config_paths):
    """v4 tools key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["runtime"] = {
        "tools": {"mode": "allowlist", "names": ["", "  ", "ops"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "superseded-config-key" and "tools" in issue.field for issue in issues)


def test_rejects_group_additional_roots(raw_config, config_paths):
    """v4 sandbox key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["editorial"], "additional_roots": ["tmp"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "superseded-config-key" and "sandbox" in issue.field for issue in issues)


def test_rejects_agent_roots(raw_config, config_paths):
    """v4 sandbox key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["tmp"]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "superseded-config-key" and "sandbox" in issue.field for issue in issues)


def test_validates_group_sandbox_semantics(raw_config, config_paths):
    """v4 sandbox key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["runtime"] = {"sandbox": {"mode": "unrestricted", "roots": ["tmp"]}}

    issues = validate_config(raw_config, config_paths["config_path"])

    assert any(issue.code == "superseded-config-key" and "sandbox" in issue.field for issue in issues)


def test_accepts_restricted_group_permissions(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    raw_config["teams"]["newsletter"]["runtime"] = {
        "permissions": {"mode": "restricted", "rules": [{"tools": ["read"]}]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])
    assert not any(issue.code == "invalid-field-shape" for issue in issues)

    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.teams["newsletter"].runtime.permissions.mode == "restricted"
    assert parsed.teams["newsletter"].runtime.permissions.rules[0].tools == ("read",)


def test_accepts_agent_permission_rules(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    ws = str(raw_config["teams"]["newsletter"]["workspace_path"])
    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "permissions": {"rules": [{"path": ws, "tools": ["read", "write"]}]}
    }

    issues = validate_config(raw_config, config_paths["config_path"])
    assert not any(issue.code == "invalid-field-shape" for issue in issues)

    parsed = parse_config(raw_config, config_paths["config_path"])
    rule = parsed.teams["newsletter"].agents["builder"].runtime.permissions.rules[0]
    assert rule.tools == ("read", "write")


def test_rejects_channel_memory_reference_without_channel(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "channel"}
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-memory-channel" for issue in issues)


def test_rejects_undeclared_channel_memory_reference(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": "missing",
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "missing-memory-channel" for issue in issues)


def test_accepts_declared_channel_memory_reference(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["memory"]["channels"] = {"ops": {"display_name": "Ops"}}
    raw_config["teams"]["newsletter"]["agents"][0]["default_memory"] = {
        "scope": "channel",
        "channel": "ops",
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert not any(issue.code == "missing-memory-channel" for issue in issues)


@pytest.mark.parametrize("scope_path", [
    ["teams", "newsletter", "agents", 0, "default_memory"],
    ["teams", "newsletter", "agents", 0, "routines", 0, "memory"],
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

    raw_config["teams"]["newsletter"]["agents"][0]["routines"] = [
        {"id": "daily", "prompt": {"scope": "blueprint", "name": "daily"}, "schedule": {}},
    ]
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-dispatch-rule" for issue in issues)


@pytest.mark.parametrize("schedule_value", ["daily", ["at", "09:00"], 42])
def test_rejects_non_mapping_schedule_values(raw_config, config_paths, schedule_value):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["routines"] = [
        {"id": "daily", "prompt": {"scope": "blueprint", "name": "daily"}, "schedule": schedule_value},
    ]

    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "invalid-dispatch-rule" for issue in issues)


@pytest.mark.parametrize("routine_value", [None, "daily", ["daily"], 42])
def test_rejects_non_mapping_routine_entries(raw_config, config_paths, routine_value):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["routines"] = [routine_value]

    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(
        issue.code == "invalid-routine-entry" and issue.field == "teams.newsletter.agents[0].routines[0]"
        for issue in issues
    )


@pytest.mark.parametrize("routine_value", [None, "daily", ["daily"], 42])
def test_parse_config_rejects_non_mapping_routine_entries(raw_config, config_paths, routine_value):
    from agency.configuration.models import parse_config

    raw_config["teams"]["newsletter"]["agents"][0]["routines"] = [routine_value]

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(
        issue.code == "invalid-routine-entry" and issue.field == "teams.newsletter.agents[0].routines[0]"
        for issue in excinfo.value.issues
    )


def test_rejects_empty_allowlist(raw_config, config_paths):
    """v4 tools key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "tools": {"mode": "allowlist", "names": []}
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "superseded-config-key" and "tools" in issue.field for issue in issues)


@pytest.mark.parametrize("names, expected_field", [([""], "runtime.tools.names[0]"), (["   "], "runtime.tools.names[0]")])
def test_rejects_blank_allowlist_names(raw_config, config_paths, names, expected_field):
    """v4 tools key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "tools": {"mode": "allowlist", "names": names}
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "superseded-config-key" and "tools" in issue.field for issue in issues)


def test_rejects_unrestricted_with_additions(raw_config, config_paths):
    """v4 sandbox key is now rejected outright in v6."""
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "unrestricted", "additional_roots": ["/tmp"]}
    }
    issues = validate_config(raw_config, config_paths["config_path"])
    assert any(issue.code == "superseded-config-key" and "sandbox" in issue.field for issue in issues)


def test_parse_validate_parity_for_superseded_keys(raw_config, config_paths):
    """v4 sandbox/tools keys produce superseded-config-key in v5."""
    from agency.configuration.models import parse_config, validate_config

    candidate = _clone_config(raw_config)
    candidate["teams"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["editorial"]}
    }
    candidate["teams"]["newsletter"]["agents"][0]["runtime"] = {
        "sandbox": {"mode": "restricted"}
    }

    issues = validate_config(candidate, config_paths["config_path"])
    assert any(issue.code == "superseded-config-key" for issue in issues)

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(candidate, config_paths["config_path"])

    assert excinfo.value.issues == issues


def test_preserves_supported_workspace_fields(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["teams"]["newsletter"]["workspaces"][0]["extra"] = "kept"
    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.teams["newsletter"].workspaces[0].extra == "kept"


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"].append(
                {
                    "name": "builder",
                    "blueprint": "other-blueprint",
                    "integration": "claude-code",
                }
            ),
            id="duplicate-agent-name",
        ),
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"][0].update({"name": "bad name"}),
            id="invalid-agent-identifier",
        ),
        pytest.param(
            lambda raw: raw["teams"].update({"bad team": raw["teams"].pop("newsletter")}),
            id="invalid-team-identifier",
        ),
        pytest.param(
            lambda raw: raw["memory"].update({"channels": {"bad channel": {"display_name": "Ops"}}}),
            id="invalid-channel-identifier",
        ),
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"][0].update({"blueprint": "bad blueprint"}),
            id="invalid-blueprint-identifier",
        ),
        pytest.param(
            lambda raw: raw["agency"].update({"default_team": "missing-team"}),
            id="missing-default-team-reference",
        ),
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"][0]["default_memory"].update(
                {"scope": "channel", "channel": "missing"}
            ),
            id="missing-channel-reference",
        ),
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"][0].update(
                {"runtime": {"tools": {"mode": "allowlist", "names": [""]}}}
            ),
            id="blank-allowlist-name",
        ),
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"][0].update(
                {"runtime": {"sandbox": {"mode": "unrestricted", "additional_roots": ["tmp"]}}}
            ),
            id="sandbox-contradiction",
        ),
        pytest.param(
            lambda raw: raw["teams"]["newsletter"]["agents"][0]["routines"].append(
                {
                    "id": "daily-review",
                    "prompt": {"scope": "blueprint", "name": "daily-review-2"},
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
    agent = candidate["teams"]["newsletter"]["agents"][0]
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
    assert parsed.teams["newsletter"].agents["builder"].routines[0].id == "daily-review"


def test_parse_config_preserves_routine_arguments_order_and_text(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["teams"]["newsletter"]["agents"][0]["routines"][0]["arguments"] = [
        "--mode=review",
        "literal  value  with  spaces",
        "--flag=",
    ]

    parsed = parse_config(raw_config, config_paths["config_path"])

    assert parsed.teams["newsletter"].agents["builder"].routines[0].arguments == (
        "--mode=review",
        "literal  value  with  spaces",
        "--flag=",
    )


@pytest.mark.parametrize(
    ("bad_arguments", "expected_field"),
    [
        ("--mode=review", "teams.newsletter.agents[0].routines[0].arguments"),
        (["--ok", 3], "teams.newsletter.agents[0].routines[0].arguments[1]"),
        (["--ok", ""], "teams.newsletter.agents[0].routines[0].arguments[1]"),
    ],
)
def test_parse_config_rejects_malformed_routine_arguments(
    raw_config, config_paths, bad_arguments, expected_field
):
    from agency.configuration.models import parse_config, validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["routines"][0]["arguments"] = bad_arguments

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
        ("teams", [], "teams"),
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
    ("team_value", "expected_field"),
    [
        ([], "teams.newsletter"),
        ("newsletter", "teams.newsletter"),
    ],
)
def test_parse_config_rejects_malformed_team_records(raw_config, config_paths, team_value, expected_field):
    from agency.configuration.models import parse_config

    raw_config["teams"]["newsletter"] = team_value

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert any(issue.field == expected_field for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    ("path", "bad_value", "expected_field"),
    [
        (["memory", "channels"], [], "memory.channels"),
        (["memory", "channels", "support"], [], "memory.channels.support"),
        (["teams", "newsletter", "runtime"], [], "teams.newsletter.runtime"),
        (["teams", "newsletter", "dispatch"], [], "teams.newsletter.dispatch"),
        (["teams", "newsletter", "workspaces"], {}, "teams.newsletter.workspaces"),
        (["teams", "newsletter", "agents"], {}, "teams.newsletter.agents"),
        (["teams", "newsletter", "agents", 0, "identity"], [], "teams.newsletter.agents[0].identity"),
        (["teams", "newsletter", "agents", 0, "runtime"], [], "teams.newsletter.agents[0].runtime"),
        (["teams", "newsletter", "agents", 0, "default_memory"], [], "teams.newsletter.agents[0].default_memory"),
        (["teams", "newsletter", "agents", 0, "routines"], {}, "teams.newsletter.agents[0].routines"),
        (["teams", "newsletter", "agents", 0, "routines", 0, "schedule"], [], "teams.newsletter.agents[0].routines[0].schedule"),
        (["teams", "newsletter", "agents", 0, "routines", 0, "memory"], [], "teams.newsletter.agents[0].routines[0].memory"),
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


# ---------------------------------------------------------------------------
# catch_up field on ScheduleRule
# ---------------------------------------------------------------------------

def _write_minimal_config(tmp_path, *, catch_up=None, dispatch_daily_limit=None, jobs_pool=None):
    import yaml

    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)

    schedule = {"at": "08:00"}
    if catch_up is not None:
        schedule["catch_up"] = catch_up

    agency = {
        "title": "Test",
        "default_team": "grp",
        "ai_backend": "copilot",
        "agent_library": str(lib),
        "compilation_cache": str(tmp_path / "cache"),
        "memory_store": str(tmp_path / "memory"),
        "prompt_store": str(prompts),
    }
    if jobs_pool is not None:
        agency["jobs"] = {"pool": jobs_pool}

    raw = {
        "schema_version": 6,
        "agency": agency,
        "teams": {
            "grp": {
                "name": "Grp",
                "workspace_path": str(ws),
                "path": str(tmp_path / "groups" / "grp"),
                "default_integration": "copilot",
                **({"dispatch": {"daily_limit": dispatch_daily_limit}} if dispatch_daily_limit is not None else {}),
                "agents": [
                    {
                        "name": "product",
                        "blueprint": "product-bp",
                        "integration": "copilot",
                        "routines": [
                            {
                                "id": "daily",
                                "prompt": {"scope": "blueprint", "name": "daily"},
                                "schedule": schedule,
                                "memory": {"scope": "routine"},
                            }
                        ],
                    }
                ],
            }
        },
    }

    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump(raw))
    return config


def load_config(config_path):
    import yaml
    from agency.configuration.models import parse_config

    raw = yaml.safe_load(config_path.read_text())
    return parse_config(raw, config_path)


def test_schedule_accepts_a_catch_up_value(tmp_path):
    config = _write_minimal_config(tmp_path, catch_up="always")
    parsed = load_config(config)
    routine = parsed.teams["grp"].agents["product"].routines[0]
    assert routine.schedule.catch_up == "always"


def test_schedule_catch_up_defaults_to_none_in_the_model(tmp_path):
    config = _write_minimal_config(tmp_path)
    parsed = load_config(config)
    routine = parsed.teams["grp"].agents["product"].routines[0]
    assert routine.schedule.catch_up is None


def test_malformed_catch_up_is_rejected(tmp_path):
    config = _write_minimal_config(tmp_path, catch_up="sometimes")
    with pytest.raises(ValidationFailed) as error:
        load_config(config)
    assert any(issue.code == "invalid-dispatch-rule" for issue in error.value.issues)


def test_group_dispatch_rejects_daily_limit(tmp_path):
    config = _write_minimal_config(tmp_path, dispatch_daily_limit=20)
    with pytest.raises(ValidationFailed):
        load_config(config)


def test_jobs_pool_defaults_to_four(tmp_path):
    parsed = load_config(_write_minimal_config(tmp_path))
    assert parsed.agency.jobs.pool == 4


def test_jobs_pool_is_read_from_config(tmp_path):
    parsed = load_config(_write_minimal_config(tmp_path, jobs_pool=2))
    assert parsed.agency.jobs.pool == 2


def test_jobs_pool_below_one_is_rejected(tmp_path):
    with pytest.raises(ValidationFailed):
        load_config(_write_minimal_config(tmp_path, jobs_pool=0))


# ── M6: v4 keys rejected in v5 documents ─────────────────────────────────────


@pytest.mark.parametrize("key", ["sandbox", "tools"])
def test_v5_group_runtime_rejects_v4_key(raw_config, config_paths, key):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["runtime"][key] = {"mode": "all"}

    issues = validate_config(raw_config, config_paths["config_path"])

    matches = [i for i in issues if i.code == "superseded-config-key" and key in i.field]
    assert matches, f"Expected rejection of group runtime.{key}"
    assert "migrate" in matches[0].corrective_hint


@pytest.mark.parametrize("key", ["sandbox", "tools"])
def test_v5_agent_runtime_rejects_v4_key(raw_config, config_paths, key):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["runtime"] = {key: {"mode": "all"}}

    issues = validate_config(raw_config, config_paths["config_path"])

    matches = [i for i in issues if i.code == "superseded-config-key" and key in i.field]
    assert matches, f"Expected rejection of agent runtime.{key}"


def test_v5_agent_rejects_capabilities_key(raw_config, config_paths):
    from agency.configuration.models import validate_config

    raw_config["teams"]["newsletter"]["agents"][0]["capabilities"] = {"write": True}

    issues = validate_config(raw_config, config_paths["config_path"])

    matches = [i for i in issues if i.code == "superseded-config-key" and "capabilities" in i.field]
    assert matches, "Expected rejection of capabilities key"
    assert "migrate" in matches[0].corrective_hint
