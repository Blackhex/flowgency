from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency.configuration.store import ConfigStore
from agency.configuration.store import config_revision
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


def test_setup_post_creates_strict_canonical_group_without_scanning_agents(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    config_path.write_text(
        "agency:\n  title: Agency\n  default_group: ''\ngroups: {}\n",
        encoding="utf-8",
    )
    app_mod.refresh_services()

    group_path = tmp_path / "groups" / "newsletter"
    library = tmp_path / "agent-library"
    cache = tmp_path / "compiled-agents"
    memory = tmp_path / "memory-store"
    group_path.mkdir(parents=True)
    library.mkdir(parents=True)
    client = TestClient(app_mod.app)
    revision = ConfigStore(config_path).inspect().revision

    response = client.post(
        "/setup",
        data={
            "expected_revision": revision,
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
    assert "schema_version" not in saved
    assert saved["agency"]["default_group"] == "newsletter"
    assert saved["agency"]["agent_library"] == str(library)
    assert saved["agency"]["compilation_cache"] == str(cache)
    assert saved["agency"]["memory_store"] == str(memory)
    assert saved["groups"]["newsletter"]["agents"] == []
    assert group_path.is_dir()


def test_setup_page_surfaces_structured_startup_diagnostics(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency:\n  title: Agency\ngroups: {}\n", encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert "schema_version" not in response.text
    assert "agent_library" in response.text
    assert "compilation_cache" in response.text
    assert "memory_store" in response.text


def test_setup_page_includes_expected_revision_for_existing_bootstrap_config(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    original = b"agency:\n  title: Agency\ngroups: {}\n"
    config_path.write_bytes(original)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    assert 'name="expected_revision"' in response.text
    assert f'value="{config_revision(original)}"' in response.text


def test_setup_form_has_distinct_group_key_and_expected_revision_inputs(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    original = b"agency:\n  title: Agency\ngroups: {}\n"
    config_path.write_bytes(original)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    response = client.get("/setup")

    assert response.status_code == 200
    forms = [form for form in _parse_forms(response.text) if form["attrs"].get("action") == "/setup"]
    assert len(forms) == 1
    setup_form = forms[0]
    inputs = {input_["name"]: input_ for input_ in setup_form["inputs"] if input_.get("name")}

    assert inputs["group_key"]["value"] == ""
    assert inputs["expected_revision"]["value"] == config_revision(original)
    assert inputs["group_key"] is not inputs["expected_revision"]

    (tmp_path / "groups" / "newsletter").mkdir(parents=True)
    (tmp_path / "agent-library").mkdir(parents=True)

    payload = {
        "expected_revision": inputs["expected_revision"]["value"],
        "group_key": "newsletter",
        "group_name": "Newsletter",
        "path": str(tmp_path / "groups" / "newsletter"),
        "agent_library": str(tmp_path / "agent-library"),
        "compilation_cache": str(tmp_path / "compiled-agents"),
        "memory_store": str(tmp_path / "memory-store"),
        "workspace_name": "Terminal Grid",
        "workspace_type": "tmux",
        "workspace_config": '{"script_path": "tmux-agents.sh"}',
    }

    first = client.post("/setup", data=payload, follow_redirects=False)
    assert first.status_code == 303

    second = client.post("/setup", data=payload, follow_redirects=False)
    assert second.status_code == 409
    assert "Configuration changed" in second.text


def test_setup_post_write_failure_preserves_existing_bytes(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    original = b"agency:\n  title: Agency\ngroups: {}\n"
    config_path.write_bytes(original)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()

    original_encode = ConfigStore._encode

    def boom(self, raw):
        raise RuntimeError("encode failed")

    monkeypatch.setattr(ConfigStore, "_encode", boom)
    client = TestClient(app_mod.app, raise_server_exceptions=False)

    response = client.post(
        "/setup",
        data={
            "expected_revision": config_revision(original),
            "group_key": "newsletter",
            "group_name": "Newsletter",
            "path": str(tmp_path / "groups" / "newsletter"),
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(tmp_path / "memory-store"),
            "workspace_name": "Terminal Grid",
            "workspace_type": "tmux",
            "workspace_config": '{"script_path": "tmux-agents.sh"}',
        },
        follow_redirects=False,
    )

    monkeypatch.setattr(ConfigStore, "_encode", original_encode)

    assert response.status_code == 500
    assert config_path.read_bytes() == original


def test_setup_post_conflict_returns_409_and_preserves_concurrent_bytes(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    original = b"agency:\n  title: Agency\ngroups: {}\n"
    concurrent = b"agency:\n  title: Changed elsewhere\ngroups: {}\n"
    config_path.write_bytes(original)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    client = TestClient(app_mod.app)

    get_response = client.get("/setup")
    assert get_response.status_code == 200
    assert 'name="expected_revision"' in get_response.text

    config_path.write_bytes(concurrent)

    response = client.post(
        "/setup",
        data={
            "expected_revision": config_revision(original),
            "group_key": "newsletter",
            "group_name": "Newsletter",
            "path": str(tmp_path / "groups" / "newsletter"),
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(tmp_path / "memory-store"),
            "workspace_name": "Terminal Grid",
            "workspace_type": "tmux",
            "workspace_config": '{"script_path": "tmux-agents.sh"}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert config_path.read_bytes() == concurrent