"""Tests for web server startup and reload configuration."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
import yaml

from flowgency import app as app_mod
from flowgency.configuration import ValidationFailed
from flowgency.integrations import IntegrationError
from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import ConnectedLaunchError
from flowgency.web.setup_sessions import SetupSessionConflict, SetupSessionManager
from tests._connected_setup_helpers import FakeProcess

_LOCAL_BASE_URL = "http://127.0.0.1:8500"
_LOCAL_PEER = ("127.0.0.1", 50000)


def _local_client() -> TestClient:
    return TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=_LOCAL_PEER)


def _setup_csrf(client: TestClient) -> str:
    page = client.get("/setup")
    match = re.search(r'name="setup_csrf" value="([^"]+)"', page.text)
    assert match is not None, page.text
    return match.group(1)


def _configure_existing_config(tmp_path: Path, monkeypatch) -> Path:
    config_path = tmp_path / "config.yaml"
    (tmp_path / "agent-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "compiled-agents").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory-store").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        (
            "schema_version: 1\n"
            "flowgency:\n"
            "  title: Flowgency\n"
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
    raw["flowgency"]["agent_library"] = str(library_root.resolve())
    raw["flowgency"]["compilation_cache"] = str(compiled_root.resolve())
    raw["flowgency"]["memory_store"] = str(memory_root.resolve())
    raw["flowgency"]["prompt_store"] = str(prompt_root.resolve())
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
        fallback_command: str = "copilot -C C:\\project -i \"prompt\" --name \"Flowgency setup\"",
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


class _ConnectedLaunchIntegration(_LaunchIntegration):
    def __init__(self, *args, connected_error: Exception | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.connected_requests = []
        self._connected_error = connected_error

    def connected_setup_available(self) -> bool:
        return True

    def connected_setup_launch(self, request) -> RuntimeLaunch:
        self.connected_requests.append(request)
        if self._connected_error is not None:
            raise self._connected_error
        return RuntimeLaunch(("copilot", "-i", request.prompt), request.data_root, {}, "connected")


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
        "flowgency.app:app",
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
    assert isinstance(events[5][1], app_mod._FlowgencyReloadFilter)
    assert events[5][1].root == tmp_path.resolve()


def test_reload_supervisor_rejects_future_artifacts_at_any_depth(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    config = uvicorn.Config(
        "flowgency.app:app",
        reload=True,
        reload_dirs=[str(root.resolve())],
        reload_includes=list(app_mod.RELOAD_INCLUDES),
    )
    server = uvicorn.Server(config)
    supervisor = app_mod._create_reload_supervisor(config, server, [])
    assert supervisor.reloader_name == "WatchFiles"

    watched_paths = [
        root / "flowgency" / "app.py",
        root / "flowgency" / "templates" / "base.html",
        root / "flowgency" / "static" / "app.css",
        root / "flowgency" / "static" / "sw.js",
        root / "flowgency" / "static" / "manifest.json",
        root / "flowgency" / "themes" / "workshop.yaml",
        root / "flowgency" / "themes" / "local.yml",
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

    external_group_log = tmp_path / "flowgency-data" / "groups" / "newsletter" / "logs" / "run.out"
    external_group_log.parent.mkdir(parents=True)
    external_group_log.write_text("probe", encoding="utf-8")
    assert not supervisor.watch_filter(external_group_log.resolve())


def test_reload_filter_uses_directory_components_not_name_fragments(tmp_path):
    reload_filter = app_mod._FlowgencyReloadFilter(tmp_path.resolve())

    assert reload_filter(tmp_path / "flowgency" / "shared_config.py")
    assert reload_filter(tmp_path / "flowgency" / "venv_tools.py")
    assert reload_filter(tmp_path / "flowgency" / "metadata.egg-info.json")


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
        "First run: open http://localhost:8602/setup to launch guided Flowgency setup."
        in output
    )
    assert "/admin/" not in output
    assert "/setup" in output


def test_setup_get_renders_only_data_root_and_integration_fields(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (
            _LaunchIntegration("copilot", "GitHub Copilot"),
        ),
    )
    response = TestClient(app_mod.app).get("/setup")

    assert response.status_code == 200
    assert "Flowgency data root" in response.text
    assert "Project folder" not in response.text
    assert 'name="data_root"' in response.text
    assert 'name="project_dir"' not in response.text
    assert 'placeholder="C:\\Flowgency"' in response.text
    assert 'id="browse-data-root"' in response.text
    assert "Choose Flowgency data root" in response.text
    assert "Flowgency data root selected." in response.text


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
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (_LaunchIntegration(),),
    )
    client = _local_client()
    csrf = _setup_csrf(client)
    response = client.post(
        "/setup/launch",
        data={"data_root": "relative/Flowgency", "integration": "copilot", "setup_csrf": csrf},
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    assert "Flowgency data root must be an absolute path." in response.text


def test_setup_launch_rejects_unavailable_integration(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    data_root.mkdir()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (_LaunchIntegration("claude-code", "Claude Code"),),
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root.resolve()), "integration": "copilot", "setup_csrf": csrf},
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    assert "Choose an available integration." in response.text


def test_setup_launch_error_response_preserves_csrf_for_retry(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    data_root.mkdir()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (_LaunchIntegration(),),
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
            "integration": "not-registered",
            "setup_csrf": csrf,
        },
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    match = re.search(r'name="setup_csrf" value="([^"]+)"', response.text)
    assert match is not None, response.text
    retry_csrf = match.group(1)
    assert retry_csrf == csrf

    retry = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
            "integration": "copilot",
            "setup_csrf": retry_csrf,
        },
        headers={"Origin": _LOCAL_BASE_URL},
    )
    assert retry.status_code == 200
    assert "Waiting for setup to complete" in retry.text


def test_setup_launch_waiting_response_preserves_csrf_for_relaunch(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    data_root.mkdir()
    integration = _LaunchIntegration(
        name="custom-launcher",
        display_name="Custom Launcher",
        fallback_command="custom-launcher --resume-setup",
        error=IntegrationError("Launch failed."),
    )
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
            "integration": "custom-launcher",
            "setup_csrf": csrf,
        },
        headers={"Origin": _LOCAL_BASE_URL},
    )
    assert response.status_code == 200
    assert "Waiting for setup to complete" in response.text
    match = re.search(r'name="setup_csrf" value="([^"]+)"', response.text)
    assert match is not None, response.text
    relaunch_csrf = match.group(1)
    assert relaunch_csrf == csrf

    relaunch = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
            "integration": "custom-launcher",
            "setup_csrf": relaunch_csrf,
        },
        headers={"Origin": _LOCAL_BASE_URL},
    )
    assert relaunch.status_code == 200
    assert "Waiting for setup to complete" in relaunch.text


def test_setup_launch_does_not_write_config(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    data_root.mkdir()
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )

    async def fake_run_in_threadpool(func, *args, **kwargs):
        assert getattr(func, "__self__", None) is integration
        assert getattr(func, "__name__", "") == "launch_interactive_setup"
        assert len(args) == 1
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.run_in_threadpool",
        fake_run_in_threadpool,
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root.resolve()), "integration": "copilot", "setup_csrf": csrf},
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    assert not config_path.exists()
    assert "Waiting for setup to complete" in response.text
    assert "setTimeout(" in response.text
    assert "setInterval(" not in response.text
    assert integration.requests[0].data_root == data_root.resolve()
    assert not hasattr(integration.requests[0], "project_dir")
    assert integration.requests[0].config_path == config_path.resolve()
    assert "flowgency-setup" in integration.requests[0].prompt
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
    data_root = tmp_path / "Flowgency"
    data_root.mkdir()
    integration = _LaunchIntegration(
        name="custom-launcher",
        display_name="Custom Launcher",
        fallback_command="custom-launcher --resume-setup",
        error=IntegrationError("Launch failed."),
    )
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )

    async def fake_run_in_threadpool(func, *args, **kwargs):
        assert getattr(func, "__self__", None) is integration
        assert getattr(func, "__name__", "") == "launch_interactive_setup"
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.run_in_threadpool",
        fake_run_in_threadpool,
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
            "integration": "custom-launcher",
            "setup_csrf": csrf,
        },
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    assert "Waiting for setup to complete" in response.text
    assert "custom-launcher --resume-setup" in response.text
    assert "copilot -C" not in response.text
    assert integration.fallback_requests == integration.requests


def test_setup_launch_creates_and_uses_missing_data_root(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "new" / "Flowgency"
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot", "setup_csrf": csrf},
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    assert data_root.is_dir()
    assert list(data_root.iterdir()) == []
    assert not config_path.exists()
    assert integration.requests[0].data_root == data_root.resolve(strict=True)
    assert "Waiting for setup to complete" in response.text
    assert "Flowgency data root" in response.text


def test_setup_launch_returns_to_form_when_launch_and_fallback_fail(
    tmp_path, monkeypatch
):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    integration = _LaunchIntegration(
        error=IntegrationError("Bundled setup skill is unavailable."),
        fallback_error=IntegrationError("No valid fallback command."),
    )
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot", "setup_csrf": csrf},
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 200
    assert data_root.is_dir()
    assert "Bundled setup skill is unavailable." in response.text
    assert "Waiting for setup to complete" not in response.text


def test_connected_launch_is_local_idempotent_and_streams_output(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"
    integration = _ConnectedLaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (integration,),
    )
    monkeypatch.setattr("flowgency.web.routes.admin_teams.connected_process_available", lambda: True)
    process = FakeProcess()
    launches = []

    def start(launch):
        launches.append(launch)
        return process

    manager = SetupSessionManager(process_factory=start)
    monkeypatch.setattr(app_mod, "SetupSessionManager", lambda: manager)
    with TestClient(app_mod.app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 50001)) as client:
        page = client.get("/setup")
        csrf = re.search(r'name="setup_csrf" value="([^"]+)"', page.text).group(1)
        form = {"data_root": str(root), "integration": "copilot", "setup_csrf": csrf}
        headers = {"Origin": "http://127.0.0.1:8500"}
        first = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)
        assert first.status_code == 303 and first.headers["location"] == "/setup/session"
        repeat = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)
        assert repeat.status_code == 303 and repeat.headers["location"] == "/setup/session"
        assert len(integration.connected_requests) == 2
        assert len(launches) == 1
        assert integration.requests == []
        assert not config_path.exists()
        assert client.get("/setup/session/state").json()["state"] == "running"
        with client.websocket_connect("ws://127.0.0.1:8500/setup/session/ws", headers=headers) as ws:
            assert ws.receive_json()["state"] == "running"
            process.output.put(b"Hello from Copilot")
            assert ws.receive_bytes() == b"Hello from Copilot"
            ws.send_json({"type": "input", "data": "yes\r"})


def test_setup_launch_rejects_remote_peer_without_creating_root_or_launching(
    tmp_path, monkeypatch
):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    local_client = _local_client()
    csrf = _setup_csrf(local_client)
    remote_client = TestClient(app_mod.app)

    response = remote_client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot", "setup_csrf": csrf},
    )

    assert response.status_code == 403
    assert not data_root.exists()
    assert integration.requests == []


def test_setup_launch_rejects_invalid_csrf(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = _local_client()
    _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot", "setup_csrf": "not-the-token"},
        headers={"Origin": _LOCAL_BASE_URL},
    )

    assert response.status_code == 403
    assert not data_root.exists()
    assert integration.requests == []


def test_setup_launch_rejects_mismatched_origin(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Flowgency"
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = _local_client()
    csrf = _setup_csrf(client)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot", "setup_csrf": csrf},
        headers={"Origin": "http://evil.example"},
    )

    assert response.status_code == 403
    assert not data_root.exists()
    assert integration.requests == []


def _start_connected_session(tmp_path, monkeypatch, *, process_factory=None):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"
    integration = _ConnectedLaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (integration,),
    )
    monkeypatch.setattr("flowgency.web.routes.admin_teams.connected_process_available", lambda: True)
    process = FakeProcess()
    manager = SetupSessionManager(process_factory=process_factory or (lambda launch: process))
    monkeypatch.setattr(app_mod, "SetupSessionManager", lambda: manager)
    return config_path, root, integration, process, manager


def test_connected_session_stop_confirms_process_and_redirects(tmp_path, monkeypatch):
    config_path, root, integration, process, manager = _start_connected_session(tmp_path, monkeypatch)
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50002)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert launch.status_code == 303

        stop = client.post(
            "/setup/session/stop",
            data={"setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )

        assert stop.status_code == 303
        assert stop.headers["location"] == "/setup"
        assert process.running is False


def test_setup_session_view_and_status_stay_ready_while_session_runs(
    tmp_path, monkeypatch, raw_config
):
    config_path, root, integration, process, manager = _start_connected_session(tmp_path, monkeypatch)
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50003)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert launch.status_code == 303

        config_path.write_text(
            yaml.safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
            encoding="utf-8",
        )

        assert client.get("/setup/status").json() == {"state": "ready", "redirect": "/"}
        assert client.get("/setup/session").status_code == 200

        stop = client.post(
            "/setup/session/stop",
            data={"setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert stop.status_code == 303
        assert stop.headers["location"] == "/"


def test_setup_session_state_and_ws_reject_foreign_cookie(tmp_path, monkeypatch):
    config_path, root, integration, process, manager = _start_connected_session(tmp_path, monkeypatch)
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50004)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert launch.status_code == 303

        client.cookies.set("flowgency_setup", "forged.notarealsignature")

        state_response = client.get("/setup/session/state")
        assert state_response.status_code == 403

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("ws://127.0.0.1:8500/setup/session/ws", headers=headers):
                pass
        assert exc_info.value.code == 1008


def test_setup_session_ws_rejects_malformed_and_oversized_messages(tmp_path, monkeypatch):
    config_path, root, integration, process, manager = _start_connected_session(tmp_path, monkeypatch)
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50005)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert launch.status_code == 303

        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as malformed:
            with client.websocket_connect("ws://127.0.0.1:8500/setup/session/ws", headers=headers) as ws:
                ws.receive_json()
                ws.send_text("not json")
                ws.receive_text()
        assert malformed.value.code == 1003

        with pytest.raises(WebSocketDisconnect) as oversized:
            with client.websocket_connect("ws://127.0.0.1:8500/setup/session/ws", headers=headers) as ws:
                ws.receive_json()
                ws.send_text("x" * 70000)
                ws.receive_text()
        assert oversized.value.code == 1009


def test_connected_launch_reports_unconfirmed_cleanup_without_fallback(tmp_path, monkeypatch):
    def failing_factory(launch):
        raise ConnectedLaunchError("could not confirm stopped", cleanup_confirmed=False)

    config_path, root, integration, process, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=failing_factory
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)

        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

        assert response.status_code == 409
        assert integration.requests == []


def test_connected_launch_reports_generic_spawn_error_without_fallback(tmp_path, monkeypatch):
    def failing_factory(launch):
        raise RuntimeError("boom")

    config_path, root, integration, process, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=failing_factory
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)

        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

        assert response.status_code == 409
        assert integration.requests == []


def test_connected_launch_confirmed_clean_failure_falls_back_to_external_launch(
    tmp_path, monkeypatch
):
    def failing_factory(launch):
        raise ConnectedLaunchError("cleaned up", cleanup_confirmed=True)

    config_path, root, integration, process, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=failing_factory
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)

        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

        assert response.status_code == 200
        assert "Waiting for setup to complete" in response.text
        assert integration.requests != []


def test_connected_launch_conflict_never_replaces_running_session(tmp_path, monkeypatch):
    config_path, root, integration, process, manager = _start_connected_session(tmp_path, monkeypatch)
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50006)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert launch.status_code == 303

        other_root = root.parent / "Other"
        conflict = client.post(
            "/setup/launch",
            data={"data_root": str(other_root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )

        assert conflict.status_code == 409
        assert conflict.json()["session"] == "/setup/session"
        assert client.get("/setup/session/state").json()["state"] == "running"


def test_send_output_reports_unavailable_when_session_vanishes_without_snapshot():
    import asyncio

    from flowgency.web.routes.setup_terminal import _send_output

    class _FakeWebSocket:
        def __init__(self):
            self.sent_json = []

        async def send_json(self, payload):
            self.sent_json.append(payload)

        async def send_bytes(self, data):  # pragma: no cover - not exercised here
            raise AssertionError("no output expected")

    class _FakeManager:
        def snapshot(self, owner):
            return None

        async def consumed(self, owner, connection_id, byte_count):  # pragma: no cover
            raise AssertionError("not reached")

    async def _run():
        websocket = _FakeWebSocket()
        manager = _FakeManager()
        pending: asyncio.Queue[bytes | None] = asyncio.Queue()
        pending.put_nowait(None)
        await _send_output(websocket, manager, "owner", "connection", pending)
        assert websocket.sent_json == [
            {
                "type": "state",
                "state": "unavailable",
                "message": "Setup session is no longer available.",
            }
        ]

    asyncio.run(_run())


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


def test_setup_status_returns_incomplete_when_config_has_no_teams(
    tmp_path, monkeypatch, raw_config
):
    config_path = tmp_path / "config.yaml"
    raw_config = _materialize_ready_config(tmp_path, raw_config)
    raw_config["flowgency"]["default_team"] = ""
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
    raw["flowgency"] = dict(raw_config["flowgency"])
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


def test_static_pwa_metadata_uses_flowgency():
    import json

    repo_root = Path(__file__).parents[1]
    manifest = json.loads(
        (repo_root / "flowgency" / "static" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    service_worker = (
        repo_root / "flowgency" / "static" / "sw.js"
    ).read_text(encoding="utf-8")
    base_template = (
        repo_root / "flowgency" / "templates" / "base.html"
    ).read_text(encoding="utf-8")

    assert manifest["name"] == "Flowgency"
    assert manifest["short_name"] == "Flowgency"
    assert manifest["description"] == (
        "Ticket-driven orchestration for teams of AI agents"
    )
    assert "flowgency-app-shell" in service_worker
    assert "flowgency_title" in base_template
    assert "bg-flowgency-" in base_template


def test_setup_page_renders_flowgency_title(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    client = TestClient(app_mod.app)

    response = client.get("/setup", follow_redirects=True)

    assert response.status_code == 200
    assert "<title>Setup \u2014 Flowgency</title>" in response.text
    assert ">Flowgency<" in response.text


def test_admin_context_fallback_title_without_snapshot():
    from unittest.mock import MagicMock

    from flowgency.web.routes.admin_teams import _base_admin_context

    ctx = _base_admin_context(MagicMock(), snapshot=None)

    assert ctx["flowgency_title"] == "Flowgency"
