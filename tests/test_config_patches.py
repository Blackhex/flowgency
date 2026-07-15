from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def config_store(tmp_path, canonical_raw_config):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(tmp_path / "config.yaml", canonical_raw_config)
    return ConfigStore(path)


def test_agent_patch_preserves_workspaces_and_other_agents(config_store):
    from agency.configuration.patches import (
        AgentProfilePatch,
        patch_agent_profile,
    )

    snapshot = config_store.load()
    group = snapshot.raw["groups"]["newsletter"]
    group["agents"].append(
        {
            "name": "advisor",
            "blueprint": "advisor-blueprint",
            "integration": "claude-code",
            "worktree_extension": {"enabled": True},
        }
    )
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    snapshot = config_store.load()
    updated = patch_agent_profile(
        config_store,
        snapshot.revision,
        "newsletter",
        "advisor",
        AgentProfilePatch(
            display_name="Editor",
            title="Lead",
            emoji="",
            can_write=False,
        ),
    )

    assert (
        updated.raw["groups"]["newsletter"]["workspaces"]
        == snapshot.raw["groups"]["newsletter"]["workspaces"]
    )
    assert (
        updated.raw["groups"]["newsletter"]["agents"][0]
        == snapshot.raw["groups"]["newsletter"]["agents"][0]
    )
    assert (
        updated.raw["groups"]["newsletter"]["agents"][1][
            "worktree_extension"
        ]
        == {"enabled": True}
    )
    assert len(updated.config.groups["newsletter"].agents) == 2


def test_patch_agent_profile_preserves_extension_keys(config_store):
    from agency.configuration.patches import (
        AgentProfilePatch,
        patch_agent_profile,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["groups"]["newsletter"]["agents"][0]
    agent["identity"] = {
        "display_name": "Builder",
        "title": "Engineer",
        "emoji": "🤖",
        "nickname": "builder-bot",
    }
    agent["capabilities"] = {"write": True, "approve": True}
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = config_store.load()
    updated = patch_agent_profile(
        config_store,
        refreshed.revision,
        "newsletter",
        "builder",
        AgentProfilePatch(
            display_name="Editor",
            title="Lead",
            emoji="",
            can_write=False,
        ),
    )

    identity = updated.raw["groups"]["newsletter"]["agents"][0]["identity"]
    capabilities = updated.raw["groups"]["newsletter"]["agents"][0][
        "capabilities"
    ]
    assert identity == {
        "display_name": "Editor",
        "title": "Lead",
        "emoji": "",
        "nickname": "builder-bot",
    }
    assert capabilities == {"write": False, "approve": True}


def test_patch_group_settings_preserves_unowned_group_fields(config_store):
    from agency.configuration.patches import (
        GroupSettingsPatch,
        patch_group_settings,
    )

    snapshot = config_store.load()
    snapshot.raw["groups"]["newsletter"]["ui_extension"] = {"theme": "sunset"}
    snapshot.raw["groups"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "sandbox": {"mode": "unrestricted"},
        "tools": {"mode": "all"},
    }
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = config_store.load()
    updated = patch_group_settings(
        config_store,
        refreshed.revision,
        "newsletter",
        GroupSettingsPatch(
            name="Editorial",
            path=str(refreshed.path.parent / "agents" / "editorial"),
            default_integration="copilot",
        ),
    )

    assert updated.raw["groups"]["newsletter"]["ui_extension"] == {
        "theme": "sunset"
    }
    assert (
        updated.raw["groups"]["newsletter"]["runtime"]
        == refreshed.raw["groups"]["newsletter"]["runtime"]
    )


def test_create_group_requires_absent_group_and_preserves_other_top_level_fields(
    config_store,
):
    from agency.configuration.patches import GroupSettingsPatch, create_group

    snapshot = config_store.load()
    snapshot.raw["extensions"] = {"beta": {"enabled": True}}
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = config_store.load()
    updated = create_group(
        config_store,
        refreshed.revision,
        "research",
        GroupSettingsPatch(
            name="Research",
            path=str(refreshed.path.parent / "agents" / "research"),
            default_integration="claude-code",
        ),
    )

    assert updated.raw["extensions"] == {"beta": {"enabled": True}}
    assert updated.raw["groups"]["research"]["name"] == "Research"


def test_patch_memory_channels_replaces_owned_subtree_only(config_store):
    from agency.configuration.patches import patch_memory_channels

    snapshot = config_store.load()
    snapshot.raw["extensions"] = {"retention": "custom"}
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = config_store.load()
    updated = patch_memory_channels(
        config_store,
        refreshed.revision,
        {
            "support": {"display_name": "Support"},
            "ops-channel": {"display_name": "Ops"},
        },
    )

    assert updated.raw["memory"]["channels"] == {
        "support": {"display_name": "Support"},
        "ops-channel": {"display_name": "Ops"},
    }
    assert updated.raw["extensions"] == {"retention": "custom"}


def test_patch_agent_runtime_preserves_extension_keys(config_store):
    from agency.configuration.patches import (
        AgentRuntimePatch,
        ToolPolicy,
        patch_agent_runtime,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["groups"]["newsletter"]["agents"][0]
    agent["runtime"] = {
        "timeout": 900,
        "sandbox": {
            "mode": "restricted",
            "additional_roots": ["superseded"],
            "sandbox_extension": {"preserve": True},
        },
        "tools": {
            "mode": "allowlist",
            "names": ["shell"],
            "tools_extension": {"preserve": True},
        },
        "runtime_extension": {"preserve": True},
    }
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = config_store.load()
    updated = patch_agent_runtime(
        config_store,
        refreshed.revision,
        "newsletter",
        "builder",
        AgentRuntimePatch(
            timeout=1200,
            additional_roots=("shared", "assets"),
            tools=ToolPolicy(mode="allowlist", names=("shell", "write")),
        ),
    )

    runtime = updated.raw["groups"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1200
    assert runtime["sandbox"]["mode"] == "restricted"
    assert runtime["sandbox"]["additional_roots"] == ["shared", "assets"]
    assert runtime["sandbox"]["sandbox_extension"] == {"preserve": True}
    assert runtime["tools"] == {
        "mode": "allowlist",
        "names": ["shell", "write"],
        "tools_extension": {"preserve": True},
    }
    assert runtime["runtime_extension"] == {"preserve": True}


def test_patch_agent_runtime_clears_only_known_fields(config_store):
    from agency.configuration.patches import (
        AgentRuntimePatch,
        patch_agent_runtime,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["groups"]["newsletter"]["agents"][0]
    agent["runtime"] = {
        "timeout": 2400,
        "sandbox": {
            "mode": "restricted",
            "additional_roots": ["old"],
            "sandbox_extension": {"preserve": True},
        },
        "tools": {
            "mode": "allowlist",
            "names": ["shell"],
            "tools_extension": {"preserve": True},
        },
        "runtime_extension": {"preserve": True},
    }
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = config_store.load()
    updated = patch_agent_runtime(
        config_store,
        refreshed.revision,
        "newsletter",
        "builder",
        AgentRuntimePatch(
            timeout=None,
            additional_roots=(),
            tools=None,
        ),
    )

    runtime = updated.raw["groups"]["newsletter"]["agents"][0]["runtime"]
    assert "timeout" not in runtime
    assert runtime["sandbox"] == {
        "mode": "restricted",
        "sandbox_extension": {"preserve": True},
    }
    assert runtime["tools"] == {
        "tools_extension": {"preserve": True},
    }
    assert runtime["runtime_extension"] == {"preserve": True}
