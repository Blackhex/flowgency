from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.configuration import ConfigStore


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._current = {"attrs": attrs, "inputs": [], "options": []}
        elif tag == "input" and self._current is not None:
            self._current["inputs"].append(attrs)
        elif tag == "option" and self._current is not None:
            self._current["options"].append(attrs)

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


def _make_client(monkeypatch, tmp_path, canonical_raw_config):
    raw = deepcopy(canonical_raw_config)
    raw["agency"]["title"] = "Agency"
    raw["agency"]["default_group"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "library")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "memory")
    raw["groups"] = {
        "grp": {
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "default_integration": "copilot",
            "agents": [],
            "workspaces": [],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), ConfigStore(config_path)


def test_admin_org_save_persists_sandbox_root(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "restricted",
            "sandbox_roots": str(tmp_path / "repo"),
            "tool_mode": "all",
            "tool_names": "",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["sandbox"] == {
        "mode": "restricted",
        "roots": [str(tmp_path / "repo")],
    }


def test_admin_org_save_clears_sandbox_root_when_empty(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["/old/root"]}
    }
    snapshot.path.write_text(yaml.safe_dump(snapshot.raw, sort_keys=False), encoding="utf-8")
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "unrestricted",
            "sandbox_roots": "",
            "tool_mode": "all",
            "tool_names": "",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["sandbox"] == {"mode": "unrestricted", "roots": []}


def test_admin_org_create_persists_sandbox_root(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    (tmp_path / "new-agents").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "key": "new",
            "name": "New Group",
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": str(tmp_path / "repo"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["sandbox"] == {
        "mode": "restricted",
        "roots": [str(tmp_path / "repo")],
    }


def test_admin_org_create_omits_sandbox_root_when_empty(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    (tmp_path / "new-agents").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "key": "new",
            "name": "New Group",
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["sandbox"] == {
        "mode": "unrestricted",
        "roots": [],
    }


def test_admin_org_save_persists_multiline_sandbox_root_as_list(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "restricted",
            "sandbox_roots": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
            "tool_mode": "all",
            "tool_names": "",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["sandbox"] == {
        "mode": "restricted",
        "roots": [str(tmp_path / "repo"), str(tmp_path / "cowork")],
    }


def test_admin_org_save_single_line_sandbox_root_stays_string(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "restricted",
            "sandbox_roots": str(tmp_path / "repo"),
            "tool_mode": "all",
            "tool_names": "",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["sandbox"] == {
        "mode": "restricted",
        "roots": [str(tmp_path / "repo")],
    }


def test_admin_org_save_persists_allowed_tools(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "restricted",
            "sandbox_roots": str(tmp_path / "repo"),
            "tool_mode": "allowlist",
            "tool_names": "shell\nwrite",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["tools"] == {"mode": "allowlist", "names": ["shell", "write"]}


def test_admin_org_save_clears_allowed_tools_when_none_checked(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["runtime"] = {"tools": {"mode": "allowlist", "names": ["shell"]}}
    snapshot.path.write_text(yaml.safe_dump(snapshot.raw, sort_keys=False), encoding="utf-8")
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "path": str(tmp_path / "agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "unrestricted",
            "sandbox_roots": "",
            "tool_mode": "all",
            "tool_names": "",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["tools"] == {"mode": "all", "names": []}


def test_admin_org_save_preserves_unknown_runtime_and_group_extension_keys(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["group_extension"] = {"theme": "sunset"}
    snapshot.raw["groups"]["grp"]["runtime"] = {
        "timeout": 1200,
        "runtime_extension": {"preserve": True},
        "sandbox": {"mode": "restricted", "roots": ["superseded"], "sandbox_extension": {"preserve": True}},
        "tools": {"mode": "allowlist", "names": ["shell"], "tools_extension": {"preserve": True}},
    }
    snapshot.raw["groups"]["grp"]["dispatch"] = {
        "enabled": False,
        "daily_limit": 10,
    }
    snapshot.raw["groups"]["grp"]["workspaces"] = [
        {
            "name": "superseded",
            "type": "tmux",
            "config": {"script_path": "superseded.sh"},
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
            "path": str(tmp_path / "agents"),
            "workspaces_json": '[{"name":"Primary","type":"tmux","config":{"script_path":"primary.sh"},"workspace_extension":{"preserve":true}}]',
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "sandbox_mode": "restricted",
            "sandbox_roots": str(tmp_path / "repo"),
            "tool_mode": "allowlist",
            "tool_names": "shell",
            "dispatch_enabled": "on",
            "daily_limit": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["group_extension"] == {"theme": "sunset"}
    assert saved["groups"]["grp"]["runtime"]["runtime_extension"] == {"preserve": True}
    assert saved["groups"]["grp"]["runtime"]["sandbox"]["sandbox_extension"] == {"preserve": True}
    assert saved["groups"]["grp"]["runtime"]["tools"]["tools_extension"] == {"preserve": True}
    assert saved["groups"]["grp"]["workspaces"][0]["workspace_extension"] == {"preserve": True}


def test_admin_org_create_persists_multiline_and_tools(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    (tmp_path / "new-agents").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "key": "new",
            "name": "New Group",
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
            "allowed_tools": ["shell", "write"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["sandbox"] == {
        "mode": "restricted",
        "roots": [str(tmp_path / "repo"), str(tmp_path / "cowork")],
    }
    assert saved["groups"]["new"]["runtime"]["tools"] == {
        "mode": "allowlist",
        "names": ["shell", "write"],
    }


def test_admin_org_create_uses_selected_default_integration_and_rejects_unknown(
    tmp_path, monkeypatch, canonical_raw_config
):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    (tmp_path / "new-agents").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "key": "copilot-group",
            "name": "Copilot Group",
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store.load().raw["groups"]["copilot-group"]["default_integration"] == "copilot"

    bad = client.post(
        "/admin/orgs/create",
        data={
            "key": "bad-group",
            "name": "Bad Group",
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "default_integration": "not-registered",
        },
        follow_redirects=False,
    )

    assert bad.status_code == 409
    assert "not-registered" in bad.text
    assert 'name="default_integration"' in bad.text
    assert "selected" in bad.text
    assert "bad-group" not in store.load().raw["groups"]


def test_admin_org_create_form_parser_smoke_preserves_default_integration_select(
    monkeypatch, tmp_path, canonical_raw_config
):
    client, _ = _make_client(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/admin/orgs/new")

    assert response.status_code == 200
    forms = [form for form in _parse_forms(response.text) if form["attrs"].get("action") == "/admin/orgs/create"]
    assert len(forms) == 1
    assert any(option.get("value") == "copilot" for option in forms[0]["options"])


def test_admin_org_create_calls_one_patch_and_persists_full_group_state(
    tmp_path, monkeypatch, canonical_raw_config
):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    (tmp_path / "new-agents").mkdir()
    calls = 0
    original_patch = ConfigStore.patch

    def patched_patch(self, expected_revision, patcher):
        nonlocal calls
        if self.path == store.path:
            calls += 1
        return original_patch(self, expected_revision, patcher)

    monkeypatch.setattr(ConfigStore, "patch", patched_patch)

    response = client.post(
        "/admin/orgs/create",
        data={
            "key": "new",
            "name": "New Group",
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": '[{"name":"Primary","type":"tmux","config":{"script_path":"tmux-agents.sh"}}]',
            "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
            "allowed_tools": ["shell", "write"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == 1

    saved = store.load().raw["groups"]["new"]
    assert saved["name"] == "New Group"
    assert saved["path"] == str(tmp_path / "new-agents")
    assert saved["default_integration"] == "claude-code"
    assert saved["dispatch"] == {"enabled": False, "daily_limit": 20}
    assert saved["runtime"]["sandbox"] == {
        "mode": "restricted",
        "roots": [str(tmp_path / "repo"), str(tmp_path / "cowork")],
    }
    assert saved["runtime"]["tools"] == {
        "mode": "allowlist",
        "names": ["shell", "write"],
    }
    assert saved["workspaces"] == [
        {
            "name": "Primary",
            "type": "tmux",
            "config": {"script_path": "tmux-agents.sh"},
        }
    ]
    assert saved["agents"] == []


def test_admin_org_save_invalid_workspaces_is_all_or_nothing(tmp_path, monkeypatch, canonical_raw_config):
    client, store = _make_client(monkeypatch, tmp_path, canonical_raw_config)
    before = store.load()

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": before.revision,
            "name": "Changed",
            "path": str(tmp_path / "changed"),
            "workspaces_json": "not-json",
            "default_integration": "claude-code",
            "runtime_timeout": "9999",
            "sandbox_mode": "restricted",
            "sandbox_roots": str(tmp_path / "repo"),
            "tool_mode": "allowlist",
            "tool_names": "shell",
            "dispatch_enabled": "on",
            "daily_limit": "3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    after = store.load().raw
    assert after == before.raw
