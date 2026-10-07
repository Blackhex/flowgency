"""Tests for web server startup and reload configuration."""

import json
import re
import shutil
import threading
from dataclasses import replace
from html.parser import HTMLParser
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
from flowgency.jobs.processes import ProcessStopEvidence
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
    with _local_client() as client:
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
    with _local_client() as client:
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
    with _local_client() as client:
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
    with _local_client() as client:
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
    with _local_client() as client:
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
    with _local_client() as client:
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


@pytest.mark.parametrize("slot_state", ["running", "failed-start", "failed-stop"])
@pytest.mark.parametrize("integration_name", ["copilot", "codex"])
def test_external_setup_launch_requires_confirmed_cleanup(tmp_path, monkeypatch, slot_state, integration_name):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"
    connected = _ConnectedLaunchIntegration()
    external = _LaunchIntegration(integration_name, "External setup")

    class CleanupProcess(FakeProcess):
        confirmed = False

        def stop(self, lifecycle):
            if not self.confirmed:
                return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, False, "cleanup-pending")
            return super().stop(lifecycle)

    process = CleanupProcess()

    def factory(launch):
        if slot_state == "failed-start":
            raise ConnectedLaunchError("cleanup pending", cleanup_confirmed=False, _cleanup=process)
        return process

    manager = SetupSessionManager(process_factory=factory, sweep_interval=0)
    monkeypatch.setattr(app_mod, "SetupSessionManager", lambda: manager)
    monkeypatch.setattr("flowgency.web.routes.admin_teams.connected_process_available", lambda: True)
    monkeypatch.setattr("flowgency.web.routes.admin_teams.launchable_integrations", lambda integrations, data_root: (connected,))
    with _local_client() as client:
        try:
            csrf = _setup_csrf(client)
            headers = {"Origin": _LOCAL_BASE_URL}
            form = {"data_root": str(root), "integration": "copilot", "setup_csrf": csrf}
            first = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)
            assert first.status_code == (409 if slot_state == "failed-start" else 303)
            if slot_state == "failed-stop":
                assert client.post("/setup/session/stop", data={"setup_csrf": csrf}, headers=headers).status_code == 409

            monkeypatch.setattr("flowgency.web.routes.admin_teams.launchable_integrations", lambda integrations, data_root: (external,))
            form["integration"] = integration_name
            blocked = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)

            assert blocked.status_code == 409
            assert external.requests == []
            assert process.running is True
            assert not config_path.exists()

            process.confirmed = True
            assert client.post("/setup/session/stop", data={"setup_csrf": csrf}, headers=headers, follow_redirects=False).status_code == 303
            allowed = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)
            assert allowed.status_code == 200
            assert len(external.requests) == 1
            assert external.requests[0].data_root == root.resolve()
            assert not config_path.exists()
        finally:
            process.confirmed = True


@pytest.mark.parametrize("failure_path", ["connected-structured", "connected-generic", "external-barrier", "external-barrier-generic"])
def test_failed_setup_launch_http_diagnostics_are_sanitized(tmp_path, monkeypatch, failure_path):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    integration = _LaunchIntegration("codex", "Codex") if failure_path.startswith("external-barrier") else _ConnectedLaunchIntegration()

    def fail(launch=None):
        if failure_path.endswith("generic"):
            raise RuntimeError("private launch detail")
        raise ConnectedLaunchError("private launch detail", cleanup_confirmed=False)

    manager = SetupSessionManager(process_factory=fail, sweep_interval=0)
    monkeypatch.setattr(app_mod, "SetupSessionManager", lambda: manager)
    monkeypatch.setattr("flowgency.web.routes.admin_teams.connected_process_available", lambda: True)
    monkeypatch.setattr("flowgency.web.routes.admin_teams.launchable_integrations", lambda integrations, root: (integration,))
    if failure_path.startswith("external-barrier"):
        monkeypatch.setattr("flowgency.web.setup_sessions._retry_unconfirmed", fail)
    with _local_client() as client:
        csrf = _setup_csrf(client)
        response = client.post(
            "/setup/launch", data={"data_root": str(tmp_path / "Flowgency"), "integration": integration.name, "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL}, follow_redirects=False,
        )

        assert response.status_code == 409
        assert "private launch detail" not in response.text
        state = client.get("/setup/session/state").json()
        assert state["state"] == "failed"
        assert "private launch detail" not in state["message"]
        assert "cleanup could not be confirmed" in state["message"]
        assert client.post("/setup/session/stop", data={"setup_csrf": csrf}, headers={"Origin": _LOCAL_BASE_URL}).status_code == 409
        assert client.get("/setup/session/state").json()["state"] == "failed"
        assert integration.requests == []
        assert not config_path.exists()


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


def _start_connected_session(tmp_path, monkeypatch, *, process_factory=None, integration=None):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"
    integration = integration or _ConnectedLaunchIntegration()
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

        form_page = client.get("/setup", follow_redirects=False)
        assert form_page.status_code == 200
        assert 'id="browse-data-root"' in form_page.text

        session_page = client.get("/setup/session")
        assert session_page.status_code == 200


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

        status = client.get("/setup/status").json()
        assert status["state"] == "ready"
        assert status["completion"]["phase"] == "pending"
        assert "redirect" not in status
        assert client.get("/setup", follow_redirects=False).headers["location"] == "/setup/session"
        assert client.get("/setup/session").status_code == 200
        assert process.running is True

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


def test_setup_session_ws_survives_late_close_after_client_disconnect(tmp_path, monkeypatch):
    """A real transport raises when the server tries to close after the peer
    already disconnected; Starlette converts that OSError into
    WebSocketDisconnect(1006). TestClient's in-memory transport never fails
    this way on its own, so simulate the failure directly on WebSocket.close.
    """
    from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

    original_close = WebSocket.close

    async def close_like_a_dead_transport(self, code: int = 1000, reason: str | None = None) -> None:
        if self.client_state == WebSocketState.DISCONNECTED:
            self.application_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect(code=1006)
        await original_close(self, code=code, reason=reason)

    monkeypatch.setattr(WebSocket, "close", close_like_a_dead_transport)

    original_detach = SetupSessionManager.detach
    detach_completed = threading.Event()

    async def detach_and_signal(self, owner: str, connection_id: str) -> None:
        try:
            await original_detach(self, owner, connection_id)
        finally:
            detach_completed.set()

    monkeypatch.setattr(SetupSessionManager, "detach", detach_and_signal)

    config_path, root, integration, process, manager = _start_connected_session(tmp_path, monkeypatch)
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50008)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers=headers,
            follow_redirects=False,
        )
        assert launch.status_code == 303

        session = client.websocket_connect("ws://127.0.0.1:8500/setup/session/ws", headers=headers)
        ws = session.__enter__()
        try:
            assert ws.receive_json()["state"] == "running"
            ws.close()  # simulate the browser tab closing
            assert detach_completed.wait(timeout=5), "server never finished handling the disconnect"
        finally:
            session.__exit__(None, None, None)

        assert client.get("/setup/session/state").json()["state"] == "running"


