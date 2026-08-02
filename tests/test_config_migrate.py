from __future__ import annotations

import copy
from pathlib import Path

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
    result, _ = migrate_v4_to_v5(v4({"sandbox": {"mode": "unrestricted"}}, {}))

    assert result["schema_version"] == 5


def test_restricted_roots_become_one_rule_each():
    result, _ = migrate_v4_to_v5(
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
    result, _ = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "all"}}, {})
    )

    assert rules_of(result) == [{"path": "C:/ws"}]


def test_tools_none_becomes_an_empty_list():
    result, _ = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "none"}}, {})
    )

    assert rules_of(result) == [{"path": "C:/ws", "tools": []}]


def test_unrestricted_becomes_a_single_pathless_rule():
    result, _ = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "unrestricted"}, "tools": {"mode": "allowlist", "names": ["fetch"]}}, {})
    )

    assert rules_of(result) == [{"tools": ["fetch"]}]


def test_capabilities_write_true_is_dropped_not_migrated():
    # Old behaviour: a workspace_path write rule was added, widening beyond the
    # v4 sandbox roots (C4b). New contract: capabilities.write is dropped and
    # reported; no workspace_path rule is emitted.
    result, dropped = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"capabilities": {"write": True}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    agent_rules = agent.get("runtime", {}).get("permissions", {}).get("rules", [])
    rule_paths = [r.get("path") for r in agent_rules]
    assert "C:/ws" not in rule_paths, "workspace_path rule must be absent — it would widen access"
    assert any("capabilities.write" in msg for msg in dropped)
    assert any("g" in msg and "a" in msg for msg in dropped)


def test_capabilities_write_false_adds_no_rules():
    result, dropped = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"capabilities": {"write": False}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    assert agent.get("runtime", {}).get("permissions", {}).get("rules", []) == []


def test_capabilities_write_false_is_reported_not_silently_dropped():
    # In v4 this denied the agent workspace writes. The v5 additive model
    # cannot narrow the group's grant, so the agent now inherits whatever the
    # group allows — a widening the operator never asked for. It must be
    # reported by group and agent name, like every other dropped construct.
    result, dropped = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read", "write"]}},
            {"capabilities": {"write": False}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    messages = [msg for msg in dropped if "capabilities.write" in msg]
    assert messages, "a denied write must be reported, not dropped in silence"
    assert any("group 'g'" in msg and "agent 'a'" in msg for msg in messages)
    # Reporting must not be traded for a widening rule.
    agent_rules = agent.get("runtime", {}).get("permissions", {}).get("rules", [])
    assert agent_rules == []


def test_superseded_keys_are_removed():
    result, _ = migrate_v4_to_v5(v4({"sandbox": {"mode": "unrestricted"}, "tools": {"mode": "all"}}, {}))
    runtime = result["groups"]["g"]["runtime"]

    assert "sandbox" not in runtime
    assert "tools" not in runtime


# ── agent runtime.tools override ─────────────────────────────────────────────

def test_agent_tool_override_is_dropped_and_reported():
    # In v4, runtime.tools was a complete override for all paths. Under v5's
    # additive model it cannot narrow the group's grant on the group's roots
    # (C4a). The override is dropped and the drop is reported.
    result, dropped = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "all"}},
            {"runtime": {"tools": {"mode": "allowlist", "names": ["read"]}}},
        )
    )

    assert any("runtime.tools" in msg for msg in dropped)
    assert any("g" in msg and "a" in msg for msg in dropped)
    agent = result["groups"]["g"]["agents"][0]
    # No agent permissions block when there are no additional_roots.
    assert agent.get("runtime", {}).get("permissions") is None


def test_agent_without_tool_override_generates_no_tool_drop():
    _, dropped = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "all"}},
            {},
        )
    )

    assert not any("runtime.tools" in msg for msg in dropped)


# ── additional_roots ──────────────────────────────────────────────────────────

def test_additional_roots_single_root_becomes_one_agent_rule():
    # Agent tools match group tools; the tool list on additional_roots is still
    # expressed correctly. The runtime.tools override is reported regardless.
    result, dropped = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"runtime": {"sandbox": {"additional_roots": ["C:/extra"]}, "tools": {"mode": "allowlist", "names": ["read"]}}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert agent["runtime"]["permissions"]["rules"] == [{"path": "C:/extra", "tools": ["read"]}]
    assert any("runtime.tools" in msg for msg in dropped)


