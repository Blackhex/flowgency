from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod


def _write_blueprint(root: Path, key: str, title: str) -> None:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _status(state="inactive", installed=False, conflict=False, mismatches=None):
    return {
        "state": state,
        "installed": installed,
        "enabled": state == "active",
        "timer_active": state == "active",
        "definition_matches": installed and state != "misconfigured",
        "config_conflict": conflict,
        "config_path": "C:/other/config.yaml" if conflict else None,
        "interval": 15 if installed else None,
        "expected_config_path": "C:/agency/config.yaml",
        "expected_interval": 15,
        "mismatches": list(mismatches or []),
        "error": None,
    }


def _configure_admin(tmp_path: Path, monkeypatch, scheduler_status):
    group_path = tmp_path / "agents"
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    (group_path / "shared" / "prompts").mkdir(parents=True)
    (group_path / "shared" / "prompts" / "routine.md").write_text("# Routine\n", encoding="utf-8")
    _write_blueprint(library_root, "advisor", "Advisor")
    config_path = tmp_path / "config.yaml"
    config = {
        "schema_version": 2,
        "agency": {
            "title": "Agency",
            "default_group": "test",
            "ai_backend": "copilot",
            "agent_library": str(library_root),
            "compilation_cache": str(cache_root),
            "memory_store": str(memory_root),
            "dispatch": {"interval": 15},
        },
        "memory": {"channels": {}},
        "groups": {
            "test": {
                "name": "Test Agents",
                "path": str(group_path),
                "default_integration": "copilot",
                "runtime": {
                    "timeout": 1800,
                    "sandbox": {"mode": "restricted", "roots": [str(group_path / "shared")]},
                    "tools": {"mode": "allowlist", "names": ["shell"]},
                },
                "agents": [
                    {
                        "name": "product",
                        "blueprint": "advisor",
                        "integration": "copilot",
                    }
                ],
                "dispatch": {
                    "enabled": True,
                    "daily_limit": 15,
                },
                "workspaces": [],
            },
        },
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    monkeypatch.setattr(app_mod, "_get_timer_status", lambda path, interval: scheduler_status)
    app_mod.reload_groups()
    return TestClient(app_mod.app)


def test_dispatch_status_ignores_persisted_installed_flag(tmp_path, monkeypatch):
    _configure_admin(tmp_path, monkeypatch, _status())
    config = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    config["agency"]["dispatch"]["installed"] = True
    app_mod.CONFIG_PATH.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    status = app_mod.get_dispatch_status()
    assert status["installed"] is False
    assert status["state"] == "inactive"


def test_group_page_labels_config_as_schedule_enabled(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    response = client.get("/admin/groups")
    assert response.status_code == 200
    assert "Schedule enabled" in response.text
    assert "Dispatch on" not in response.text


def test_group_schedule_controls_remain_visible_when_dispatcher_inactive(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    response = client.get("/admin/orgs/test/edit")
    assert response.status_code == 200
    assert "Runtime defaults" in response.text
    assert "Dispatch enabled" in response.text
    assert "Daily Limit" in response.text
    assert "Dispatch Schedule" not in response.text
    assert "Save Dispatch Config" not in response.text
    assert "Manage agents (1)" in response.text


def test_dispatch_page_uses_platform_neutral_inactive_copy(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    response = client.get("/admin/dispatch")
    assert response.status_code == 200
    assert "Dispatcher inactive" in response.text
    assert "Set Up Dispatcher" in response.text
    assert "system scheduler" in response.text
    assert "systemd timer" not in response.text


def test_dispatch_page_shows_runtime_inspection_error(tmp_path, monkeypatch):
    failed_status = _status()
    failed_status["error"] = "Task Scheduler service is unavailable"
    client = _configure_admin(tmp_path, monkeypatch, failed_status)
    response = client.get("/admin/dispatch")
    assert response.status_code == 200
    assert "Dispatcher inactive" in response.text
    assert "Task Scheduler service is unavailable" in response.text


def test_dispatch_page_shows_guarded_conflict_repair(tmp_path, monkeypatch):
    client = _configure_admin(
        tmp_path,
        monkeypatch,
        _status(
            state="misconfigured",
            installed=True,
            conflict=True,
            mismatches=["config_path", "interval"],
        ),
    )
    response = client.get("/admin/dispatch")
    assert response.status_code == 200
    assert "Dispatcher misconfigured" in response.text
    assert "config_path" in response.text
    assert "interval" in response.text
    assert "Repair Dispatcher" in response.text
    assert 'name="replace" value="true"' in response.text


def test_dispatch_install_route_forwards_explicit_replacement(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    calls = []
    monkeypatch.setattr(
        app_mod,
        "install_dispatch",
        lambda interval=None, replace=False: calls.append((interval, replace)),
    )
    response = client.post(
        "/admin/dispatch/install",
        data={"replace": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls == [(None, True)]


def test_interval_update_repairs_dispatcher_through_shared_api(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status(state="active", installed=True))
    calls = []
    monkeypatch.setattr(
        app_mod,
        "install_timer",
        lambda path, interval, replace=False: calls.append((path, interval, replace)),
    )
    response = client.post(
        "/admin/settings",
        data={
            "title": "Agency",
            "default_group": "test",
            "ai_backend": "copilot",
            "theme": "",
            "dispatch_interval": "30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls == [(str(app_mod.CONFIG_PATH.resolve()), 30, False)]
    saved = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["agency"]["dispatch"]["interval"] == 30
    assert "installed" not in saved["agency"]["dispatch"]


def test_interval_update_returns_409_when_inspection_error(tmp_path, monkeypatch):
    """Prove interval POST receiving inspection error renders status 409 with error text."""
    failed_status = _status()
    failed_status["error"] = "Task Scheduler service is unavailable"
    client = _configure_admin(tmp_path, monkeypatch, failed_status)
    response = client.post(
        "/admin/settings",
        data={
            "title": "Agency",
            "default_group": "test",
            "ai_backend": "copilot",
            "theme": "",
            "dispatch_interval": "30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "Task Scheduler service is unavailable" in response.text


def test_admin_groups_card_layout_stacks_on_mobile(tmp_path, monkeypatch):
    """Group card outer layout must stack content above actions on mobile, return to row at sm."""
    client = _configure_admin(tmp_path, monkeypatch, _status(state="active", installed=True))
    response = client.get("/admin/groups")
    assert response.status_code == 200
    # Outer card layout must stack on mobile: flex flex-col on base, flex-row at sm
    assert 'flex flex-col' in response.text and 'sm:flex-row' in response.text
    # Content area must allow text wrapping with min-w-0
    assert 'min-w-0' in response.text
    # Path text must break on mobile
    assert 'break-all' in response.text or 'break-words' in response.text
    # Status badges must wrap
    assert 'flex-wrap' in response.text
    # Actions must not have margin-left on mobile (only at sm+)
    assert 'sm:ml-4' in response.text
    assert 'class="flex items-start justify-between"' not in response.text  # Old layout


def test_admin_org_edit_schedule_rules_use_mobile_responsive_grid(tmp_path, monkeypatch):
    """Group settings no longer render the superseded per-agent schedule rule editor."""
    client = _configure_admin(tmp_path, monkeypatch, _status(state="active", installed=True))
    response = client.get("/admin/orgs/test/edit")
    assert response.status_code == 200
    assert "Dispatch Schedule" not in response.text
    assert "Save Dispatch Config" not in response.text
    assert "Agent roster management moved to the group roster page" in response.text
    assert 'href="/test/agents"' in response.text


def test_admin_org_edit_preserves_selected_theme(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status(state="active", installed=True))
    config = app_mod.load_config()
    config["agency"]["theme"] = "workshop"
    app_mod.save_config(config)
    app_mod.reload_groups()

    response = client.get("/admin/orgs/test/edit")

    assert response.status_code == 200
    assert "/* Theme: Workshop */" in response.text
