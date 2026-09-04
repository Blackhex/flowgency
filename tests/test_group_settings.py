from __future__ import annotations

import shutil
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
    library_root = tmp_path / "agent-library"
    library_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "newsletter").mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "newsletter-state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repo-root").mkdir(parents=True, exist_ok=True)
    raw["agency"]["title"] = "Agency"
    raw["agency"]["default_team"] = "newsletter"
    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(tmp_path / "compiled-agents")
    raw["agency"]["memory_store"] = str(tmp_path / "memory-store")
    raw["agency"]["prompt_store"] = str(tmp_path / "prompts")
    raw["teams"]["newsletter"]["workspace_path"] = str(
        tmp_path / "workspace" / "newsletter"
    )
    raw["teams"]["newsletter"]["path"] = str(
        tmp_path / "groups" / "newsletter-state"
    )
    raw["teams"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "permissions": {
            "mode": "restricted",
            "rules": [
                {"path": str(tmp_path / "repo-root"), "tools": ["shell"]},
            ],
        },
    }
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    for agent in raw["teams"]["newsletter"].get("agents", []):
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
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), ConfigStore(config_path)


def test_group_settings_has_defaults_and_manage_agents_link(monkeypatch, tmp_path, raw_config):
    client, _ = _make_client(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/teams/newsletter/edit")

    assert response.status_code == 200
    assert "Runtime defaults" in response.text
    assert 'href="/newsletter/agents"' in response.text
    assert 'name="workspace_path"' in response.text
    assert 'name="path"' in response.text
    assert "Workspace path" in response.text
    assert "Team path" in response.text
    assert "shared/" not in response.text
    assert "/initialize" not in response.text
    assert "Agent Roster" not in response.text
    assert "Dispatch Schedule" not in response.text
    assert "Auto-detect" not in response.text


def test_admin_groups_lists_workspace_and_team_paths_without_initialize_action(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _ = _make_client(monkeypatch, tmp_path, raw_config)
    group_root = tmp_path / "groups" / "newsletter-state"
    shutil.rmtree(group_root)

    response = client.get("/admin/teams")

    assert response.status_code == 200
    assert "Workspace path" in response.text
    assert "Team path" in response.text
    assert str(tmp_path / "workspace" / "newsletter") in response.text
    assert str(group_root) in response.text
    assert "/initialize" not in response.text
    assert "Record directories missing" in response.text
    assert "Path does not exist" not in response.text


def test_stale_group_save_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    stale = store.load().revision

    store.patch(
        stale,
        lambda raw: raw["teams"]["newsletter"].__setitem__("name", "Elsewhere"),
    )

    response = client.post(
        "/admin/teams/newsletter/save",
        data={
            "revision": stale,
            "name": "Newsletter",
            "workspace_path": str(tmp_path / "workspace" / "newsletter"),
            "path": str(tmp_path / "groups" / "newsletter-state"),
            "default_integration": "claude-code",
            "runtime_timeout": "1800",
            "permission_mode": "restricted",
            "dispatch_enabled": "on",
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
    response = client.get("/admin/teams/new")
    forms = [
        form
        for form in _parse_forms(response.text)
        if form["attrs"].get("action") == "/admin/teams/create"
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
        "/admin/teams/create",
        data={
            "revision": stale,
            "key": "new-group",
            "name": "New Team",
            "workspace_path": str(tmp_path / "workspace" / "new-group"),
            "path": str(tmp_path / "groups" / "new-group-state"),
            "default_integration": "copilot",
            "workspaces_json": "[]",
        },
        follow_redirects=False,
    )

    assert inputs["revision"]["value"] == stale
    assert response.status_code == 409
    assert "new-group" not in store.load().config.teams


def test_setup_launch_preserves_existing_bootstrap_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = b"schema_version: 3\nagency:\n  title: Agency\ngroups: {}\n"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    config_path.write_bytes(original)
    app_mod.refresh_services()

    data_root = tmp_path / "Agency"
    data_root.mkdir()
    integration = _LauncherIntegration()
    monkeypatch.setattr(
        "agency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={
            "data_root": str(data_root.resolve()),
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
    config_path.write_text("schema_version: 3\nagency:\n  title: Agency\ngroups: {}\n", encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert "Startup diagnostics" in response.text
    assert "agency data root" in response.text.lower()
    assert 'name="agent_library"' not in response.text
    assert 'name="workspace_config"' not in response.text


def test_setup_page_includes_launcher_fields_for_existing_bootstrap_config(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    config_path.write_bytes(b"schema_version: 3\nagency:\n  title: Agency\ngroups: {}\n")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert 'action="/setup/launch"' in response.text
    assert 'name="data_root"' in response.text
    assert 'name="integration"' in response.text
    assert 'name="expected_revision"' not in response.text


def test_setup_form_posts_only_launcher_inputs(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    config_path.write_bytes(b"schema_version: 3\nagency:\n  title: Agency\ngroups: {}\n")
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

    assert "data_root" in inputs
    assert "project_dir" not in inputs
    assert "team_key" not in inputs
    assert "expected_revision" not in inputs