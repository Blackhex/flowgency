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
    from flowgency.configuration.store import ConfigStore

    path = _write_yaml(tmp_path / "config.yaml", raw_config)
    return ConfigStore(path)


def test_routine_patch_round_trips_the_recovery_bound(config_store):
    from flowgency.configuration.patches import replace_agent_routines

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
    from flowgency.configuration.patches import (
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
    from flowgency.configuration.patches import (
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


def test_patch_team_settings_preserves_unowned_team_fields(config_store):
    from flowgency.configuration.patches import (
        TeamSettingsPatch,
        patch_team_settings,
    )

    snapshot = config_store.load()
    snapshot.raw["teams"]["newsletter"]["ui_extension"] = {"theme": "sunset"}
    snapshot.raw["teams"]["newsletter"]["runtime"] = {
        "timeout": 2400,
    }
    snapshot.raw["teams"]["newsletter"]["permissions"] = {"mode": "unrestricted"}
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
    from flowgency.configuration.patches import (
        TeamSettingsStatePatch,
        patch_team_settings_state,
    )

    snapshot = config_store.load()
    snapshot.raw["teams"]["newsletter"]["team_extension"] = {"theme": "sunset"}
    snapshot.raw["teams"]["newsletter"]["runtime"] = {
        "timeout": 1200,
        "runtime_extension": {"preserve": True},
    }
    snapshot.raw["teams"]["newsletter"]["permissions"] = {
        "mode": "restricted",
        "rules": [{"path": "shared-root", "tools": ["shell"]}],
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
    assert team["team_extension"] == {"theme": "sunset"}
    assert team["runtime"]["runtime_extension"] == {"preserve": True}
    assert team["workspaces"][0]["workspace_extension"] == {"preserve": True}


def test_patch_agent_runtime_merges_integration_config_without_dropping_existing_keys(config_store):
    from flowgency.configuration.patches import AgentRuntimePatch, patch_agent_runtime

    snapshot = config_store.load()
    snapshot.raw["teams"]["newsletter"]["agents"][0]["integration"] = "copilot"
    snapshot.raw["teams"]["newsletter"]["agents"][0]["integration_config"] = {
        "model": "gpt-5.4"
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
            timeout=1801,
            integration_config={"allow_local_network": False},
        ),
    )

    agent = updated.raw["teams"]["newsletter"]["agents"][0]
    assert agent["runtime"]["timeout"] == 1801
    assert agent["integration_config"] == {
        "model": "gpt-5.4",
        "allow_local_network": False,
    }


def test_create_team_rejects_unknown_root_key_on_load(
    config_store,
):
    from flowgency.configuration import ValidationFailed

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
    from flowgency.configuration.patches import TeamCreateStatePatch, create_team_state

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
    from flowgency.configuration import ValidationFailed
    from flowgency.configuration.patches import patch_memory_channels

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
    from flowgency.configuration.patches import (
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
    from flowgency.configuration.patches import (
        AgentRuntimePatch,
        patch_agent_runtime,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    agent["runtime"] = {
        "timeout": 900,
        "runtime_extension": {"preserve": True},
    }
    agent["permissions"] = {
        "rules": [{"path": "/shared-root", "tools": ["shell"]}],
    }
    snapshot.path.write_text(
        yaml.safe_dump(snapshot.raw, sort_keys=False),
        encoding="utf-8",
    )
    workspace_root = Path(snapshot.raw["teams"]["newsletter"]["workspace_path"])
    shared_root = workspace_root / "shared-root"
    shared_root.mkdir(parents=True, exist_ok=True)
    agent["permissions"] = {
        "rules": [{"path": str(shared_root), "tools": ["shell"]}],
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
        ),
    )

    runtime = updated.raw["teams"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1200
    assert updated.raw["teams"]["newsletter"]["agents"][0]["permissions"]["rules"] == [
        {"path": str(shared_root), "tools": ["shell"]},
    ]
    assert runtime["runtime_extension"] == {"preserve": True}


def test_patch_agent_runtime_clears_only_known_fields(config_store):
    from flowgency.configuration.patches import (
        AgentRuntimePatch,
        patch_agent_runtime,
    )

    snapshot = config_store.load()
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    agent["runtime"] = {
        "timeout": 2400,
        "runtime_extension": {"preserve": True},
    }
    agent["permissions"] = {
        "rules": [{"path": str(snapshot.path.parent / "old"), "tools": ["shell"]}],
    }
    (snapshot.path.parent / "old").mkdir(parents=True, exist_ok=True)
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
        ),
    )

    runtime = updated.raw["teams"]["newsletter"]["agents"][0]["runtime"]
    assert "timeout" not in runtime
    assert updated.raw["teams"]["newsletter"]["agents"][0].get("permissions", {}).get("rules") == [
        {"path": str(snapshot.path.parent / "old"), "tools": ["shell"]}
    ]
    assert runtime["runtime_extension"] == {"preserve": True}


def test_patch_workflow_instance_advances_generation_only_on_selection_change(
    tmp_path, raw_config
):
    from copy import deepcopy

    from flowgency.configuration.store import ConfigStore
    from flowgency.workflows.configuration import (
        WorkflowInstancePatch,
        patch_workflow_instance,
    )

    library = tmp_path / "workflow-library"
    library.mkdir()
    root_a = tmp_path / "tickets-a"
    root_a.mkdir()
    root_b = tmp_path / "tickets-b"
    root_b.mkdir()
    raw = deepcopy(raw_config)
    raw["flowgency"]["workflow_library"] = str(library)
    store = ConfigStore(_write_yaml(tmp_path / "config.yaml", raw))

    snapshot = store.load()
    created = patch_workflow_instance(
        store,
        snapshot.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch("Delivery", "blueprint-one", "local", {"root": str(root_a)}),
        create=True,
    )
    workflow = created.config.teams["newsletter"].workflows["workflow-one"]
    assert workflow.context_generation == 0

    switched = patch_workflow_instance(
        store,
        created.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch("Delivery", "blueprint-one", "local", {"root": str(root_b)}),
    )
    assert (
        switched.config.teams["newsletter"].workflows["workflow-one"].context_generation
        == 1
    )

    renamed = patch_workflow_instance(
        store,
        switched.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch("Renamed", "blueprint-one", "local", {"root": str(root_b)}),
    )
    assert (
        renamed.config.teams["newsletter"].workflows["workflow-one"].context_generation
        == 1
    )
    # The stored raw board keeps the authored relative root untouched.
    assert (
        renamed.raw["teams"]["newsletter"]["workflows"]["workflow-one"]["name"]
        == "Renamed"
    )
