from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency.configuration.store import ConfigStore
from agency import app as app_mod


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _make_client(monkeypatch, tmp_path, canonical_raw_config):
    raw = deepcopy(canonical_raw_config)
    raw["agency"]["title"] = "Agency"
    raw["agency"]["default_group"] = "newsletter"
    raw["agency"]["agent_library"] = str(tmp_path / "agent-library")
    raw["agency"]["compilation_cache"] = str(tmp_path / "compiled-agents")
    raw["agency"]["memory_store"] = str(tmp_path / "memory-store")
    raw["groups"]["newsletter"]["path"] = str(tmp_path / "groups" / "newsletter")
    raw["groups"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "sandbox": {"mode": "restricted", "roots": [str(tmp_path / "repo-root")]},
        "tools": {"mode": "allowlist", "names": ["shell"]},
    }
    raw["groups"]["newsletter"]["dispatch"] = {"enabled": True, "daily_limit": 12}
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.reload_groups()
    return TestClient(app_mod.app), ConfigStore(config_path)


def test_group_settings_has_defaults_and_manage_agents_link(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _make_client(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/admin/orgs/newsletter/edit")

    assert response.status_code == 200
    assert "Runtime defaults" in response.text
    assert 'href="/newsletter/agents"' in response.text
    assert "Agent Roster" not in response.text
    assert "Dispatch Schedule" not in response.text
    assert "Auto-detect" not in response.text


def test_stale_group_save_returns_conflict(monkeypatch, tmp_path, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    stale = store.load().revision

    store.patch(
        stale,
        lambda raw: raw["groups"]["newsletter"].__setitem__("name", "Elsewhere"),
    )

    response = client.post(
        "/admin/orgs/newsletter/save",
        data={
            "revision": stale,
            "name": "Newsletter",
            "path": str(tmp_path / "groups" / "newsletter"),
            "default_integration": "claude-code",
            "runtime_timeout": "1800",
            "sandbox_mode": "restricted",
            "sandbox_roots": str(tmp_path / "repo-root"),
            "tool_mode": "allowlist",
            "tool_names": "shell",
            "dispatch_enabled": "on",
            "daily_limit": "12",
            "workspaces_json": "[]",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "reload" in response.text.lower()


def test_setup_post_creates_strict_canonical_group_without_scanning_agents(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.save_config({"agency": {"title": "Agency", "default_group": ""}, "groups": {}})
    app_mod.reload_groups()

    group_path = tmp_path / "groups" / "newsletter"
    library = tmp_path / "agent-library"
    cache = tmp_path / "compiled-agents"
    memory = tmp_path / "memory-store"
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup",
        data={
            "group_key": "newsletter",
            "group_name": "Newsletter",
            "path": str(group_path),
            "agent_library": str(library),
            "compilation_cache": str(cache),
            "memory_store": str(memory),
            "workspace_name": "Terminal Grid",
            "workspace_type": "tmux",
            "workspace_config": '{"script_path": "tmux-agents.sh"}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents"

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["agency"]["default_group"] == "newsletter"
    assert saved["agency"]["agent_library"] == str(library)
    assert saved["agency"]["compilation_cache"] == str(cache)
    assert saved["agency"]["memory_store"] == str(memory)
    assert saved["groups"]["newsletter"]["agents"] == []
    assert not group_path.exists()


def test_setup_page_surfaces_structured_startup_diagnostics(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency:\n  title: Agency\ngroups: {}\n", encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.reload_groups()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert "schema_version" in response.text
    assert "Only schema_version 2 is supported" in response.text