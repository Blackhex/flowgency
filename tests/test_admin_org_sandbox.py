"""Tests for the admin group settings save/create with the permissions model."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.configuration import ConfigStore


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _make_client(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["title"] = "Agency"
    raw["agency"]["default_group"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "library")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "memory")
    raw["groups"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "agents": [],
            "workspaces": [],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), ConfigStore(config_path)


def test_admin_org_save_persists_permission_mode(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "restricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["permissions"]["mode"] == "restricted"


def test_admin_org_save_sets_unrestricted_by_default(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["runtime"] = {
        "permissions": {"mode": "restricted", "rules": [{"path": "/old/root"}]}
    }
    snapshot.path.write_text(yaml.safe_dump(snapshot.raw, sort_keys=False), encoding="utf-8")
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "unrestricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["permissions"]["mode"] == "unrestricted"


def test_admin_org_create_sets_restricted_when_roots_given(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()
    (tmp_path / "repo").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": str(tmp_path / "repo"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["permissions"]["mode"] == "restricted"
    paths = [r.get("path") for r in saved["groups"]["new"]["runtime"]["permissions"]["rules"]]
    assert str(tmp_path / "repo") in paths


def test_admin_org_create_sets_unrestricted_when_no_roots(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["permissions"]["mode"] == "unrestricted"


def test_admin_org_create_multiline_roots(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "cowork").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    rules = saved["groups"]["new"]["runtime"]["permissions"]["rules"]
    paths = [r.get("path") for r in rules]
    assert str(tmp_path / "repo") in paths
    assert str(tmp_path / "cowork") in paths


def test_admin_org_save_preserves_extension_keys(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["group_extension"] = {"theme": "sunset"}
    snapshot.raw["groups"]["grp"]["runtime"] = {
        "timeout": 1200,
        "runtime_extension": {"preserve": True},
        "permissions": {"mode": "unrestricted", "rules": []},
    }
    snapshot.raw["groups"]["grp"]["dispatch"] = {"enabled": False}
    snapshot.raw["groups"]["grp"]["workspaces"] = [
        {
            "name": "Archive",
            "type": "tmux",
            "config": {"script_path": "workspace.sh"},
            "workspace_extension": {"preserve": True},
        }
    ]
    snapshot.path.write_text(yaml.safe_dump(snapshot.raw, sort_keys=False), encoding="utf-8")
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": '[{"name":"Primary","type":"tmux","config":{"script_path":"primary.sh"},"workspace_extension":{"preserve":true}}]',
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "restricted",
            "dispatch_enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["group_extension"] == {"theme": "sunset"}
    assert saved["groups"]["grp"]["runtime"]["runtime_extension"] == {"preserve": True}
    assert saved["groups"]["grp"]["workspaces"][0]["workspace_extension"] == {"preserve": True}


def test_admin_org_save_updates_dispatch_enabled(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "unrestricted",
            "dispatch_enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["dispatch"]["enabled"] is True


def test_admin_org_save_updates_timeout(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "3600",
            "permission_mode": "unrestricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["timeout"] == 3600


def test_admin_org_save_preserves_workspaces(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": '[{"name":"Main","type":"tmux","config":{}}]',
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "unrestricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["workspaces"] == [
        {"name": "Main", "type": "tmux", "config": {}}
    ]
