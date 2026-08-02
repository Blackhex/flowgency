"""Tests for the admin group settings save/create with the permissions model."""

from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest
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
            self._current = {"attrs": attrs, "inputs": [], "selects": [], "options": []}
        elif self._current is not None:
            if tag == "input":
                self._current["inputs"].append(attrs)
            elif tag == "select":
                self._current["selects"].append(attrs)
            elif tag == "option":
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


def _make_client(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "groups" / "grp-state").mkdir(parents=True, exist_ok=True)
    raw["agency"]["title"] = "Agency"
    raw["agency"]["default_group"] = "grp"
    raw["agency"]["agent_library"] = str(tmp_path / "library")
    raw["agency"]["compilation_cache"] = str(tmp_path / "cache")
    raw["agency"]["memory_store"] = str(tmp_path / "memory")
    raw["groups"] = {
        "grp": {
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "default_integration": "copilot",
            "agents": [],
            "workspaces": [],
        }
    }
    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), ConfigStore(config_path)


def test_admin_org_save_persists_permission_mode(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "restricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["permissions"]["mode"] == "restricted"


def test_admin_org_save_sets_unrestricted_by_default(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["runtime"] = {
        "permissions": {"mode": "restricted", "rules": [{"path": "/old/root"}]}
    }
    snapshot.path.write_text(yaml.safe_dump(snapshot.raw, sort_keys=False), encoding="utf-8")
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "unrestricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["permissions"]["mode"] == "unrestricted"


def test_admin_org_create_sets_restricted_when_roots_given(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()
    (tmp_path / "repo").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": str(tmp_path / "repo"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["permissions"]["mode"] == "restricted"
    paths = [r.get("path") for r in saved["groups"]["new"]["runtime"]["permissions"]["rules"]]
    assert str(tmp_path / "repo") in paths


def test_admin_org_create_sets_unrestricted_when_no_roots(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["new"]["runtime"]["permissions"]["mode"] == "unrestricted"


def test_admin_org_create_multiline_roots(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "cowork").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": "[]",
            "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    rules = saved["groups"]["new"]["runtime"]["permissions"]["rules"]
    paths = [r.get("path") for r in rules]
    assert str(tmp_path / "repo") in paths
    assert str(tmp_path / "cowork") in paths


def test_admin_org_save_preserves_extension_keys(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    snapshot = store.load()
    snapshot.raw["groups"]["grp"]["group_extension"] = {"theme": "sunset"}
    snapshot.raw["groups"]["grp"]["runtime"] = {
        "timeout": 1200,
        "runtime_extension": {"preserve": True},
        "permissions": {"mode": "unrestricted", "rules": []},
    }
    snapshot.raw["groups"]["grp"]["dispatch"] = {"enabled": False}
    snapshot.raw["groups"]["grp"]["workspaces"] = [
        {
            "name": "Archive",
            "type": "tmux",
            "config": {"script_path": "workspace.sh"},
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
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": '[{"name":"Primary","type":"tmux","config":{"script_path":"primary.sh"},"workspace_extension":{"preserve":true}}]',
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "restricted",
            "dispatch_enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["group_extension"] == {"theme": "sunset"}
    assert saved["groups"]["grp"]["runtime"]["runtime_extension"] == {"preserve": True}
    assert saved["groups"]["grp"]["workspaces"][0]["workspace_extension"] == {"preserve": True}


def test_admin_org_save_updates_dispatch_enabled(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "unrestricted",
            "dispatch_enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["dispatch"]["enabled"] is True


def test_admin_org_save_updates_timeout(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "[]",
            "default_integration": "copilot",
            "runtime_timeout": "3600",
            "permission_mode": "unrestricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["runtime"]["timeout"] == 3600


def test_admin_org_save_preserves_workspaces(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    revision = store.load().revision

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": revision,
            "name": "Grp",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": '[{"name":"Main","type":"tmux","config":{}}]',
            "default_integration": "copilot",
            "runtime_timeout": "1800",
            "permission_mode": "unrestricted",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = store.load().raw
    assert saved["groups"]["grp"]["workspaces"] == [
        {"name": "Main", "type": "tmux", "config": {}}
    ]


def test_admin_org_create_calls_one_patch_and_persists_full_group_state(
    tmp_path, monkeypatch, raw_config
):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "cowork").mkdir()
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
            "revision": store.load().revision,
            "key": "new",
            "name": "New Group",
            "workspace_path": str(tmp_path / "new-workspace"),
            "path": str(tmp_path / "new-agents"),
            "workspaces_json": '[{"name":"Primary","type":"tmux","config":{"script_path":"tmux-agents.sh"}}]',
            "sandbox_root": f"{tmp_path / 'repo'}\n{tmp_path / 'cowork'}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == 1

    saved = store.load().raw["groups"]["new"]
    assert saved["name"] == "New Group"
    assert saved["workspace_path"] == str(tmp_path / "new-workspace")
    assert saved["path"] == str(tmp_path / "new-agents")
    assert saved["default_integration"] == "claude-code"
    assert saved["dispatch"] == {"enabled": False}
    assert saved["runtime"]["permissions"]["mode"] == "restricted"
    rules = saved["runtime"]["permissions"]["rules"]
    paths = [r["path"] for r in rules]
    assert str(tmp_path / "repo") in paths
    assert str(tmp_path / "cowork") in paths
    assert saved["workspaces"] == [
        {
            "name": "Primary",
            "type": "tmux",
            "config": {"script_path": "tmux-agents.sh"},
        }
    ]
    assert saved["agents"] == []


def test_admin_org_save_invalid_workspaces_is_all_or_nothing(tmp_path, monkeypatch, raw_config):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    before = store.load()

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": before.revision,
            "name": "Changed",
            "workspace_path": str(tmp_path / "workspace"),
            "path": str(tmp_path / "groups" / "grp-state"),
            "workspaces_json": "not-json",
            "default_integration": "copilot",
            "runtime_timeout": "9999",
            "permission_mode": "restricted",
            "dispatch_enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    after = store.load().raw
    assert after == before.raw


@pytest.mark.parametrize(
    ("workspace_path", "group_path", "diagnostic"),
    [
        (
            "missing-new-workspace",
            "new-group-state",
            "Configured path must exist as a directory",
        ),
        (
            "workspace",
            "workspace",
            "overlaps",
        ),
    ],
)
def test_admin_org_create_invalid_paths_rerender_submitted_form_without_writing(
    tmp_path,
    monkeypatch,
    raw_config,
    workspace_path,
    group_path,
    diagnostic,
):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    before = store.load()
    submitted_workspace = tmp_path / workspace_path
    submitted_group = tmp_path / group_path

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": before.revision,
            "key": "submitted-group",
            "name": "Submitted Group",
            "workspace_path": str(submitted_workspace),
            "path": str(submitted_group),
            "default_integration": "copilot",
            "workspaces_json": "[]",
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert str(submitted_workspace) in response.text
    assert str(submitted_group) in response.text
    assert f'name="revision" value="{before.revision}"' in response.text
    assert diagnostic in response.text
    assert "submitted-group" not in store.load().raw["groups"]
    assert store.load().raw == before.raw


def test_admin_org_create_uses_selected_default_integration_and_rejects_unknown(
    tmp_path, monkeypatch, raw_config
):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    (tmp_path / "new-agents").mkdir()
    (tmp_path / "new-workspace").mkdir()

    response = client.post(
        "/admin/orgs/create",
        data={
            "revision": store.load().revision,
            "key": "copilot-group",
            "name": "Copilot Group",
            "workspace_path": str(tmp_path / "new-workspace"),
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
            "workspace_path": str(tmp_path / "new-workspace"),
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
    monkeypatch, tmp_path, raw_config
):
    client, _ = _make_client(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/orgs/new")

    assert response.status_code == 200
    forms = [form for form in _parse_forms(response.text) if form["attrs"].get("action") == "/admin/orgs/create"]
    assert len(forms) == 1
    assert any(option.get("value") == "copilot" for option in forms[0]["options"])


@pytest.mark.parametrize(
    ("workspace_path", "group_path", "permission_mode", "diagnostic"),
    [
        (
            "nonexistent-workspace",
            "groups/grp-state",
            "unrestricted",
            "Configured path must exist as a directory",
        ),
        (
            "workspace",
            "workspace",
            "restricted",
            "overlaps",
        ),
    ],
)
def test_admin_org_save_invalid_paths_rerender_submitted_form_without_writing(
    tmp_path,
    monkeypatch,
    raw_config,
    workspace_path,
    group_path,
    permission_mode,
    diagnostic,
):
    client, store = _make_client(monkeypatch, tmp_path, raw_config)
    before = store.load()
    submitted_workspace = tmp_path / workspace_path
    submitted_group = tmp_path / group_path

    response = client.post(
        "/admin/orgs/grp/save",
        data={
            "revision": before.revision,
            "name": "Grp",
            "workspace_path": str(submitted_workspace),
            "path": str(submitted_group),
            "default_integration": "copilot",
            "workspaces_json": "[]",
            "runtime_timeout": "1800",
            "permission_mode": permission_mode,
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert str(submitted_workspace) in response.text
    assert str(submitted_group) in response.text
    assert f'name="revision" value="{before.revision}"' in response.text
    assert diagnostic in response.text
    assert store.load().raw == before.raw