def test_setup_session_ws_attached_during_starting_survives_confirmed_clean_launch_failure(
    tmp_path, monkeypatch
):
    from starlette.websockets import WebSocketDisconnect

    factory_entered = threading.Event()
    release_factory = threading.Event()

    def blocking_then_clean_failure(launch):
        factory_entered.set()
        assert release_factory.wait(timeout=5), "test never released the fake spawn"
        raise ConnectedLaunchError("cleaned up", cleanup_confirmed=True)

    config_path, root, integration, process, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=blocking_then_clean_failure
    )
    launch_result: dict[str, object] = {}
    ws_result: dict[str, object] = {}
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50007)) as client:
        csrf = _setup_csrf(client)
        headers = {"Origin": _LOCAL_BASE_URL}

        def run_launch() -> None:
            try:
                launch_result["response"] = client.post(
                    "/setup/launch",
                    data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
                    headers=headers,
                    follow_redirects=False,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                launch_result["error"] = exc

        launch_thread = threading.Thread(target=run_launch, daemon=True)
        launch_thread.start()
        try:
            assert factory_entered.wait(timeout=5), "connected launch never reached the fake spawn"

            def run_ws() -> None:
                try:
                    with client.websocket_connect(
                        "ws://127.0.0.1:8500/setup/session/ws", headers=headers
                    ) as ws:
                        ws_result["starting"] = ws.receive_json()
                        release_factory.set()
                        ws_result["unavailable"] = ws.receive_json()
                        ws.receive_text()
                except WebSocketDisconnect as exc:
                    ws_result["disconnect_code"] = exc.code
                except Exception as exc:  # pragma: no cover - asserted below
                    ws_result["error"] = exc

            ws_thread = threading.Thread(target=run_ws, daemon=True)
            ws_thread.start()
            ws_thread.join(timeout=5)
            assert not ws_thread.is_alive(), (
                "WS attach-during-starting handling hung instead of closing cleanly"
            )
        finally:
            release_factory.set()
            launch_thread.join(timeout=5)
            assert not launch_thread.is_alive(), "connected launch fallback hung"

        assert "error" not in ws_result, ws_result.get("error")
        assert ws_result["starting"]["state"] == "starting"
        assert ws_result["unavailable"] == {
            "type": "state",
            "state": "unavailable",
            "message": "Setup session is no longer available.",
        }
        assert ws_result.get("disconnect_code") == 1000

        assert "error" not in launch_result, launch_result.get("error")
        response = launch_result["response"]
        assert response.status_code == 200
        assert "Waiting for setup to complete" in response.text
        assert integration.requests != []

        assert client.get("/setup/session/state").status_code == 404


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


def test_connected_launch_fallback_builder_integration_error_returns_error_form(
    tmp_path, monkeypatch
):
    integration = _ConnectedLaunchIntegration(
        fallback_error=IntegrationError(
            "Flowgency data root contains a conflicting flowgency-setup skill."
        )
    )
    config_path, root, integration, process, manager = _start_connected_session(
        tmp_path, monkeypatch, integration=integration
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)

        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

        assert response.status_code == 200
        assert "conflicting flowgency-setup skill" in response.text
        assert integration.connected_requests == []
        assert client.get("/setup/session/state").status_code == 404

        retry_match = re.search(r'name="setup_csrf" value="([^"]+)"', response.text)
        assert retry_match is not None, response.text
        retry = client.post(
            "/setup/launch",
            data={
                "data_root": str(root),
                "integration": "copilot",
                "setup_csrf": retry_match.group(1),
            },
            headers={"Origin": _LOCAL_BASE_URL},
            follow_redirects=False,
        )
        assert retry.status_code != 403


def test_connected_launch_builder_integration_error_returns_error_form(
    tmp_path, monkeypatch
):
    integration = _ConnectedLaunchIntegration(
        connected_error=IntegrationError(
            "Bundled flowgency-setup skill is missing or unreadable; reinstall flowgency."
        )
    )
    config_path, root, integration, process, manager = _start_connected_session(
        tmp_path, monkeypatch, integration=integration
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)

        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

        assert response.status_code == 200
        assert "Bundled flowgency-setup skill is missing or unreadable" in response.text
        assert client.get("/setup/session/state").status_code == 404

        retry_match = re.search(r'name="setup_csrf" value="([^"]+)"', response.text)
        assert retry_match is not None, response.text
        retry = client.post(
            "/setup/launch",
            data={
                "data_root": str(root),
                "integration": "copilot",
                "setup_csrf": retry_match.group(1),
            },
            headers={"Origin": _LOCAL_BASE_URL},
            follow_redirects=False,
        )
        assert retry.status_code != 403


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


_COMPLETION_ENV_NAMES = (
    "FLOWGENCY_SETUP_ORIGIN",
    "FLOWGENCY_SETUP_TOKEN",
    "FLOWGENCY_SETUP_LAUNCH_ID",
)


class _EnvironmentLaunchIntegration(_ConnectedLaunchIntegration):
    """Carries the setup-only environment overlay the way the real integration does."""

    def connected_setup_launch(self, request) -> RuntimeLaunch:
        self.connected_requests.append(request)
        return RuntimeLaunch(
            ("copilot", "-i", request.prompt), request.data_root, dict(request.environment), "connected"
        )


class _CompletionSession:
    def __init__(self, client, launches, process, manager, config_path, tmp_path, integration):
        self.client = client
        self.launches = launches
        self.process = process
        self.manager = manager
        self.config_path = config_path
        self.tmp_path = tmp_path
        self.integration = integration

    @property
    def env(self):
        return self.launches[0].env

    @property
    def token(self) -> str:
        return self.env["FLOWGENCY_SETUP_TOKEN"]

    @property
    def launch_id(self) -> str:
        return self.env["FLOWGENCY_SETUP_LAUNCH_ID"]

    def write_ready_config(self, raw_config) -> str:
        import hashlib

        self.config_path.write_text(
            yaml.safe_dump(_materialize_ready_config(self.tmp_path, raw_config), sort_keys=False),
            encoding="utf-8",
        )
        return hashlib.sha256(self.config_path.read_bytes()).hexdigest()

    def command(self, revision: str, **overrides) -> dict:
        body = {
            "launch_id": self.launch_id,
            "revision": revision,
            "scheduler_result": "manual-only",
            "all_questions_answered": True,
            "summary_delivered": True,
        }
        body.update(overrides)
        return body

    def post(self, body, *, token=None, headers=None, **kwargs):
        sent = {"Authorization": f"Bearer {self.token if token is None else token}"}
        sent.update(headers or {})
        return self.client.post("/setup/session/completion", json=body, headers=sent, **kwargs)


@pytest.fixture
def completion_session(tmp_path, monkeypatch):
    launches = []
    process = FakeProcess()

    def factory(launch):
        launches.append(launch)
        return process

    config_path, root, integration, _unused, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=factory, integration=_EnvironmentLaunchIntegration()
    )
    with TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50040)) as client:
        csrf = _setup_csrf(client)
        launch = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
            follow_redirects=False,
        )
        assert launch.status_code == 303
        yield _CompletionSession(client, launches, process, manager, config_path, tmp_path, integration)
        process.running = False
        process.output.put(None)