def test_additional_roots_multiple_roots_each_become_a_rule():
    result, dropped = migrate_v4_to_v5(
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
    assert any("runtime.tools" in msg for msg in dropped)


def test_additional_roots_with_capabilities_write_drops_workspace_rule():
    # Old behaviour asserted the workspace write rule was present (C4b).
    # New contract: capabilities.write is dropped; workspace_path rule must be
    # absent. The additional_roots rule is still emitted with the agent's tools.
    result, dropped = migrate_v4_to_v5(
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
    rule_paths = [r.get("path") for r in rules]
    assert "C:/ws" not in rule_paths, "workspace_path write rule must be absent — it widens access"
    assert any("capabilities.write" in msg for msg in dropped)
    assert any("runtime.tools" in msg for msg in dropped)


# ── Untranslated keys must survive (data-loss guard) ─────────────────────────

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
    result, _ = migrate_v4_to_v5(raw)

    assert result["agency"]["custom_field"] == "keep-agency"
    assert result["groups"]["g"]["dispatch"] == {"enabled": True}
    assert result["groups"]["g"]["default_integration"] == "copilot"
    agent = result["groups"]["g"]["agents"][0]
    assert agent["identity"] == {"display_name": "Agent A"}
    assert agent["routines"] == [{"id": "r1", "schedule": {"at": "09:00"}}]
    assert agent["default_memory"] == {"scope": "agent"}


# ── Round-trip: migrated output loads through the real config parser ──────────

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

    migrated, _ = migrate_v4_to_v5(v4_input)
    parsed = parse_config(migrated, config_paths["config_path"])

    assert parsed.resolved.schema_version == 5


# ── C4 regression: resolve through _merge_rules + tools_for ──────────────────

def test_c4_regression_resolve_through_merge_rules(config_paths):
    """The Task 8 review checked emitted YAML but never resolved through _merge_rules.

    Reproduces the C4a scenario end-to-end: a v4 agent narrows tools relative
    to the group. After migration the override is dropped; the migrated config
    loads successfully; effective tools_for on the group root equals the group's
    grant (all tools). This confirms the migration does not silently narrow the
    group rule and that the result is a valid v5 document.
    """
    from agency.configuration.models import parse_config
    from agency.configuration.effective import resolve_effective_policy
    from agency.integrations import BaseIntegration
    from agency.integrations.models import RuntimeCapabilities

    class _RestrictedIntegration(BaseIntegration):
        name = "copilot"
        display_name = "Copilot"
        supports_execution = False
        runtime_capabilities = RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"}),
        )
        def identity_filename(self): return "AGENTS.md"
        def parse_identity(self, agent_dir): return None
        def write_identity(self, agent_dir, identity): raise NotImplementedError
        def run(self, request): raise NotImplementedError

    ws = str(config_paths["workspace_path"])
    v4_input = {
        "schema_version": 4,
        "agency": {
            "title": "C4 Regression",
            "default_group": "g",
            "ai_backend": "copilot",
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
            "prompt_store": str(config_paths["prompt_store"]),
        },
        "groups": {
            "g": {
                "name": "g",
                "workspace_path": ws,
                "path": str(config_paths["group_path"]),
                "default_integration": "copilot",
                "runtime": {
                    "sandbox": {"mode": "restricted", "roots": [ws]},
                    "tools": {"mode": "all"},
                },
                "agents": [
                    {
                        "name": "a",
                        "blueprint": "b",
                        "integration": "copilot",
                        "runtime": {
                            "tools": {"mode": "allowlist", "names": ["read"]},
                        },
                    }
                ],
            }
        },
    }

    migrated, dropped = migrate_v4_to_v5(v4_input)

    assert any("runtime.tools" in msg for msg in dropped)
    assert any("a" in msg for msg in dropped)

    parsed = parse_config(migrated, config_paths["config_path"])
    policy = resolve_effective_policy(
        parsed.resolved, "g", "a", integration=_RestrictedIntegration()
    )

    # Agent override dropped → only group rule remains → all tools on workspace.
    assert policy.tools_for(Path(ws)) is None, (
        "Expected the group's grant (all tools) after the agent override was dropped"
    )


# ── Version gating ───────────────────────────────────────────────────────────

def test_rejects_schema_version_three():
    with pytest.raises(ValueError, match="cannot migrate"):
        migrate_v4_to_v5({"schema_version": 3})


def test_rejects_missing_schema_version():
    with pytest.raises(ValueError, match="cannot migrate"):
        migrate_v4_to_v5({"groups": {}})


# ── Input isolation ───────────────────────────────────────────────────────────

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
