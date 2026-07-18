"""Tests for web server startup and reload configuration."""

from pathlib import Path

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod


def _configure_existing_config(tmp_path: Path, monkeypatch) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "agency:\n  title: Agency\n  default_group: ''\ngroups: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    return config_path


def test_run_server_normal_mode_uses_in_memory_app(tmp_path, monkeypatch):
    _configure_existing_config(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        app_mod.uvicorn,
        "run",
        lambda application, **options: calls.append((application, options)),
    )

    app_mod.run_server(host="127.0.0.1", port=8600)

    assert calls == [(app_mod.app, {"host": "127.0.0.1", "port": 8600})]


def test_run_server_reload_mode_uses_import_string_and_project_policy(
    tmp_path, monkeypatch
):
    _configure_existing_config(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    events = []

    class FakeConfig:
        def __init__(self, application, **options):
            self.reload_dirs = [Path(path) for path in options["reload_dirs"]]
            events.append(("config", application, options))

        def load_app(self):
            events.append("load_app")

        def bind_socket(self):
            events.append("bind_socket")
            return "socket"

    class FakeServer:
        def __init__(self, config):
            events.append(("server", config))

        def run(self, sockets=None):
            raise AssertionError("worker target must not run in the launcher process")

    class FakeSupervisor:
        def __init__(self, config, target, sockets):
            self.watch_filter = None
            events.append(("supervisor", config, target, sockets))

        def run(self):
            events.append(("run", self.watch_filter))

    monkeypatch.setattr(app_mod.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(app_mod.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(app_mod, "WatchFilesReload", FakeSupervisor)

    app_mod.run_server(host="127.0.0.1", port=8601, reload=True)

    assert events[0] == (
        "config",
        "agency.app:app",
        {
            "host": "127.0.0.1",
            "port": 8601,
            "reload": True,
            "reload_dirs": [str(tmp_path.resolve())],
            "reload_includes": list(app_mod.RELOAD_INCLUDES),
        },
    )
    assert events[1] == "load_app"
    assert events[2][0] == "server"
    assert events[3] == "bind_socket"
    assert events[4][0] == "supervisor"
    assert events[4][3] == ["socket"]
    assert events[5][0] == "run"
    assert isinstance(events[5][1], app_mod._AgencyReloadFilter)
    assert events[5][1].root == tmp_path.resolve()


def test_reload_supervisor_rejects_future_artifacts_at_any_depth(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    config = uvicorn.Config(
        "agency.app:app",
        reload=True,
        reload_dirs=[str(root.resolve())],
        reload_includes=list(app_mod.RELOAD_INCLUDES),
    )
    server = uvicorn.Server(config)
    supervisor = app_mod._create_reload_supervisor(config, server, [])
    assert supervisor.reloader_name == "WatchFiles"

    watched_paths = [
        root / "agency" / "app.py",
        root / "agency" / "templates" / "base.html",
        root / "agency" / "static" / "app.css",
        root / "agency" / "static" / "sw.js",
        root / "agency" / "static" / "manifest.json",
        root / "agency" / "themes" / "workshop.yaml",
        root / "agency" / "themes" / "local.yml",
        root / "config.yaml",
    ]
    excluded_paths = [
        root / "deep" / ".git" / "state.json",
        root / "deep" / ".venv" / "Lib" / "site-packages" / "tool.py",
        root / "deep" / "venv" / "Lib" / "tool.py",
        root / "deep" / "__pycache__" / "module.py",
        root / "deep" / ".pytest_cache" / "state.json",
        root / "deep" / ".mypy_cache" / "state.json",
        root / "deep" / ".ruff_cache" / "state.json",
        root / "deep" / "shared" / "jobs" / "job.yaml",
        root / "deep" / "package.egg-info" / "metadata.json",
    ]
    for path in [*watched_paths, *excluded_paths]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("probe", encoding="utf-8")

    for path in watched_paths:
        assert supervisor.watch_filter(path.resolve()), path
    for path in excluded_paths:
        assert not supervisor.watch_filter(path.resolve()), path

    assert not supervisor.watch_filter((tmp_path / "outside.py").resolve())
    assert not supervisor.watch_filter((root / "README.md").resolve())


def test_reload_filter_uses_directory_components_not_name_fragments(tmp_path):
    reload_filter = app_mod._AgencyReloadFilter(tmp_path.resolve())

    assert reload_filter(tmp_path / "agency" / "shared_config.py")
    assert reload_filter(tmp_path / "agency" / "venv_tools.py")
    assert reload_filter(tmp_path / "agency" / "metadata.egg-info.json")


def test_reload_server_propagates_supervisor_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeConfig:
        def __init__(self, application, **options):
            self.reload_dirs = [Path(path) for path in options["reload_dirs"]]

        def load_app(self):
            pass

        def bind_socket(self):
            return "socket"

    class FakeServer:
        def __init__(self, config):
            pass

        def run(self, sockets=None):
            pass

    class FailingSupervisor:
        def run(self):
            raise RuntimeError("watcher failed")

    monkeypatch.setattr(app_mod.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(app_mod.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(
        app_mod,
        "_create_reload_supervisor",
        lambda config, server, sockets: FailingSupervisor(),
    )

    with pytest.raises(RuntimeError, match="watcher failed"):
        app_mod._run_reload_server("127.0.0.1", 8601)


def test_run_server_reports_first_run_before_starting_uvicorn(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.yaml"
    events = []
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)

    def fake_refresh_services():
        assert not config_path.exists()
        events.append("refresh_services")

    def fake_uvicorn_run(application, **options):
        assert application is app_mod.app
        assert options == {"host": "127.0.0.1", "port": 8602}
        events.append("uvicorn.run")

    monkeypatch.setattr(app_mod, "refresh_services", fake_refresh_services)
    monkeypatch.setattr(app_mod.uvicorn, "run", fake_uvicorn_run)

    app_mod.run_server(host="127.0.0.1", port=8602)

    assert events == ["refresh_services", "uvicorn.run"]
    assert not config_path.exists()
    output = capsys.readouterr().out
    assert (
        f"First run — canonical config not found in {tmp_path}"
        in output
    )
    assert "Visit http://localhost:8602/admin/" in output


def test_setup_get_returns_wizard_when_no_groups_exist(tmp_path, monkeypatch):
    _configure_existing_config(tmp_path, monkeypatch)
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert 'action="/setup"' in response.text
    assert 'name="path"' in response.text


def test_setup_post_invalid_and_empty_agent_paths_render_errors(tmp_path, monkeypatch):
    _configure_existing_config(tmp_path, monkeypatch)
    client = TestClient(app_mod.app)

    invalid_response = client.post("/setup", data={"path": str(tmp_path / "missing")})
    empty_response = client.post(
        "/setup",
        data={
            "group_key": "newsletter",
            "group_name": "Newsletter",
            "path": str(tmp_path / "workspace"),
            "agent_library": str(tmp_path / "library"),
            "compilation_cache": str(tmp_path / "cache"),
            "memory_store": str(tmp_path / "memory"),
            "workspace_name": "Main Workspace",
            "workspace_type": "tmux",
            "workspace_config": "not-json",
        },
    )

    assert invalid_response.status_code == 200
    assert "All setup fields are required for the canonical configuration." in invalid_response.text
    assert empty_response.status_code == 200
    assert "Workspace config must be a JSON object." in empty_response.text


def test_setup_post_valid_path_creates_group_and_redirects(tmp_path, monkeypatch):
    config_path = _configure_existing_config(tmp_path, monkeypatch)
    group_path = tmp_path / "newsletter-agents"
    group_path.mkdir()
    (tmp_path / "library").mkdir()
    client = TestClient(app_mod.app)
    expected_revision = app_mod.build_services(config_path).config_store.inspect().revision

    response = client.post(
        "/setup",
        data={
            "expected_revision": expected_revision,
            "group_key": "newsletter",
            "group_name": "Newsletter",
            "path": str(group_path),
            "agent_library": str(tmp_path / "library"),
            "compilation_cache": str(tmp_path / "cache"),
            "memory_store": str(tmp_path / "memory"),
            "workspace_name": "Terminal Grid",
            "workspace_type": "tmux",
            "workspace_config": '{"script_path": "tmux-agents.sh"}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "agency": {
            "title": "Agency",
            "default_group": "newsletter",
            "ai_backend": "claude-code",
            "agent_library": str(tmp_path / "library"),
            "compilation_cache": str(tmp_path / "cache"),
            "memory_store": str(tmp_path / "memory"),
        },
        "memory": {"channels": {}},
        "groups": {
            "newsletter": {
                "name": "Newsletter",
                "path": str(group_path),
                "default_integration": "claude-code",
                "dispatch": {"enabled": False, "daily_limit": 20},
                "workspaces": [
                    {
                        "name": "Terminal Grid",
                        "type": "tmux",
                        "config": {"script_path": "tmux-agents.sh"},
                    }
                ],
                "agents": [],
            }
        },
    }
    assert group_path.is_dir()