def test_completion_launch_passes_scoped_capability_only_through_environment(completion_session):
    session = completion_session
    request = session.integration.connected_requests[0]

    assert set(session.env) == set(_COMPLETION_ENV_NAMES)
    assert session.env["FLOWGENCY_SETUP_ORIGIN"] == _LOCAL_BASE_URL
    assert len(session.launch_id) == 32
    assert request.environment == dict(session.env)
    assert session.token not in request.prompt
    assert not any(session.token in part for part in session.launches[0].argv)
    assert session.token not in repr(request)
    assert session.token not in repr(session.launches[0])
    page = session.client.get("/setup/session")
    assert session.token not in page.text
    assert session.token not in session.client.get("/setup/session/state").text
    snapshot = session.manager._session
    assert session.token not in snapshot.fallback_command


def test_completion_callback_acknowledges_with_bearer_capability(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)

    response = session.post(session.command(revision))

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["completion"]["launch_id"] == session.launch_id
    assert set(body["completion"]) == {"launch_id", "phase", "redirect_allowed", "message"}
    assert session.token not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_completion_callback_rejects_missing_and_invalid_credentials(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)
    body = session.command(revision)

    missing = session.client.post("/setup/session/completion", json=body)
    invalid = session.client.post(
        "/setup/session/completion",
        json={"launch_id": "a" * 32},
        headers={"Authorization": "Bearer invalid-test-capability"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert "invalid-test-capability" not in invalid.text
    assert session.token not in missing.text + invalid.text


def test_completion_callback_denies_remote_peer_foreign_host_and_origin(completion_session, raw_config):
    session = completion_session
    body = session.command(session.write_ready_config(raw_config))
    auth = {"Authorization": f"Bearer {session.token}"}

    remote = TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("192.0.2.9", 50041))
    foreign_host = TestClient(app_mod.app, base_url="http://localhost:8500", client=("127.0.0.1", 50042))

    assert remote.post("/setup/session/completion", json=body, headers=auth).status_code == 403
    assert foreign_host.post("/setup/session/completion", json=body, headers=auth).status_code == 403
    foreign_origin = session.post(body, headers={"Origin": "http://evil.example"})
    assert foreign_origin.status_code == 403
    assert session.token not in foreign_origin.text


def test_completion_callback_rejects_wrong_launch_and_stale_revision(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)

    wrong_launch = session.post(session.command(revision, launch_id="c" * 32))
    stale = session.post(session.command("d" * 64))

    assert wrong_launch.status_code == 409
    assert stale.status_code == 409
    assert session.token not in wrong_launch.text + stale.text


def test_completion_callback_rejects_malformed_oversized_and_extra_fields(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)
    auth = {"Authorization": f"Bearer {session.token}", "Content-Type": "application/json"}

    extra = session.post({**session.command(revision), "unexpected": "leaked-field-sentinel"})
    typed = session.post(session.command(revision, all_questions_answered="true"))
    declared = session.client.post(
        "/setup/session/completion", content=b" " * 5000, headers=auth
    )

    def chunks():
        for _ in range(60):
            yield b" " * 100

    streamed = session.client.post("/setup/session/completion", content=chunks(), headers=auth)

    assert extra.status_code == typed.status_code == 422
    assert "leaked-field-sentinel" not in extra.text
    assert declared.status_code == 413
    assert streamed.status_code == 413
    assert session.token not in extra.text + typed.text + declared.text + streamed.text


def test_completion_callback_rejects_capability_after_unexpected_exit(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)
    session.process.running = False
    session.process.output.put(None)
    for _ in range(500):
        if session.client.get("/setup/session/state").json()["state"] == "exited":
            break
        threading.Event().wait(0.01)

    response = session.post(session.command(revision))

    assert response.status_code == 401
    assert session.token not in response.text


def test_completion_callback_replays_identical_request_without_revalidating(
    completion_session, raw_config, monkeypatch
):
    from flowgency.web.routes import setup_terminal

    session = completion_session
    revision = session.write_ready_config(raw_config)
    calls = []
    real = setup_terminal.validate_current_completion
    monkeypatch.setattr(
        setup_terminal, "validate_current_completion", lambda *args: calls.append(args) or real(*args)
    )

    first = session.post(session.command(revision))
    again = session.post(session.command(revision))

    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("change", "expected_status", "expected_code"),
    [("revision", 409, "stale"), ("source", 503, "not-ready")],
)
def test_completion_identical_replay_revalidates_drift_and_source_removal(
    completion_session, raw_config, monkeypatch, change, expected_status, expected_code
):
    from flowgency.web.routes import setup_terminal

    session = completion_session
    revision = session.write_ready_config(raw_config)
    command = session.command(revision)
    assert session.post(command).status_code == 200
    acknowledgement = session.manager._completion.acknowledgement
    if change == "revision":
        session.config_path.write_bytes(session.config_path.read_bytes() + b"\n# revision drift\n")
    else:
        (session.tmp_path / "agent-library" / "builder-blueprint" / "AGENTS.md").unlink()
    status = session.client.get("/setup/status").json()
    assert status["completion"]["phase"] == "pending"
    assert "redirect" not in status

    progressed = threading.Event()
    release_validation = threading.Event()
    calls = []
    responses = []
    real = setup_terminal.validate_current_completion

    def paused(config_path, reported_revision):
        calls.append((config_path, reported_revision))
        progressed.set()
        assert release_validation.wait(5), "validation barrier was not released"
        return real(config_path, reported_revision)

    monkeypatch.setattr(setup_terminal, "validate_current_completion", paused)

    def replay():
        try:
            responses.append(session.post(command))
        finally:
            progressed.set()

    thread = threading.Thread(target=replay)
    thread.start()
    try:
        assert progressed.wait(5)
        release_validation.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert len(responses) == 1
        response = responses[0]
        assert response.status_code == expected_status
        assert response.json()["ok"] is False
        assert response.json()["code"] == expected_code
        assert calls == [(session.config_path, revision)]
        assert session.manager._completion.acknowledgement is acknowledgement
        assert session.process.running is True
        assert session.process.writes == []
        assert session.token not in response.text
        assert str(session.config_path) not in response.text
    finally:
        release_validation.set()
        thread.join(timeout=10)


