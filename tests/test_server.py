"""Tests for web server startup and reload configuration."""

from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
import yaml

from agency import app as app_mod
from agency.configuration import ValidationFailed
from agency.integrations import IntegrationError


def _configure_existing_config(tmp_path: Path, monkeypatch) -> Path:
    config_path = tmp_path / "config.yaml"
    (tmp_path / "agent-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "compiled-agents").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory-store").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        (
            "schema_version: 6\n"
            "agency:\n"
            "  title: Agency\n"
            "  default_team: ''\n"
            f"  agent_library: {(tmp_path / 'agent-library').as_posix()}\n"
            f"  compilation_cache: {(tmp_path / 'compiled-agents').as_posix()}\n"
            f"  memory_store: {(tmp_path / 'memory-store').as_posix()}\n"
            f"  prompt_store: {(tmp_path / 'prompts').as_posix()}\n"
            "teams: {}\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    return config_path


def _configure_missing_config(tmp_path: Path, monkeypatch) -> Path:
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.app.state.services = None
    return config_path


def _materialize_ready_config(tmp_path: Path, raw_config: dict) -> dict:
    raw = yaml.safe_load(yaml.safe_dump(raw_config, sort_keys=False))
    library_root = tmp_path / "agent-library"
    compiled_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    prompt_root = tmp_path / "prompts"
    library_root.mkdir(parents=True, exist_ok=True)
    compiled_root.mkdir(parents=True, exist_ok=True)
    memory_root.mkdir(parents=True, exist_ok=True)
    prompt_root.mkdir(parents=True, exist_ok=True)
    raw["agency"]["agent_library"] = str(library_root.resolve())
    raw["agency"]["compilation_cache"] = str(compiled_root.resolve())
    raw["agency"]["memory_store"] = str(memory_root.resolve())
    raw["agency"]["prompt_store"] = str(prompt_root.resolve())
    for group in raw.get("teams", {}).values():
        for agent in group.get("agents", []):
            blueprint_root = library_root / agent["blueprint"]
            blueprint_root.mkdir(parents=True, exist_ok=True)
            (blueprint_root / "AGENTS.md").write_text(f"# {agent['blueprint']}\n", encoding="utf-8")
            prompt_dir = blueprint_root / ".agents" / "prompts"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            for routine in agent.get("routines", []):
                prompt_name = routine.get("prompt", {}).get("name")
                if prompt_name:
                    (prompt_dir / f"{prompt_name}.prompt.md").write_text(
                        f"---\nname: {prompt_name}\ndescription: Routine prompt\n---\n\nRun.\n",
                        encoding="utf-8",
                    )
    return raw


class _LaunchIntegration:
    def __init__(
        self,
        name: str = "copilot",
        display_name: str = "GitHub Copilot",
        *,
        fallback_command: str = "copilot -C C:\\project -i \"prompt\" --name \"Agency setup\"",
        error: Exception | None = None,
        fallback_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self._fallback_command = fallback_command
        self._error = error
        self._fallback_error = fallback_error
        self.requests = []
        self.fallback_requests = []

    def launch_interactive_setup(self, request) -> object:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return type(
            "LaunchResult",
            (),
            {"fallback_command": self._fallback_command},
        )()

    def interactive_setup_fallback_command(self, request) -> str:
        self.fallback_requests.append(request)
        if self._fallback_error is not None:
            raise self._fallback_error
        return self._fallback_command


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

    external_group_log = tmp_path / "agency-data" / "groups" / "newsletter" / "logs" / "run.out"
    external_group_log.parent.mkdir(parents=True)
    external_group_log.write_text("probe", encoding="utf-8")
    assert not supervisor.watch_filter(external_group_log.resolve())


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
        "First run: open http://localhost:8602/setup to launch guided Agency setup."
        in output
    )
    assert "/admin/" not in output
    assert "/setup" in output


def test_setup_get_renders_only_data_root_and_integration_fields(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (
            _LaunchIntegration("copilot", "GitHub Copilot"),
        ),
    )
    response = TestClient(app_mod.app).get("/setup")

    assert response.status_code == 200
    assert "Agency data root" in response.text
    assert "Project folder" not in response.text
    assert 'name="data_root"' in response.text
    assert 'name="project_dir"' not in response.text
    assert 'placeholder="C:\\Agency"' in response.text
    assert 'id="browse-data-root"' in response.text
    assert "Choose Agency data root" in response.text
    assert "Agency data root selected." in response.text


def test_setup_get_redirects_to_dashboard_when_setup_is_ready(
    tmp_path, monkeypatch, raw_config
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        __import__("yaml").safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.app.state.services = None
    client = TestClient(app_mod.app)

    response = client.get("/setup", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_setup_get_rebuilds_services_before_redirect_when_config_appears_out_of_band(
    tmp_path,
    monkeypatch,
    raw_config,
):
    config_path = _configure_missing_config(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        assert app_mod.app.state.services.startup_error is not None
        config_path.write_text(
            __import__("yaml").safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
            encoding="utf-8",
        )

        response = client.get("/setup", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert app_mod.app.state.services.startup_error is None


def test_setup_launch_rejects_relative_data_root(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (_LaunchIntegration(),),
    )
    response = TestClient(app_mod.app).post(
        "/setup/launch",
        data={"data_root": "relative/Agency", "integration": "copilot"},
    )

    assert response.status_code == 200
    assert "Agency data root must be an absolute path." in response.text


def test_setup_launch_rejects_unavailable_integration(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (_LaunchIntegration("claude-code", "Claude Code"),),
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root.resolve()), "integration": "copilot"},
    )

    assert response.status_code == 200
    assert "Choose an available integration." in response.text


def test_setup_launch_does_not_write_config(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )

    async def fake_run_in_threadpool(func, *args, **kwargs):
        assert getattr(func, "__self__", None) is integration
        assert getattr(func, "__name__", "") == "launch_interactive_setup"
        assert len(args) == 1
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "agency.web.routes.admin_teams.run_in_threadpool",
        fake_run_in_threadpool,
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root.resolve()), "integration": "copilot"},
    )

    assert response.status_code == 200
    assert not config_path.exists()
    assert "Waiting for setup to complete" in response.text
    assert "setTimeout(" in response.text
    assert "setInterval(" not in response.text
    assert integration.requests[0].data_root == data_root.resolve()
    assert not hasattr(integration.requests[0], "project_dir")
    assert integration.requests[0].config_path == config_path.resolve()
    assert "agency-setup" in integration.requests[0].prompt
    assert "Selected integration: copilot." in integration.requests[0].prompt
    assert (
        "carry inspected project facts and every approved setup answer forward"
        in integration.requests[0].prompt
    )
    assert (
        "The canonical config remains the only setup completion output"
        in integration.requests[0].prompt
    )
    assert (
        "After one consolidated team approval, ask `Customize the derived storage paths?` once."
        in integration.requests[0].prompt
    )
    assert "After the group ID is approved, ask" not in integration.requests[0].prompt
    assert (
        "consolidated team approval covers complete operating profiles"
        in integration.requests[0].prompt
    )
    assert (
        "Storage paths are approved afterward"
        in integration.requests[0].prompt
    )
    assert (
        "every exact profile label"
        not in integration.requests[0].prompt
    )
    assert (
        "semantic categories in any clear layout"
        in integration.requests[0].prompt
    )
    assert (
        "verify" in integration.requests[0].prompt
    )
    assert (
        "clear theme"
        in integration.requests[0].prompt
    )
    assert integration.fallback_requests == []

