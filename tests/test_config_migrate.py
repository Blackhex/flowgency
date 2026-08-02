from __future__ import annotations

import copy

import pytest

from agency.configuration.migrate import migrate_v4_to_v5


def v4(group_runtime, agent):
    return {
        "schema_version": 4,
        "agency": {"title": "Agency"},
        "groups": {
            "g": {
                "name": "G",
                "workspace_path": "C:/ws",
                "path": "C:/state",
                "default_integration": "copilot",
                "runtime": group_runtime,
                "agents": [dict({"name": "a", "blueprint": "b", "integration": "copilot"}, **agent)],
            }
        },
    }


def rules_of(result, group="g"):
    return result["groups"][group]["runtime"]["permissions"]["rules"]


def test_schema_version_becomes_five():
    result = migrate_v4_to_v5(v4({"sandbox": {"mode": "unrestricted"}}, {}))

    assert result["schema_version"] == 5


def test_restricted_roots_become_one_rule_each():
    result = migrate_v4_to_v5(
        v4(
            {
                "sandbox": {"mode": "restricted", "roots": ["C:/ws", "C:/other"]},
                "tools": {"mode": "allowlist", "names": ["read", "search"]},
            },
            {},
        )
    )

    assert rules_of(result) == [
        {"path": "C:/ws", "tools": ["read", "search"]},
        {"path": "C:/other", "tools": ["read", "search"]},
    ]
    assert result["groups"]["g"]["runtime"]["permissions"]["mode"] == "restricted"


def test_tools_all_omits_the_tools_key():
    result = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "all"}}, {})
    )

    assert rules_of(result) == [{"path": "C:/ws"}]


def test_tools_none_becomes_an_empty_list():
    result = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "none"}}, {})
    )

    assert rules_of(result) == [{"path": "C:/ws", "tools": []}]


def test_unrestricted_becomes_a_single_pathless_rule():
    result = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "unrestricted"}, "tools": {"mode": "allowlist", "names": ["fetch"]}}, {})
    )

    assert rules_of(result) == [{"tools": ["fetch"]}]


def test_capabilities_write_true_adds_write_to_the_workspace_rule():
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"capabilities": {"write": True}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    assert {"path": "C:/ws", "tools": ["read", "write"]} in agent["runtime"]["permissions"]["rules"]


def test_capabilities_write_false_adds_no_write():
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"capabilities": {"write": False}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    assert agent.get("runtime", {}).get("permissions", {}).get("rules", []) == []


def test_superseded_keys_are_removed():
    result = migrate_v4_to_v5(v4({"sandbox": {"mode": "unrestricted"}, "tools": {"mode": "all"}}, {}))
    runtime = result["groups"]["g"]["runtime"]

    assert "sandbox" not in runtime
    assert "tools" not in runtime


# GAP 1 — additional_roots (lines 67-74 of migrate.py)

def test_additional_roots_single_root_becomes_one_agent_rule():
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"runtime": {"sandbox": {"additional_roots": ["C:/extra"]}, "tools": {"mode": "allowlist", "names": ["read"]}}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert agent["runtime"]["permissions"]["rules"] == [{"path": "C:/extra", "tools": ["read"]}]


def test_additional_roots_multiple_roots_each_become_a_rule():
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"runtime": {"sandbox": {"additional_roots": ["C:/a", "C:/b"]}, "tools": {"mode": "allowlist", "names": ["read"]}}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert agent["runtime"]["permissions"]["rules"] == [
        {"path": "C:/a", "tools": ["read"]},
        {"path": "C:/b", "tools": ["read"]},
    ]


def test_additional_roots_do_not_receive_write_even_with_capabilities_write():
    # Write is intentionally granted to workspace_path only; additional_roots are read-only.
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {
                "capabilities": {"write": True},
                "runtime": {
                    "sandbox": {"additional_roots": ["C:/extra"]},
                    "tools": {"mode": "allowlist", "names": ["read"]},
                },
            },
        )
    )
    agent = result["groups"]["g"]["agents"][0]
    rules = agent["runtime"]["permissions"]["rules"]

    assert {"path": "C:/extra", "tools": ["read"]} in rules
    assert {"path": "C:/ws", "tools": ["read", "write"]} in rules


# GAP 2 — untranslated keys must survive migration (data-loss guard)

def test_untranslated_keys_survive_migration():
    raw = {
        "schema_version": 4,
        "agency": {"title": "Agency", "custom_field": "keep-agency"},
        "groups": {
            "g": {
                "name": "G",
                "workspace_path": "C:/ws",
                "path": "C:/state",
                "default_integration": "copilot",
                "dispatch": {"enabled": True},
                "runtime": {"sandbox": {"mode": "unrestricted"}},
                "agents": [
                    {
                        "name": "a",
                        "blueprint": "b",
                        "integration": "copilot",
                        "identity": {"display_name": "Agent A"},
                        "routines": [{"id": "r1", "schedule": {"at": "09:00"}}],
                        "default_memory": {"scope": "agent"},
                    }
                ],
            }
        },
    }
    result = migrate_v4_to_v5(raw)

    assert result["agency"]["custom_field"] == "keep-agency"
    assert result["groups"]["g"]["dispatch"] == {"enabled": True}
    assert result["groups"]["g"]["default_integration"] == "copilot"
    agent = result["groups"]["g"]["agents"][0]
    assert agent["identity"] == {"display_name": "Agent A"}
    assert agent["routines"] == [{"id": "r1", "schedule": {"at": "09:00"}}]
    assert agent["default_memory"] == {"scope": "agent"}


# GAP 3 — round-trip: migrated output must be accepted by parse_config

def test_round_trip_produces_valid_v5_config(config_paths):
    from agency.configuration.models import parse_config

    v4_input = {
        "schema_version": 4,
        "agency": {
            "title": "Round Trip",
            "default_group": "g",
            "ai_backend": "copilot",
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
            "prompt_store": str(config_paths["prompt_store"]),
        },
        "groups": {
            "g": {
                "name": "G",
                "workspace_path": str(config_paths["workspace_path"]),
                "path": str(config_paths["group_path"]),
                "default_integration": "copilot",
                "runtime": {
                    "sandbox": {"mode": "restricted", "roots": [str(config_paths["workspace_path"])]},
                    "tools": {"mode": "allowlist", "names": ["read", "search"]},
                },
                "agents": [
                    {"name": "a", "blueprint": "b", "integration": "copilot"},
                ],
            }
        },
    }

    migrated = migrate_v4_to_v5(v4_input)
    parsed = parse_config(migrated, config_paths["config_path"])

    assert parsed.resolved.schema_version == 5


# GAP 4 — refusal of non-4 schema versions

def test_rejects_schema_version_three():
    with pytest.raises(ValueError, match="cannot migrate"):
        migrate_v4_to_v5({"schema_version": 3})


def test_rejects_missing_schema_version():
    with pytest.raises(ValueError, match="cannot migrate"):
        migrate_v4_to_v5({"groups": {}})


# GAP 5 — input isolation: the original mapping must not be mutated

def test_does_not_mutate_the_input():
    original = v4(
        {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
        {"capabilities": {"write": True}},
    )
    before = copy.deepcopy(original)

    migrate_v4_to_v5(original)

    assert original == before


def test_a_version_five_document_is_refused():
    with pytest.raises(ValueError, match="already"):
        migrate_v4_to_v5({"schema_version": 5})