def test_completion_callback_never_overlaps_validation_for_one_attempt(
    completion_session, raw_config, monkeypatch
):
    from flowgency.web.routes import setup_terminal

    session = completion_session
    revision = session.write_ready_config(raw_config)
    state = {"active": 0, "peak": 0, "calls": 0}
    guard = threading.Lock()
    real = setup_terminal.validate_current_completion

    def slow(*args):
        with guard:
            state["active"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["active"])
        threading.Event().wait(0.15)
        try:
            return real(*args)
        finally:
            with guard:
                state["active"] -= 1

    monkeypatch.setattr(setup_terminal, "validate_current_completion", slow)
    results = []

    def run(result):
        results.append(session.post(session.command(revision, scheduler_result=result)))

    threads = [
        threading.Thread(target=run, args=("manual-only",)),
        threading.Thread(target=run, args=("inactive",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert [item.status_code for item in results] == [200, 200]
    assert state["calls"] == 2
    assert state["peak"] == 1


def test_completion_callback_reports_sanitized_unavailable_when_validation_fails(
    completion_session, raw_config, monkeypatch
):
    from flowgency.web.routes import setup_terminal

    session = completion_session
    revision = session.write_ready_config(raw_config)

    def boom(*args):
        raise RuntimeError(f"secret-trace-detail {session.token} {session.config_path}")

    monkeypatch.setattr(setup_terminal, "validate_current_completion", boom)

    response = session.post(session.command(revision))

    assert response.status_code == 503
    assert "secret-trace-detail" not in response.text
    assert session.token not in response.text
    assert str(session.config_path) not in response.text
    assert set(response.json()) == {"ok", "code", "error"}


def test_completion_callback_reports_not_ready_configuration(completion_session):
    session = completion_session

    response = session.post(session.command("e" * 64))

    assert response.status_code == 503
    assert response.json()["code"] == "not-ready"
    assert str(session.config_path) not in response.text


@pytest.mark.parametrize("parent_exists", [False, True])
def test_validate_current_completion_missing_config_creates_nothing(tmp_path, parent_exists):
    from flowgency.web.setup_completion import SetupCompletionUnavailable, validate_current_completion

    config_path = tmp_path / "missing" / "nested" / "config.yaml"
    if parent_exists:
        config_path.parent.mkdir(parents=True)
    (tmp_path / "retained.txt").write_bytes(b"keep this unchanged")
    before = _tree_bytes(tmp_path)

    with pytest.raises(SetupCompletionUnavailable) as raised:
        validate_current_completion(config_path, "a" * 64)

    assert raised.value.code == "not-ready"
    assert _tree_bytes(tmp_path) == before
    assert not config_path.exists()
    assert config_path.parent.is_dir() is parent_exists


@pytest.mark.parametrize("read_method", ["load", "inspect"])
def test_validate_current_completion_lost_config_parent_is_not_recreated(
    tmp_path, raw_config, monkeypatch, read_method
):
    import hashlib
    from flowgency.configuration import ConfigStore
    from flowgency.web.setup_completion import SetupCompletionUnavailable, validate_current_completion

    config_path = tmp_path / "canonical" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        yaml.safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
        encoding="utf-8",
    )
    revision = hashlib.sha256(config_path.read_bytes()).hexdigest()
    read = getattr(ConfigStore, read_method)
    after_removal = {}

    def remove_parent(store, **kwargs):
        store.path.unlink()
        store.lock_path.unlink(missing_ok=True)
        store.path.parent.rmdir()
        after_removal.update(_tree_bytes(tmp_path))
        return read(store, **kwargs)

    monkeypatch.setattr(ConfigStore, read_method, remove_parent)
    with pytest.raises(SetupCompletionUnavailable) as raised:
        validate_current_completion(config_path, revision)

    assert raised.value.code == "not-ready"
    assert not config_path.parent.exists()
    assert _tree_bytes(tmp_path) == after_removal


def test_validate_current_completion_is_read_only_and_returns_rechecked_revision(
    tmp_path, raw_config
):
    import hashlib
    from flowgency.web.setup_completion import validate_current_completion

    config_path = tmp_path / "config.yaml"
    raw = _materialize_ready_config(tmp_path, raw_config)
    for key in ("compilation_cache", "memory_store", "prompt_store"):
        Path(raw["flowgency"][key]).rmdir()
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    revision = hashlib.sha256(config_path.read_bytes()).hexdigest()
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    assert validate_current_completion(config_path, revision) == revision

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert [item for item in after if item not in before] in ([], ["config.yaml.lock"])
    for key in ("compilation_cache", "memory_store", "prompt_store"):
        assert not Path(raw["flowgency"][key]).exists()


def _tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _instance_prompt_config(tmp_path: Path, raw_config: dict, *, write_prompt: bool) -> tuple[Path, str]:
    import hashlib

    raw = _materialize_ready_config(tmp_path, raw_config)
    raw["teams"]["newsletter"]["agents"][0]["prompts"] = ["local-triage"]
    if write_prompt:
        prompt = tmp_path / "prompts" / "newsletter" / "builder" / "local-triage.prompt.md"
        prompt.parent.mkdir(parents=True)
        prompt.write_bytes(b"---\nname: local-triage\ndescription: Triage.\n---\n\nTriage it.\n")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path, hashlib.sha256(config_path.read_bytes()).hexdigest()


def test_validate_current_completion_leaves_an_existing_prompt_store_untouched(tmp_path, raw_config):
    from flowgency.web.setup_completion import validate_current_completion

    config_path, revision = _instance_prompt_config(tmp_path, raw_config, write_prompt=True)
    prompt_root = tmp_path / "prompts"
    before = _tree_bytes(prompt_root)

    assert validate_current_completion(config_path, revision) == revision

    assert _tree_bytes(prompt_root) == before
    assert not (prompt_root / ".locks").exists()


def test_validate_current_completion_refuses_a_missing_instance_prompt_without_writing(tmp_path, raw_config):
    from flowgency.web.setup_completion import SetupCompletionUnavailable, validate_current_completion

    config_path, revision = _instance_prompt_config(tmp_path, raw_config, write_prompt=False)
    prompt_root = tmp_path / "prompts"
    before = _tree_bytes(prompt_root)

    with pytest.raises(SetupCompletionUnavailable) as raised:
        validate_current_completion(config_path, revision)

    assert raised.value.code == "not-ready"
    assert _tree_bytes(prompt_root) == before


def test_validate_current_completion_refuses_a_missing_prompt_store_root_without_creating_it(tmp_path, raw_config):
    from flowgency.web.setup_completion import SetupCompletionUnavailable, validate_current_completion

    config_path, revision = _instance_prompt_config(tmp_path, raw_config, write_prompt=True)
    prompt_root = tmp_path / "prompts"
    shutil.rmtree(prompt_root)

    with pytest.raises(SetupCompletionUnavailable) as raised:
        validate_current_completion(config_path, revision)

    assert raised.value.code == "not-ready"
    assert not prompt_root.exists()


def test_validate_current_completion_refuses_an_invalid_instance_prompt(tmp_path, raw_config):
    from flowgency.web.setup_completion import SetupCompletionUnavailable, validate_current_completion

    config_path, revision = _instance_prompt_config(tmp_path, raw_config, write_prompt=True)
    prompt = tmp_path / "prompts" / "newsletter" / "builder" / "local-triage.prompt.md"
    prompt.write_bytes(b"---\nname: local-triage\n")
    before = _tree_bytes(tmp_path / "prompts")

    with pytest.raises(SetupCompletionUnavailable) as raised:
        validate_current_completion(config_path, revision)

    assert raised.value.code == "not-ready"
    assert _tree_bytes(tmp_path / "prompts") == before


def test_validate_current_completion_rejects_stale_missing_and_unready_sources(tmp_path, raw_config):
    import hashlib
    from flowgency.web.setup_completion import (
        SetupCompletionStale,
        SetupCompletionUnavailable,
        validate_current_completion,
    )

    config_path = tmp_path / "config.yaml"
    with pytest.raises(SetupCompletionUnavailable):
        validate_current_completion(config_path, "a" * 64)
    assert not config_path.exists()

    raw = _materialize_ready_config(tmp_path, raw_config)
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    revision = hashlib.sha256(config_path.read_bytes()).hexdigest()
    with pytest.raises(SetupCompletionStale):
        validate_current_completion(config_path, "f" * 64)

    (tmp_path / "agent-library" / "builder-blueprint" / "AGENTS.md").unlink()
    with pytest.raises(SetupCompletionUnavailable):
        validate_current_completion(config_path, revision)

    empty = tmp_path / "empty.yaml"
    unready = {**raw, "teams": {}}
    empty.write_text(yaml.safe_dump(unready, sort_keys=False), encoding="utf-8")
    with pytest.raises(SetupCompletionUnavailable):
        validate_current_completion(empty, hashlib.sha256(empty.read_bytes()).hexdigest())


def test_completion_connected_launch_failure_fallback_gets_a_fresh_scoped_capability(tmp_path, monkeypatch):
    launches = []

    def failing_factory(launch):
        launches.append(launch)
        raise ConnectedLaunchError("cleaned up", cleanup_confirmed=True)

    integration = _EnvironmentLaunchIntegration()
    config_path, root, integration, _process, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=failing_factory, integration=integration
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)
        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

    assert response.status_code == 200
    external = integration.requests[0]
    assert set(external.environment) == set(_COMPLETION_ENV_NAMES)
    assert external.environment["FLOWGENCY_SETUP_TOKEN"] != launches[0].env["FLOWGENCY_SETUP_TOKEN"]
    for secret in (external.environment["FLOWGENCY_SETUP_TOKEN"], launches[0].env["FLOWGENCY_SETUP_TOKEN"]):
        assert secret not in response.text
        assert secret not in integration.fallback_requests[0].prompt


@pytest.mark.parametrize("delivered", [False, True])
def test_external_fallback_appends_completion_warning_to_existing_notice(tmp_path, monkeypatch, delivered):
    notice = "Connected launch failed; cleanup confirmed."

    def failing_factory(launch):
        raise ConnectedLaunchError(notice, cleanup_confirmed=True)

    class DeliveryIntegration(_EnvironmentLaunchIntegration):
        def launch_interactive_setup(self, request):
            self.requests.append(request)
            return type("Result", (), {
                "fallback_command": self._fallback_command,
                "completion_environment_delivered": delivered,
            })()

    config_path, root, integration, _process, _manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=failing_factory, integration=DeliveryIntegration()
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)
        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

    expected = notice
    if not delivered:
        expected += (
            " Automatic setup completion is unavailable for this launch; "
            "return to the dashboard yourself once setup has finished."
        )
    assert response.status_code == 200
    assert response.context["launch_notice"] == expected
    assert expected in response.text
    token = integration.requests[0].environment["FLOWGENCY_SETUP_TOKEN"]
    assert token not in response.text
    assert token not in response.context["fallback_command"]


