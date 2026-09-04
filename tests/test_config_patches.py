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
def config_store(tmp_path, raw_config):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(tmp_path / "config.yaml", raw_config)
    return ConfigStore(path)


def test_routine_patch_round_trips_the_recovery_bound(config_store):
    from agency.configuration.patches import replace_agent_routines

    snapshot = config_store.load()
    updated = replace_agent_routines(
        config_store,
        snapshot.revision,
        "newsletter",
        "builder",
        [
            {
                "id": "daily-review",
                "prompt": {"scope": "blueprint", "name": "pr-review"},
                "enabled": True,
                "arguments": [],
                "schedule": {"at": "09:00", "catch_up": "48h"},
            }
        ],
    )

    saved = yaml.safe_load(updated.path.read_text(encoding="utf-8"))
    routine = saved["teams"]["newsletter"]["agents"][0]["routines"][0]
    assert routine["schedule"] == {"at": "09:00", "catch_up": "48h"}
    reloaded = config_store.load().config.teams["newsletter"].agents["builder"]
    assert reloaded.routines[0].schedule.catch_up == "48h"


def test_agent_patch_preserves_workspaces_and_other_agents(config_store):
    from agency.configuration.patches import (
        AgentProfilePatch,
        patch_agent_profile,
    )

    snapshot = config_store.load()
    team = snapshot.raw["teams"]["newsletter"]
    team["agents"].append(
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
        ),
    )

    assert (
        updated.raw["teams"]["newsletter"]["workspaces"]
        == snapshot.raw["teams"]["newsletter"]["workspaces"]
    )
    assert (
        updated.raw["teams"]["newsletter"]["agents"][0]
        == snapshot.raw["teams"]["newsletter"]["agents"][0]
    )
    assert (
        updated.raw["teams"]["newsletter"]["agents"][1][
            "worktree_extension"
        ]
        == {"enabled": True}
    )
    assert len(updated.config.teams["newsletter"].agents) == 2


def test_patch_agent_profile_preserves_extension_keys(config_store):
    from agency.configuration.patches import (
        AgentProfilePatch,
        patch_agent_profile,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    agent["identity"] = {
        "display_name": "Builder",
        "title": "Engineer",
        "emoji": "🤖",
        "nickname": "builder-bot",
    }
    agent["custom_extension"] = {"approve": True}
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
        ),
    )

    identity = updated.raw["teams"]["newsletter"]["agents"][0]["identity"]
    assert identity == {
        "display_name": "Editor",
        "title": "Lead",
        "emoji": "",
        "nickname": "builder-bot",
    }


def test_patch_team_settings_preserves_unowned_group_fields(config_store):
    from agency.configuration.patches import (
        TeamSettingsPatch,
        patch_team_settings,
    )

    snapshot = config_store.load()
    snapshot.raw["teams"]["newsletter"]["ui_extension"] = {"theme": "sunset"}
    snapshot.raw["teams"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "permissions": {"mode": "unrestricted"},
    }
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )
    (snapshot.path.parent / "workspace" / "editorial").mkdir(
        parents=True, exist_ok=True
    )
    (snapshot.path.parent / "agents" / "editorial").mkdir(parents=True, exist_ok=True)

    refreshed = config_store.load()
    updated = patch_team_settings(
        config_store,
        refreshed.revision,
        "newsletter",
        TeamSettingsPatch(
            name="Editorial",
            workspace_path=str(refreshed.path.parent / "workspace" / "editorial"),
            path=str(refreshed.path.parent / "groups" / "editorial"),
            default_integration="copilot",
        ),
    )

    assert updated.raw["teams"]["newsletter"]["ui_extension"] == {
        "theme": "sunset"
    }
    assert updated.raw["teams"]["newsletter"]["workspace_path"] == str(
        refreshed.path.parent / "workspace" / "editorial"
    )
    assert updated.raw["teams"]["newsletter"]["path"] == str(
        refreshed.path.parent / "groups" / "editorial"
    )
    assert (
        updated.raw["teams"]["newsletter"]["runtime"]
        == refreshed.raw["teams"]["newsletter"]["runtime"]
    )