def test_setup_launch_uses_integration_owned_fallback_when_launch_fails(
    tmp_path, monkeypatch
):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    integration = _LaunchIntegration(
        name="custom-launcher",
        display_name="Custom Launcher",
        fallback_command="custom-launcher --resume-setup",
        error=IntegrationError("Launch failed."),
    )
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )

    async def fake_run_in_threadpool(func, *args, **kwargs):
        assert getattr(func, "__self__", None) is integration
        assert getattr(func, "__name__", "") == "launch_interactive_setup"
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "agency.web.routes.admin_teams.run_in_threadpool",
        fake_run_in_threadpool,
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
            "integration": "custom-launcher",
        },
    )

    assert response.status_code == 200
    assert "Waiting for setup to complete" in response.text
    assert "custom-launcher --resume-setup" in response.text
    assert "copilot -C" not in response.text
    assert integration.fallback_requests == integration.requests


def test_setup_launch_creates_and_uses_missing_data_root(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "new" / "Agency"
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot"},
    )

    assert response.status_code == 200
    assert data_root.is_dir()
    assert list(data_root.iterdir()) == []
    assert not config_path.exists()
    assert integration.requests[0].data_root == data_root.resolve(strict=True)
    assert "Waiting for setup to complete" in response.text
    assert "Agency data root" in response.text


def test_setup_launch_returns_to_form_when_launch_and_fallback_fail(
    tmp_path, monkeypatch
):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Agency"
    integration = _LaunchIntegration(
        error=IntegrationError("Bundled setup skill is unavailable."),
        fallback_error=IntegrationError("No valid fallback command."),
    )
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )

    response = TestClient(app_mod.app).post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot"},
    )

    assert response.status_code == 200
    assert data_root.is_dir()
    assert "Bundled setup skill is unavailable." in response.text
    assert "Waiting for setup to complete" not in response.text