@pytest.mark.parametrize("connected", [False, True])
def test_setup_manual_fallback_discloses_unavailable_completion(tmp_path, monkeypatch, connected):
    if connected:
        config_path, root, integration, _process, _manager = _start_connected_session(
            tmp_path, monkeypatch, integration=_EnvironmentLaunchIntegration()
        )
    else:
        _configure_missing_config(tmp_path, monkeypatch)
        root = tmp_path / "Flowgency"
        integration = _LaunchIntegration()
        monkeypatch.setattr(
            "flowgency.web.routes.admin_teams.launchable_integrations",
            lambda integrations, data_root: (integration,),
        )
    with _local_client() as client:
        csrf = _setup_csrf(client)
        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

    assert response.status_code == 200
    assert response.context["fallback_completion_notice"] == (
        "Automatic setup completion is unavailable when you run this command manually; "
        "return to the dashboard yourself once setup has finished."
    )
    assert response.context["fallback_completion_notice"] in response.text
    assert 'id="fallback-completion-notice"' in response.text
    assert 'aria-describedby="fallback-completion-notice"' in response.text
    assert response.context["fallback_command"] == integration._fallback_command
    requests = integration.connected_requests if connected else integration.requests
    token = requests[0].environment["FLOWGENCY_SETUP_TOKEN"]
    assert token not in response.text
    assert token not in response.context["fallback_command"]


