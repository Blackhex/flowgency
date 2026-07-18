from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
    (snapshot.path.parent / "agents" / "editorial").mkdir(parents=True, exist_ok=True)

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


def test_patch_group_settings_state_preserves_extension_keys(config_store):
    from agency.configuration.patches import (
        GroupSettingsStatePatch,
        patch_group_settings_state,
    )

    snapshot = config_store.load()
    snapshot.raw["groups"]["newsletter"]["group_extension"] = {"theme": "sunset"}
    snapshot.raw["groups"]["newsletter"]["runtime"] = {
        "timeout": 1200,
        "runtime_extension": {"preserve": True},
        "sandbox": {"mode": "restricted", "roots": ["superseded"], "sandbox_extension": {"preserve": True}},
        "tools": {"mode": "allowlist", "names": ["shell"], "tools_extension": {"preserve": True}},
    }
    snapshot.raw["groups"]["newsletter"]["dispatch"] = {
        "enabled": False,
        "daily_limit": 7,
    }
    snapshot.raw["groups"]["newsletter"]["workspaces"] = [
        {
            "name": "Terminal Grid",
            "type": "tmux",
            "config": {"script_path": "tmux-agents.sh"},
            "workspace_extension": {"preserve": True},
        }
    ]
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )
    (snapshot.path.parent / "agents" / "editorial" / "repo").mkdir(parents=True, exist_ok=True)

    refreshed = config_store.load()
    updated = patch_group_settings_state(
        config_store,
        refreshed.revision,
        "newsletter",
        GroupSettingsStatePatch(
            name="Editorial",
            path=str(refreshed.path.parent / "agents" / "editorial"),
            default_integration="copilot",
            runtime_timeout=2400,
            sandbox_mode="restricted",
            sandbox_roots=("repo",),
            tool_mode="allowlist",
            tool_names=("shell", "write"),
            dispatch_enabled=True,
            dispatch_daily_limit=12,
            workspaces=(
                {
                    "name": "Primary",
                    "type": "tmux",
                    "config": {"script_path": "primary.sh"},
                    "workspace_extension": {"preserve": True},
                },
            ),
        ),
    )

    group = updated.raw["groups"]["newsletter"]
    assert group["group_extension"] == {"theme": "sunset"}
    assert group["runtime"]["runtime_extension"] == {"preserve": True}
    assert group["runtime"]["sandbox"]["sandbox_extension"] == {"preserve": True}
    assert group["runtime"]["tools"]["tools_extension"] == {"preserve": True}
    assert group["workspaces"][0]["workspace_extension"] == {"preserve": True}


def test_create_group_rejects_unknown_root_key_on_load(
    config_store,
):
    from agency.configuration import ValidationFailed

    snapshot = config_store.load()
    snapshot.raw["extensions"] = {"beta": {"enabled": True}}
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailed) as excinfo:
        config_store.load()

    assert any(issue.field == "extensions" for issue in excinfo.value.issues)


def test_create_group_state_uses_one_patch_and_rolls_back_on_failure(config_store, monkeypatch):
    from agency.configuration.patches import GroupCreateStatePatch, create_group_state

    snapshot = config_store.load()
    calls = 0
    original_patch = config_store.patch

    def patched_patch(expected_revision, patcher):
        nonlocal calls
        calls += 1

        def failing(raw):
            patcher(raw)
            raise RuntimeError("boom")

        return original_patch(expected_revision, failing)

    monkeypatch.setattr(config_store, "patch", patched_patch)

    with pytest.raises(RuntimeError, match="boom"):
        create_group_state(
            config_store,
            snapshot.revision,
            "research",
            GroupCreateStatePatch(
                name="Research",
                path=str(snapshot.path.parent / "agents" / "research"),
                default_integration="copilot",
                runtime_timeout=2400,
                sandbox_mode="restricted",
                sandbox_roots=("repo", "cowork"),
                tool_mode="allowlist",
                tool_names=("shell", "write"),
                dispatch_enabled=True,
                dispatch_daily_limit=12,
                workspaces=(
                    {
                        "name": "Primary",
                        "type": "tmux",
                        "config": {"script_path": "primary.sh"},
                    },
                ),
            ),
        )

    assert calls == 1
    assert "research" not in config_store.load().raw["groups"]


def test_patch_memory_channels_rejects_unknown_root_key_on_load(config_store):
    from agency.configuration import ValidationFailed
    from agency.configuration.patches import patch_memory_channels

    snapshot = config_store.load()
    snapshot.raw["extensions"] = {"retention": "custom"}
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed) as excinfo:
        config_store.load()

    assert any(issue.field == "extensions" for issue in excinfo.value.issues)


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
    (snapshot.path.parent / "agents" / "newsletter" / "shared").mkdir(parents=True, exist_ok=True)
    (snapshot.path.parent / "agents" / "newsletter" / "assets").mkdir(parents=True, exist_ok=True)

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
