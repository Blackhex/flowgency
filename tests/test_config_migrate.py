from __future__ import annotations

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


def test_a_version_five_document_is_refused():
    with pytest.raises(ValueError, match="already"):
        migrate_v4_to_v5({"schema_version": 5})