def test_external_launch_reports_automatic_completion_unavailable_without_capability_in_page(
    tmp_path, monkeypatch
):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"

    class NotCarrying(_LaunchIntegration):
        def launch_interactive_setup(self, request):
            self.requests.append(request)
            return type(
                "Result",
                (),
                {"fallback_command": self._fallback_command, "completion_environment_delivered": False},
            )()

    integration = NotCarrying()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (integration,),
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)
        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": _LOCAL_BASE_URL},
        )

    token = integration.requests[0].environment["FLOWGENCY_SETUP_TOKEN"]
    assert response.status_code == 200
    assert "Automatic setup completion is unavailable" in response.text
    assert token not in response.text


def test_completion_external_relaunch_passes_new_environment_and_rejects_old_token(tmp_path, monkeypatch, raw_config):
    from flowgency.configuration import ConfigStore

    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"
    relaunch_entered = threading.Event()
    release_relaunch = threading.Event()
    responses = []

    class GatedLaunch(_LaunchIntegration):
        def launch_interactive_setup(self, request):
            self.requests.append(request)
            if len(self.requests) == 2:
                relaunch_entered.set()
                assert release_relaunch.wait(5), "relaunch barrier was not released"
            return type("Result", (), {
                "fallback_command": self._fallback_command,
                "completion_environment_delivered": True,
            })()

    integration = GatedLaunch()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (integration,),
    )
    with _local_client() as client:
        csrf = _setup_csrf(client)
        form = {"data_root": str(root), "integration": "copilot", "setup_csrf": csrf}
        headers = {"Origin": _LOCAL_BASE_URL}
        assert client.post("/setup/launch", data=form, headers=headers).status_code == 200
        old = integration.requests[0].environment

        def relaunch():
            responses.append(client.post("/setup/launch", data=form, headers=headers))

        thread = threading.Thread(target=relaunch)
        thread.start()
        try:
            assert relaunch_entered.wait(5)
            config_path.write_text(
                yaml.safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
                encoding="utf-8",
            )
            revision = ConfigStore(config_path).load().revision
            body = {
                "launch_id": old["FLOWGENCY_SETUP_LAUNCH_ID"],
                "revision": revision,
                "scheduler_result": "manual-only",
                "all_questions_answered": True,
                "summary_delivered": True,
            }
            stale = client.post("/setup/session/completion", json=body, headers={
                "Authorization": f"Bearer {old['FLOWGENCY_SETUP_TOKEN']}",
            })
            assert stale.status_code == 401
            assert stale.json()["code"] == "invalid-credentials"
            new = integration.requests[1].environment
            assert new["FLOWGENCY_SETUP_LAUNCH_ID"] != old["FLOWGENCY_SETUP_LAUNCH_ID"]
            assert new["FLOWGENCY_SETUP_TOKEN"] != old["FLOWGENCY_SETUP_TOKEN"]
            release_relaunch.set()
            thread.join(timeout=10)
            assert not thread.is_alive()
            completed = client.post("/setup/session/completion", json={
                **body, "launch_id": new["FLOWGENCY_SETUP_LAUNCH_ID"],
            }, headers={"Authorization": f"Bearer {new['FLOWGENCY_SETUP_TOKEN']}"})
            assert completed.status_code == 200
            assert completed.json()["completion"]["phase"] == "complete"
            assert len(responses) == 1
            assert responses[0].status_code == 200
            for environment in (old, new):
                assert environment["FLOWGENCY_SETUP_TOKEN"] not in responses[0].text + stale.text + completed.text
        finally:
            release_relaunch.set()
            thread.join(timeout=10)


