from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency.configuration.store import ConfigStore
from agency import app as app_mod


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._current = {"attrs": attrs, "inputs": []}
        elif tag == "input" and self._current is not None:
            self._current["inputs"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _parse_forms(html: str):
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


class _LauncherIntegration:
    name = "copilot"
    display_name = "GitHub Copilot"

    def __init__(self, fallback_command: str = "copilot -C C:\\project") -> None:
        self.fallback_command = fallback_command
        self.requests = []

    def launch_interactive_setup(self, request):
        self.requests.append(request)
        return type(
            "LaunchResult",
            (),
            {"fallback_command": self.fallback_command},
        )()


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _make_client(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    (tmp_path / "agent-library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "newsletter").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo-root").mkdir(parents=True, exist_ok=True)
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
    app_mod.refresh_services()
    return TestClient(app_mod.app), ConfigStore(config_path)


def test_group_settings_has_defaults_and_manage_agents_link(monkeypatch, tmp_path, raw_config):
    client, _ = _make_client(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/orgs/newsletter/edit")

    assert response.status_code == 200
    assert "Runtime defaults" in response.text
    assert 'href="/newsletter/agents"' in response.text
    assert "Agent Roster" not in response.text
    assert "Dispatch Schedule" not in response.text
    assert "Auto-detect" not in response.text


def test_stale_group_save_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
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


def test_stale_group_create_returns_conflict_without_writing_group(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    response = client.get("/admin/orgs/new")
    forms = [
        form
        for form in _parse_forms(response.text)
        if form["attrs"].get("action") == "/admin/orgs/create"
    ]
    inputs = {
        item["name"]: item
        for item in forms[0]["inputs"]
        if item.get("name")
    }
    stale = store.load().revision
    store.patch(
        stale,
        lambda raw: raw["agency"].__setitem__("title", "Elsewhere"),
    )

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": stale,
            "key": "new-group",
            "name": "New Group",
            "path": str(tmp_path / "groups" / "new-group"),
            "default_integration": "copilot",
            "workspaces_json": "[]",
        },
        follow_redirects=False,
    )

    assert inputs["revision"]["value"] == stale
    assert response.status_code == 409
    assert "new-group" not in store.load().config.groups


def test_setup_launch_preserves_existing_bootstrap_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = b"agency:\n  title: Agency\ngroups: {}\n"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    config_path.write_bytes(original)
    app_mod.refresh_services()

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    integration = _LauncherIntegration()
    monkeypatch.setattr(
        "agency.web.routes.admin_groups.launchable_integrations",
        lambda integrations, project_dir: (integration,),
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={
            "project_dir": str(project_dir.resolve()),
            "integration": "copilot",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Waiting for setup to complete" in response.text
    assert config_path.read_bytes() == original
    assert integration.requests[0].config_path == config_path.resolve()


def test_setup_page_surfaces_structured_startup_diagnostics(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency:\n  title: Agency\ngroups: {}\n", encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert "Startup diagnostics" in response.text
    assert "project folder" in response.text.lower()
    assert 'name="agent_library"' not in response.text
    assert 'name="workspace_config"' not in response.text


def test_setup_page_includes_launcher_fields_for_existing_bootstrap_config(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    config_path.write_bytes(b"agency:\n  title: Agency\ngroups: {}\n")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert 'action="/setup/launch"' in response.text
    assert 'name="project_dir"' in response.text
    assert 'name="integration"' in response.text
    assert 'name="expected_revision"' not in response.text


def test_setup_form_posts_only_launcher_inputs(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    config_path.write_bytes(b"agency:\n  title: Agency\ngroups: {}\n")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    forms = [
        form
        for form in _parse_forms(response.text)
        if form["attrs"].get("action") == "/setup/launch"
    ]
    assert len(forms) == 1
    setup_form = forms[0]
    inputs = {input_["name"]: input_ for input_ in setup_form["inputs"] if input_.get("name")}

    assert "project_dir" in inputs
    assert "group_key" not in inputs
    assert "expected_revision" not in inputs