def test_patch_team_settings_state_preserves_extension_keys(config_store):
    from agency.configuration.patches import (
        TeamSettingsStatePatch,
        patch_team_settings_state,
    )

    snapshot = config_store.load()
    snapshot.raw["teams"]["newsletter"]["group_extension"] = {"theme": "sunset"}
    snapshot.raw["teams"]["newsletter"]["runtime"] = {
        "timeout": 1200,
        "runtime_extension": {"preserve": True},
        "permissions": {
            "mode": "restricted",
            "rules": [{"path": "shared-root", "tools": ["shell"]}],
        },
    }
    snapshot.raw["teams"]["newsletter"]["dispatch"] = {
        "enabled": False,
    }
    snapshot.raw["teams"]["newsletter"]["workspaces"] = [
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
    (snapshot.path.parent / "workspace" / "editorial" / "repo").mkdir(
        parents=True, exist_ok=True
    )

    refreshed = config_store.load()
    updated = patch_team_settings_state(
        config_store,
        refreshed.revision,
        "newsletter",
        TeamSettingsStatePatch(
            name="Editorial",
            workspace_path=str(refreshed.path.parent / "workspace" / "editorial"),
            path=str(refreshed.path.parent / "groups" / "editorial"),
            default_integration="copilot",
            runtime_timeout=2400,
            permission_mode="restricted",
            permission_rules=({"tools": ["shell", "write"]},),
            dispatch_enabled=True,
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

    team = updated.raw["teams"]["newsletter"]
    assert team["workspace_path"] == str(
        refreshed.path.parent / "workspace" / "editorial"
    )
    assert team["path"] == str(refreshed.path.parent / "groups" / "editorial")
    assert team["group_extension"] == {"theme": "sunset"}
    assert team["runtime"]["runtime_extension"] == {"preserve": True}
    assert team["workspaces"][0]["workspace_extension"] == {"preserve": True}


def test_create_team_rejects_unknown_root_key_on_load(
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


def test_create_team_state_uses_one_patch_and_rolls_back_on_failure(config_store, monkeypatch):
    from agency.configuration.patches import TeamCreateStatePatch, create_team_state

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
        create_team_state(
            config_store,
            snapshot.revision,
            "research",
            TeamCreateStatePatch(
                name="Research",
                workspace_path=str(snapshot.path.parent / "workspace" / "research"),
                path=str(snapshot.path.parent / "groups" / "research"),
                default_integration="copilot",
                runtime_timeout=2400,
                permission_mode="restricted",
                permission_rules=({"tools": ["shell", "write"]},),
                dispatch_enabled=True,
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
    assert "research" not in config_store.load().raw["teams"]


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


def test_register_and_unregister_agent_prompt(config_store):
    from agency.configuration.patches import (
        register_agent_prompt,
        unregister_agent_prompt,
    )

    snapshot = config_store.load()

    registered = register_agent_prompt(
        config_store,
        snapshot.revision,
        "newsletter",
        "builder",
        "local-triage",
    )

    assert registered.raw["teams"]["newsletter"]["agents"][0]["prompts"] == [
        "local-triage"
    ]

    unregistered = unregister_agent_prompt(
        config_store,
        registered.revision,
        "newsletter",
        "builder",
        "local-triage",
    )

    assert unregistered.raw["teams"]["newsletter"]["agents"][0]["prompts"] == []


def test_patch_agent_runtime_preserves_extension_keys(config_store):
    from agency.configuration.patches import (
        AgentRuntimePatch,
        patch_agent_runtime,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    agent["runtime"] = {
        "timeout": 900,
        "permissions": {
            "rules": [{"path": "/shared-root", "tools": ["shell"]}],
        },
        "runtime_extension": {"preserve": True},
    }
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )
    workspace_root = Path(snapshot.raw["teams"]["newsletter"]["workspace_path"])
    (workspace_root / "editorial").mkdir(parents=True, exist_ok=True)
    (workspace_root / "assets").mkdir(parents=True, exist_ok=True)

    refreshed = config_store.load()
    updated = patch_agent_runtime(
        config_store,
        refreshed.revision,
        "newsletter",
        "builder",
        AgentRuntimePatch(
            timeout=1200,
            rules=(
                {"path": str(workspace_root / "editorial"), "tools": ["read", "write"]},
                {"path": str(workspace_root / "assets"), "tools": ["read"]},
            ),
        ),
    )

    runtime = updated.raw["teams"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1200
    assert runtime["permissions"]["rules"] == [
        {"path": str(workspace_root / "editorial"), "tools": ["read", "write"]},
        {"path": str(workspace_root / "assets"), "tools": ["read"]},
    ]
    assert runtime["runtime_extension"] == {"preserve": True}


def test_patch_agent_runtime_clears_only_known_fields(config_store):
    from agency.configuration.patches import (
        AgentRuntimePatch,
        patch_agent_runtime,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    agent["runtime"] = {
        "timeout": 2400,
        "permissions": {
            "rules": [{"path": "/old", "tools": ["shell"]}],
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
            rules=(),
        ),
    )

    runtime = updated.raw["teams"]["newsletter"]["agents"][0]["runtime"]
    assert "timeout" not in runtime
    assert runtime.get("permissions", {}).get("rules") == []
    assert runtime["runtime_extension"] == {"preserve": True}