@pytest.mark.parametrize(
    ("base_url", "peer", "expected"),
    [
        ("http://localhost:8500", "127.0.0.1", "http://127.0.0.1:8500"),
        ("http://127.0.0.1:8123", "127.0.0.1", "http://127.0.0.1:8123"),
    ],
)
def test_completion_origin_is_normalized_to_a_literal_loopback_address(
    tmp_path, monkeypatch, base_url, peer, expected
):
    launches = []

    def factory(launch):
        launches.append(launch)
        return FakeProcess()

    config_path, root, integration, _process, manager = _start_connected_session(
        tmp_path, monkeypatch, process_factory=factory, integration=_EnvironmentLaunchIntegration()
    )
    with TestClient(app_mod.app, base_url=base_url, client=(peer, 50050)) as client:
        csrf = _setup_csrf(client)
        response = client.post(
            "/setup/launch",
            data={"data_root": str(root), "integration": "copilot", "setup_csrf": csrf},
            headers={"Origin": base_url},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert launches[0].env["FLOWGENCY_SETUP_ORIGIN"] == expected


def _wait_for_session_state(session, expected: str) -> None:
    for _ in range(500):
        if session.client.get("/setup/session/state").json()["state"] == expected:
            return
        threading.Event().wait(0.01)
    raise AssertionError(f"setup session never reached {expected}")


def _stranger_client() -> TestClient:
    return TestClient(app_mod.app, base_url=_LOCAL_BASE_URL, client=("127.0.0.1", 50060))


def test_ready_config_without_setup_attempt_still_opens_the_dashboard(
    tmp_path, monkeypatch, raw_config
):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    config_path.write_text(
        yaml.safe_dump(_materialize_ready_config(tmp_path, raw_config), sort_keys=False),
        encoding="utf-8",
    )
    with _local_client() as client:
        assert client.get("/setup/status").json() == {"state": "ready", "redirect": "/"}
        assert client.get("/setup", follow_redirects=False).headers["location"] == "/"


def test_acknowledged_completion_permits_status_and_entry_redirect(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)
    assert session.post(session.command(revision)).status_code == 200

    status = session.client.get("/setup/status").json()

    assert status["state"] == "ready"
    assert status["redirect"] == "/"
    assert status["completion"]["launch_id"] == session.launch_id
    assert status["completion"]["phase"] == "complete"
    assert set(status["completion"]) == {"launch_id", "phase", "message"}
    assert session.token not in str(status)
    assert session.client.get("/setup", follow_redirects=False).headers["location"] == "/"
    assert session.process.running is True


def test_session_state_reports_the_non_secret_completion_presentation(completion_session, raw_config):
    session = completion_session

    pending = session.client.get("/setup/session/state").json()
    assert pending["completion"]["phase"] == "pending"
    assert pending["completion"]["launch_id"] == session.launch_id

    assert session.post(session.command(session.write_ready_config(raw_config))).status_code == 200
    done = session.client.get("/setup/session/state")

    assert done.json()["completion"]["phase"] == "complete"
    assert set(done.json()["completion"]) == {"launch_id", "phase", "message"}
    assert session.token not in done.text


def test_acknowledged_revision_drift_blocks_redirect_again(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)
    assert session.post(session.command(revision)).status_code == 200
    session.config_path.write_text(
        session.config_path.read_text(encoding="utf-8") + "\n# edited after acknowledgement\n",
        encoding="utf-8",
    )

    status = session.client.get("/setup/status").json()

    assert status["state"] == "ready"
    assert status["completion"]["phase"] == "pending"
    assert "redirect" not in status
    assert session.client.get("/setup", follow_redirects=False).headers["location"] == "/setup/session"


def test_invalid_config_after_acknowledgement_never_redirects(completion_session, raw_config):
    session = completion_session
    revision = session.write_ready_config(raw_config)
    assert session.post(session.command(revision)).status_code == 200
    session.config_path.write_text("schema_version: [", encoding="utf-8")

    status = session.client.get("/setup/status").json()

    assert status["state"] != "ready"
    assert "redirect" not in status
    assert status["completion"]["phase"] == "pending"


def test_explicit_stop_never_counts_as_completion_but_stop_response_is_unchanged(
    completion_session, raw_config
):
    session = completion_session
    session.write_ready_config(raw_config)
    csrf = _setup_csrf(session.client)

    stop = session.client.post(
        "/setup/session/stop",
        data={"setup_csrf": csrf},
        headers={"Origin": _LOCAL_BASE_URL},
        follow_redirects=False,
    )
    status = session.client.get("/setup/status").json()

    assert stop.status_code == 303
    assert stop.headers["location"] == "/"
    assert status["state"] == "ready"
    assert status["completion"]["phase"] == "cancelled"
    assert "redirect" not in status
    assert session.client.get("/setup", follow_redirects=False).status_code == 200


def test_unexpected_exit_requires_attention_instead_of_navigating(completion_session, raw_config):
    session = completion_session
    session.write_ready_config(raw_config)
    session.process.running = False
    session.process.output.put(None)
    _wait_for_session_state(session, "exited")

    status = session.client.get("/setup/status").json()

    assert status["state"] == "ready"
    assert status["completion"]["phase"] == "attention"
    assert "redirect" not in status
    assert session.client.get("/setup/session", follow_redirects=False).status_code == 200


def test_other_browser_is_not_gated_by_the_owners_attempt(completion_session, raw_config):
    session = completion_session
    session.write_ready_config(raw_config)
    stranger = _stranger_client()
    forged = _stranger_client()
    forged.cookies.set("flowgency_setup", "forged.notarealsignature")

    for client in (stranger, forged):
        status = client.get("/setup/status").json()
        assert status == {"state": "ready", "redirect": "/"}
        assert client.get("/setup", follow_redirects=False).headers["location"] == "/"
    assert session.client.get("/setup/status").json()["completion"]["phase"] == "pending"


def test_session_view_keeps_inspection_intent_and_launch_identity(completion_session, raw_config):
    session = completion_session
    assert session.post(session.command(session.write_ready_config(raw_config))).status_code == 200

    waiting = session.client.get("/setup/session")
    inspection = session.client.get("/setup/session?view=inspection")

    assert waiting.status_code == inspection.status_code == 200
    assert 'data-setup-view="waiting"' in waiting.text
    assert 'data-setup-view="inspection"' in inspection.text
    assert f'data-launch-id="{session.launch_id}"' in inspection.text
    assert session.token not in waiting.text + inspection.text


def test_launch_form_does_not_bounce_to_dashboard_while_attempt_is_pending(
    completion_session, raw_config
):
    session = completion_session
    session.write_ready_config(raw_config)
    csrf = _setup_csrf(session.client)
    form = {
        "data_root": str(session.tmp_path / "Flowgency"),
        "integration": "copilot",
        "setup_csrf": csrf,
    }

    again = session.client.post(
        "/setup/launch", data=form, headers={"Origin": _LOCAL_BASE_URL}, follow_redirects=False
    )

    assert again.status_code == 303
    assert again.headers["location"] == "/setup/session"


def test_dashboard_terminal_link_opens_an_inspection_view(completion_session, raw_config):
    session = completion_session
    session.write_ready_config(raw_config)
    session.client.get("/setup/status")

    page = session.client.get("/newsletter/")

    assert 'href="/setup/session?view=inspection"' in page.text


# ── Shared team navigation: live snapshot of the base-page shell ────────────

_ROSTER_LIVE_URL = "/newsletter/agents?__live=1"
_NAVIGATION_REGIONS = [
    "navigation-teams",
    "navigation-primary",
    "navigation-workflows",
    "navigation-workspace",
]
_ROSTER_REGIONS = ["roster-summary", "roster-rows", "roster-empty"]


def _live_regions(response) -> dict[str, str]:
    return {region["key"]: region["html"] for region in response.json()["regions"]}


class _RegionExtractor(HTMLParser):
    """Inner HTML of each [data-live-region] root, sliced from the source markup."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self._html = html
        self._line_starts = [0] + [match.end() for match in re.finditer("\n", html)]
        self._open: tuple[str, str, int, int] | None = None
        self.regions: dict[str, str] = {}

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if self._open is None:
            key = dict(attrs).get("data-live-region")
            if key:
                start = self._offset() + len(self.get_starttag_text())
                self._open = (key, tag, 1, start)
        elif tag == self._open[1]:
            key, name, depth, start = self._open
            self._open = (key, name, depth + 1, start)

    def handle_endtag(self, tag):
        if self._open is None or tag != self._open[1]:
            return
        key, name, depth, start = self._open
        if depth == 1:
            self.regions[key] = self._html[start:self._offset()]
            self._open = None
        else:
            self._open = (key, name, depth - 1, start)


def _page_regions(html: str) -> dict[str, str]:
    extractor = _RegionExtractor(html)
    extractor.feed(html)
    return extractor.regions


def _squash(markup: str) -> str:
    return re.sub(r">\s+<", "><", re.sub(r"\s+", " ", markup)).strip()


def test_sidebar_live_snapshot_serves_shared_navigation_for_the_agents_roster(workflow_web_env):
    response = workflow_web_env.client.get(_ROSTER_LIVE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["binding"] == {
        "page": "agents", "team": "newsletter", "entity": None, "tab": None, "query": {}
    }
    assert body["structure"] == "agents:1"
    assert list(_live_regions(response)) == [*_NAVIGATION_REGIONS, *_ROSTER_REGIONS]
    assert response.headers["cache-control"] == "private, no-cache"


def test_sidebar_live_snapshot_matches_the_initial_html_regions(workflow_web_env):
    page = workflow_web_env.client.get("/newsletter/agents")
    snapshot = workflow_web_env.client.get(_ROSTER_LIVE_URL)

    initial = {key: _squash(html) for key, html in _page_regions(page.text).items()}
    live = {key: _squash(html) for key, html in _live_regions(snapshot).items()}
    assert set(initial) == {*_NAVIGATION_REGIONS, *_ROSTER_REGIONS}
    assert initial == live


def test_sidebar_roster_page_registers_the_live_page_with_its_status_shell(workflow_web_env):
    page = workflow_web_env.client.get("/newsletter/agents").text

    match = re.search(r'<script type="application/json" id="live-initial">(.*?)</script>', page, re.DOTALL)
    assert match is not None
    registration = json.loads(match.group(1))
    assert registration["url"] == _ROSTER_LIVE_URL
    assert registration["structure"] == "agents:1"
    assert registration["binding"]["page"] == "agents"
    assert "secret" not in match.group(1).lower()
    assert '<script src="/static/live-refresh.js"></script>' in page
    assert re.search(r'<div data-live-status role="status" hidden', page)
    assert "data-live-manual-refresh" in page


def test_sidebar_pages_without_a_live_policy_do_not_register(workflow_web_env):
    page = workflow_web_env.client.get("/newsletter/logs").text

    assert 'id="live-initial"' not in page
    assert "live-refresh.js" not in page
    assert "data-live-status" not in page


def test_sidebar_live_snapshot_unknown_team_is_not_found(workflow_web_env):
    assert workflow_web_env.client.get("/missing/agents?__live=1").status_code == 404


def test_sidebar_live_snapshot_is_conditional_and_changes_with_team_label(workflow_web_env):
    client = workflow_web_env.client
    first = client.get(_ROSTER_LIVE_URL)
    etag = first.headers["etag"]
    assert client.get(_ROSTER_LIVE_URL, headers={"If-None-Match": etag}).status_code == 304

    store = workflow_web_env.store
    store.patch(store.load().revision, lambda raw: raw["teams"]["support"].update(name="Support updated"))

    changed = client.get(_ROSTER_LIVE_URL, headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag
    assert "Support updated" in _live_regions(changed)["navigation-teams"]


def test_sidebar_live_snapshot_reflects_workflow_count_and_unavailability(workflow_web_env):
    client = workflow_web_env.client
    workflows = _live_regions(client.get(_ROSTER_LIVE_URL))["navigation-workflows"]
    assert 'data-workflow-state="count">0<' in _squash(workflows)

    workflow_web_env.seed_ticket()
    counted = _live_regions(client.get(_ROSTER_LIVE_URL))["navigation-workflows"]
    assert 'data-workflow-state="count">1<' in _squash(counted)

    shutil.rmtree(workflow_web_env.root_a)
    unavailable = _live_regions(client.get(_ROSTER_LIVE_URL))["navigation-workflows"]
    assert 'data-workflow-state="unavailable"' in unavailable
    assert 'data-workflow-state="count"' not in unavailable


def test_sidebar_live_snapshot_reflects_workspace_membership(workflow_web_env):
    client = workflow_web_env.client
    store = workflow_web_env.store
    assert 'data-live-key="nav:workspaces"' in _live_regions(client.get(_ROSTER_LIVE_URL))["navigation-workspace"]

    store.patch(store.load().revision, lambda raw: raw["teams"]["newsletter"].update(workspaces=[]))

    assert 'data-live-key="nav:workspaces"' not in _live_regions(client.get(_ROSTER_LIVE_URL))["navigation-workspace"]


def test_sidebar_live_snapshot_is_keyed_by_team_and_workflow_identity(workflow_web_env):
    regions = _live_regions(workflow_web_env.client.get(_ROSTER_LIVE_URL))

    assert 'data-live-key="team:newsletter"' in regions["navigation-teams"]
    assert 'data-live-key="team:support"' in regions["navigation-teams"]
    assert 'data-live-key="workflow:board-a"' in regions["navigation-workflows"]
    assert 'data-live-key="nav:agents"' in regions["navigation-primary"]
