from __future__ import annotations

from agency.configuration.models import parse_config
from agency.records.validation import writable_agent_names


def build_config(raw_config, config_paths, agents):
    raw_config["groups"]["newsletter"]["agents"] = agents
    return parse_config(raw_config, config_paths["config_path"]).resolved


def test_only_writable_agents_are_returned(raw_config, config_paths):
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "paul",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "capabilities": {"write": True},
            },
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "capabilities": {"write": False},
            },
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset({"paul"})


def test_agents_without_a_capabilities_block_are_not_writable(raw_config, config_paths):
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
            }
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset()


def test_unknown_group_yields_an_empty_set(raw_config, config_paths):
    config = build_config(raw_config, config_paths, [])

    assert writable_agent_names(config, "no-such-group") == frozenset()