def test_setup_browse_returns_directory_listing(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    selected = tmp_path / "chosen"
    selected.mkdir()
    (selected / "Beta").mkdir()
    (selected / "alpha").mkdir()
    client = TestClient(app_mod.app, client=("127.0.0.1", 50000))

    response = client.post("/setup/browse", data={"path": str(selected)})

    assert response.status_code == 200
    assert response.json() == {
        "path": str(selected.resolve()),
        "parent": str(selected.resolve().parent),
        "roots": [str(selected.resolve().anchor)],
        "directories": [
            {
                "name": "alpha",
                "path": str((selected / "alpha").resolve()),
            },
            {
                "name": "Beta",
                "path": str((selected / "Beta").resolve()),
            },
        ],
    }


def test_setup_browse_rejects_invalid_path(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    client = TestClient(app_mod.app, client=("127.0.0.1", 50000))

    response = client.post("/setup/browse", data={"path": "relative"})

    assert response.status_code == 400
    assert response.json() == {
        "error": "Choose an absolute directory.",
    }


def test_setup_browse_rejects_non_loopback_clients(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    client = TestClient(app_mod.app, client=("192.0.2.1", 50000))

    response = client.post("/setup/browse", data={"path": str(tmp_path)})

    assert response.status_code == 403
    assert response.json() == {
        "error": "Folder browsing is available only from this computer.",
    }


def test_setup_status_returns_waiting_when_config_is_absent(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    client = TestClient(app_mod.app)

    response = client.get("/setup/status")

    assert response.status_code == 200
    assert response.json() == {"state": "waiting"}


def test_setup_status_returns_invalid_with_message(tmp_path, monkeypatch, raw_config):
    config_path = tmp_path / "config.yaml"
    raw_config = _materialize_ready_config(tmp_path, raw_config)
    raw_config["teams"]["newsletter"]["default_integration"] = ""
    config_path.write_text(
        __import__("yaml").safe_dump(raw_config, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.app.state.services = None
    client = TestClient(app_mod.app)

    response = client.get("/setup/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "invalid",
        "message": "Team default integration is required.",
    }


def test_setup_status_returns_incomplete_when_config_has_no_groups(
    tmp_path, monkeypatch, raw_config
):
    config_path = tmp_path / "config.yaml"
    raw_config = _materialize_ready_config(tmp_path, raw_config)
    raw_config["agency"]["default_team"] = ""
    raw_config["teams"] = {}
    config_path.write_text(
        __import__("yaml").safe_dump(raw_config, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.app.state.services = None
    client = TestClient(app_mod.app)

    response = client.get("/setup/status")

    assert response.status_code == 200
    assert response.json() == {"state": "incomplete"}


def test_setup_status_redirect_target_is_dashboard_when_ready(
    tmp_path, monkeypatch, raw_config
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        __import__("yaml").safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.app.state.services = None
    client = TestClient(app_mod.app)

    response = client.get("/setup/status")

    assert response.status_code == 200
    assert response.json() == {"state": "ready", "redirect": "/"}


def test_setup_status_rebuilds_services_after_out_of_band_config_write(
    tmp_path,
    monkeypatch,
    raw_config,
):
    config_path = _configure_missing_config(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        assert app_mod.app.state.services.startup_error is not None
        config_path.write_text(
            __import__("yaml").safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
            encoding="utf-8",
        )

        status = client.get("/setup/status")
        root = client.get("/", follow_redirects=False)

    assert status.status_code == 200
    assert status.json() == {"state": "ready", "redirect": "/"}
    assert app_mod.app.state.services.startup_error is None
    assert root.status_code == 303
    assert root.headers["location"] == "/newsletter/"


def test_setup_status_returns_non_ready_when_rebuilt_services_still_fail(
    tmp_path,
    monkeypatch,
    raw_config,
):
    config_path = _configure_missing_config(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        assert app_mod.app.state.services.startup_error is not None
        raw_config = _materialize_ready_config(tmp_path, raw_config)
        config_path.write_text(
            __import__("yaml").safe_dump(raw_config, sort_keys=False),
            encoding="utf-8",
        )

        def fake_build_services(path: Path):
            fresh = app_mod.build_services(path)
            return replace(
                fresh,
                blueprint_library=None,
                compilation_cache=None,
                memory_store=None,
                job_store=None,
                instances=None,
                startup_error=RuntimeError("services still unavailable"),
            )

        monkeypatch.setattr(app_mod.app.state, "build_services", fake_build_services)

        response = client.get("/setup/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "invalid",
        "message": "Services could not start: services still unavailable",
    }


def test_build_services_validates_before_initializing_storage(
    tmp_path, raw_config
):
    raw = dict(raw_config)
    raw["agency"] = dict(raw_config["agency"])
    raw["teams"] = dict(raw_config["teams"])
    raw["teams"]["newsletter"] = dict(raw_config["teams"]["newsletter"])
    raw["teams"]["newsletter"]["workspace_path"] = str(
        tmp_path / "missing-workspace"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    services = app_mod.build_services(config_path)

    assert isinstance(services.startup_error, ValidationFailed)
    assert not (tmp_path / "compiled-agents").exists()
    assert not (tmp_path / "memory").exists()
    assert not (tmp_path / "teams" / "newsletter").exists()
