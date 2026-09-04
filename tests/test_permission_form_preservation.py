"""Regression tests for C1 and C2: forms must not erase permission rules."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.configuration import ConfigStore
from agency.configuration.patches import (
    AgentRuntimePatch,
    TeamSettingsStatePatch,
    patch_team_settings_state,
)


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _make_team_client(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    src = workspace / "src"
    src.mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["title"] = "Agency"
    raw["agency"]["default_team"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "library")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "memory")
    raw["teams"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(workspace),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "runtime": {
                "timeout": 1800,
                "permissions": {
                    "mode": "restricted",
                    "rules": [
                        {"path": str(src), "tools": ["read", "search"]},
                    ],
                },
            },
            "agents": [],
            "workspaces": [],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), ConfigStore(config_path)


def _write_blueprint(root: Path, key: str) -> None:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    prompt_dir = blueprint / ".agents" / "prompts"
    skill.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {key}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _make_agent_client(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    extra = tmp_path / "extra"
    extra.mkdir(parents=True, exist_ok=True)
    team_root = tmp_path / "groups" / "newsletter"
    (team_root / "logs").mkdir(parents=True, exist_ok=True)
    (team_root / "observations").mkdir(parents=True, exist_ok=True)
    (team_root / "proposals").mkdir(parents=True, exist_ok=True)
    (team_root / "decisions").mkdir(parents=True, exist_ok=True)
    (team_root / "locks").mkdir(parents=True, exist_ok=True)
    _write_blueprint(library_root, "advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(tmp_path / "compiled-agents")
    raw["agency"]["memory_store"] = str(tmp_path / "memory-store")
    raw["agency"]["prompt_store"] = str(tmp_path / "prompts")
    raw["teams"]["newsletter"]["name"] = "Newsletter"
    raw["teams"]["newsletter"]["path"] = str(team_root)
    raw["teams"]["newsletter"]["workspace_path"] = str(workspace)
    raw["teams"]["newsletter"]["default_integration"] = "copilot"
    raw["teams"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "permissions": {
            "mode": "restricted",
            "rules": [
                {"path": str(workspace), "tools": ["read", "search"]},
            ],
        },
    }
    raw["teams"]["newsletter"]["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "identity": {"display_name": "Advisor", "title": "Test", "emoji": ""},
            "runtime": {
                "timeout": 1200,
                "permissions": {
                    "rules": [
                        {"path": str(extra), "tools": ["read", "search"]},
                    ],
                },
            },
        }
    ]
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), config_path


# ---------- C1: Group settings form preserves rules ----------


def test_team_save_unrelated_field_preserves_rules(tmp_path, monkeypatch, raw_config):
    """Editing only the team name must not touch permission mode or rules."""
    client, store = _make_team_client(monkeypatch, tmp_path, raw_config)
    before = deepcopy(store.load().raw["teams"]["grp"]["runtime"]["permissions"])
    revision = store.load().revision

    response = client.post(
        "/admin/teams/grp/save",
        data={
            "revision": revision,
            "name": "Renamed Group",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            # No permission_mode or permission_rules_yaml — leave unchanged.
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    after = store.load().raw["teams"]["grp"]["runtime"]["permissions"]
    assert after == before


def test_team_save_with_form_fields_preserves_rules(tmp_path, monkeypatch, raw_config):
    """Posting the form including permission fields round-trips rules."""
    client, store = _make_team_client(monkeypatch, tmp_path, raw_config)
    before = deepcopy(store.load().raw["teams"]["grp"]["runtime"]["permissions"])
    revision = store.load().revision
    src = tmp_path / "workspace" / "src"
    rules_yaml = yaml.safe_dump(
        [{"path": str(src), "tools": ["read", "search"]}],
        default_flow_style=False, sort_keys=False,
    ).strip()

    response = client.post(
        "/admin/teams/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "restricted",
            "permission_rules_yaml": rules_yaml,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    after = store.load().raw["teams"]["grp"]["runtime"]["permissions"]
    assert after == before


# ---------- C2: Agent runtime form preserves rules ----------


def test_agent_runtime_timeout_only_preserves_rules(tmp_path, monkeypatch, raw_config):
    """Changing only timeout must not destroy the instance's permission rules."""
    client, config_path = _make_agent_client(monkeypatch, tmp_path, raw_config)
    before = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    before_rules = deepcopy(before["teams"]["newsletter"]["agents"][0]["runtime"]["permissions"])
    revision = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    from agency.configuration import ConfigStore
    rev = ConfigStore(config_path).load().revision

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": rev,
            "timeout": "1801",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    runtime = saved["teams"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1801
    assert runtime["permissions"] == before_rules


def test_agent_runtime_form_round_trips_rules(tmp_path, monkeypatch, raw_config):
    """Submitting rules through the form round-trips them unchanged."""
    client, config_path = _make_agent_client(monkeypatch, tmp_path, raw_config)
    before = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    before_rules = before["teams"]["newsletter"]["agents"][0]["runtime"]["permissions"]["rules"]
    from agency.configuration import ConfigStore
    rev = ConfigStore(config_path).load().revision
    extra = tmp_path / "extra"
    rules_yaml = yaml.safe_dump(
        [{"path": str(extra), "tools": ["read", "search"]}],
        default_flow_style=False, sort_keys=False,
    ).strip()

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": rev,
            "timeout": "1200",
            "permission_rules_yaml": rules_yaml,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    runtime = saved["teams"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["permissions"]["rules"] == before_rules


# ---------- N3: Group create form persists what the operator typed ----------


def test_team_create_persists_typed_permission_rules(tmp_path, monkeypatch, raw_config):
    """The New Group form posts the same permission fields as the edit form.
    What the operator types must reach the configuration, not be discarded."""
    client, store = _make_team_client(monkeypatch, tmp_path, raw_config)
    new_workspace = tmp_path / "new-workspace"
    (new_workspace / "src").mkdir(parents=True)
    rules_yaml = yaml.safe_dump(
        [{"path": str(new_workspace / "src"), "tools": ["read", "search"]}],
        default_flow_style=False, sort_keys=False,
    ).strip()

    response = client.post(
        "/admin/teams/create",
        data={
            "revision": store.load().revision,
            "key": "fresh",
            "name": "Fresh",
            "workspace_path": str(new_workspace),
            "path": str(tmp_path / "groups" / "fresh-state"),
            "default_integration": "copilot",
            "workspaces_json": "[]",
            "permission_mode": "restricted",
            "permission_rules_yaml": rules_yaml,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    permissions = store.load().raw["teams"]["fresh"]["runtime"]["permissions"]
    assert permissions["mode"] == "restricted"
    assert permissions["rules"] == [
        {"path": str(new_workspace / "src"), "tools": ["read", "search"]}
    ]


def test_team_create_rejects_malformed_permission_rules(tmp_path, monkeypatch, raw_config):
    """Create and save share the parsing, so both refuse the same input."""
    client, store = _make_team_client(monkeypatch, tmp_path, raw_config)
    new_workspace = tmp_path / "new-workspace"
    new_workspace.mkdir(parents=True)

    response = client.post(
        "/admin/teams/create",
        data={
            "revision": store.load().revision,
            "key": "fresh",
            "name": "Fresh",
            "workspace_path": str(new_workspace),
            "path": str(tmp_path / "groups" / "fresh-state"),
            "default_integration": "copilot",
            "workspaces_json": "[]",
            "permission_mode": "restricted",
            "permission_rules_yaml": "not: [a, list",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "fresh" not in store.load().raw["teams"]


def test_team_create_without_permission_fields_is_unrestricted(tmp_path, monkeypatch, raw_config):
    """An operator who types nothing gets the documented default, not a
    silently discarded entry."""
    client, store = _make_team_client(monkeypatch, tmp_path, raw_config)
    new_workspace = tmp_path / "new-workspace"
    new_workspace.mkdir(parents=True)

    response = client.post(
        "/admin/teams/create",
        data={
            "revision": store.load().revision,
            "key": "fresh",
            "name": "Fresh",
            "workspace_path": str(new_workspace),
            "path": str(tmp_path / "groups" / "fresh-state"),
            "default_integration": "copilot",
            "workspaces_json": "[]",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    permissions = store.load().raw["teams"]["fresh"]["runtime"]["permissions"]
    assert permissions["mode"] == "unrestricted"
    assert permissions["rules"] == []


# ---------- Sentinel semantics: None vs () ----------


def test_team_patch_none_leaves_rules_alone(tmp_path, raw_config):
    """TeamSettingsStatePatch with permission_rules=None preserves existing rules."""
    raw = deepcopy(raw_config)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (tmp_path / "lib").mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["default_team"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "lib")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "mem")
    raw["teams"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(workspace),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "runtime": {
                "timeout": 1800,
                "permissions": {
                    "mode": "restricted",
                    "rules": [{"path": str(workspace), "tools": ["read"]}],
                },
            },
            "agents": [],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    store = ConfigStore(config_path)
    revision = store.load().revision

    patch_team_settings_state(
        store, revision, "grp",
        TeamSettingsStatePatch(
            name="Grp",
            workspace_path=str(workspace),
            path=str(tmp_path / "groups" / "grp-state"),
            default_integration="copilot",
            runtime_timeout=1800,
            permission_mode=None,
            permission_rules=None,
        ),
    )

    saved = store.load().raw["teams"]["grp"]["runtime"]["permissions"]
    assert saved["mode"] == "restricted"
    assert saved["rules"] == [{"path": str(workspace), "tools": ["read"]}]


def test_team_patch_empty_tuple_clears_rules(tmp_path, raw_config):
    """TeamSettingsStatePatch with permission_rules=() explicitly clears rules."""
    raw = deepcopy(raw_config)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (tmp_path / "lib").mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["default_team"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "lib")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "mem")
    raw["teams"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(workspace),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "runtime": {
                "timeout": 1800,
                "permissions": {
                    "mode": "restricted",
                    "rules": [{"path": str(workspace), "tools": ["read"]}],
                },
            },
            "agents": [],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    store = ConfigStore(config_path)
    revision = store.load().revision

    patch_team_settings_state(
        store, revision, "grp",
        TeamSettingsStatePatch(
            name="Grp",
            workspace_path=str(workspace),
            path=str(tmp_path / "groups" / "grp-state"),
            default_integration="copilot",
            runtime_timeout=1800,
            permission_mode="unrestricted",
            permission_rules=(),
        ),
    )

    saved = store.load().raw["teams"]["grp"]["runtime"]["permissions"]
    assert saved["mode"] == "unrestricted"
    assert saved["rules"] == []


def test_agent_patch_none_leaves_rules_alone(tmp_path, raw_config):
    """AgentRuntimePatch with rules=None preserves existing agent rules."""
    raw = deepcopy(raw_config)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    extra = tmp_path / "extra"
    extra.mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["default_team"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "lib")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "mem")
    raw["teams"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(workspace),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "runtime": {
                "timeout": 1800,
                "permissions": {"mode": "restricted", "rules": [{"path": str(workspace), "tools": ["read"]}]},
            },
            "agents": [
                {
                    "name": "bot",
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "runtime": {
                        "timeout": 900,
                        "permissions": {"rules": [{"path": str(extra), "tools": ["read", "write"]}]},
                    },
                }
            ],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)

    from copy import deepcopy as dc
    from agency.web.routes.agent_detail import _apply_runtime_patch
    raw_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    _apply_runtime_patch(raw_data, "grp", "bot", AgentRuntimePatch(timeout=1801, rules=None))

    agent_runtime = raw_data["teams"]["grp"]["agents"][0]["runtime"]
    assert agent_runtime["timeout"] == 1801
    assert agent_runtime["permissions"]["rules"] == [{"path": str(extra), "tools": ["read", "write"]}]


def test_agent_patch_empty_tuple_clears_rules(tmp_path, raw_config):
    """AgentRuntimePatch with rules=() explicitly sets rules to empty."""
    raw = deepcopy(raw_config)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    extra = tmp_path / "extra"
    extra.mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["default_team"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "lib")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "mem")
    raw["teams"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(workspace),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "runtime": {
                "timeout": 1800,
                "permissions": {"mode": "restricted", "rules": [{"path": str(workspace), "tools": ["read"]}]},
            },
            "agents": [
                {
                    "name": "bot",
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "runtime": {
                        "timeout": 900,
                        "permissions": {"rules": [{"path": str(extra), "tools": ["read", "write"]}]},
                    },
                }
            ],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)

    from agency.web.routes.agent_detail import _apply_runtime_patch
    raw_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    _apply_runtime_patch(raw_data, "grp", "bot", AgentRuntimePatch(timeout=900, rules=()))

    agent_runtime = raw_data["teams"]["grp"]["agents"][0]["runtime"]
    assert agent_runtime["permissions"]["rules"] == []
