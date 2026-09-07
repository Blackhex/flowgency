from __future__ import annotations

import json
import re

import yaml

import flowgency.app as app_mod
from flowgency.configuration import ConfigStore
from flowgency.integrations import get_integration
from flowgency.integrations.tool_catalog import ToolCatalog, ToolDescriptor, catalog_id
from tests.test_agent_detail import _seed_app


def initial_payload(html: str) -> dict:
    match = re.search(
        r'<script id="permissions-initial" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def _catalog(*, warning: str | None = None, complete: bool = True) -> ToolCatalog:
    return ToolCatalog(
        integration="copilot",
        version="test-catalog",
        complete=complete,
        warning=warning,
        tools=(
            ToolDescriptor("read", ("path",)),
            ToolDescriptor("search", ("path",)),
            ToolDescriptor("write", ("path",)),
            ToolDescriptor("shell", ("no_path",)),
        ),
    )


def _pin_catalog(monkeypatch, catalog: ToolCatalog | None = None) -> ToolCatalog:
    pinned = catalog or _catalog()
    monkeypatch.setattr(type(get_integration("copilot")), "permission_tool_catalog", lambda self: pinned)
    monkeypatch.setattr(type(get_integration("script")), "permission_tool_catalog", lambda self: pinned)
    return pinned


def _revision(config_path) -> str:
    return ConfigStore(config_path).load().revision


def test_permissions_owns_the_agent_editor(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/permissions")

    assert response.status_code == 200
    assert 'id="permissions-initial"' in response.text
    assert ">Mode<" in response.text
    assert ">Rules<" in response.text
    assert "permission_rules_yaml" not in response.text
    profile = client.get("/newsletter/agents/advisor/profile").text
    runtime = client.get("/newsletter/agents/advisor/runtime").text
    assert 'name="can_write"' not in profile
    assert "permission_rules_yaml" not in runtime
    assert 'name="timeout"' in runtime


def test_permissions_preview_updates_summary_without_writing(monkeypatch, tmp_path, raw_config):
    catalog = _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/permissions")
    payload = initial_payload(page.text)["draft"]
    before = config_path.read_bytes()
    payload["draft_version"] = 1
    payload["draft"]["rules"][0]["path"] = str(tmp_path / "edited-preview")

    response = client.post(
        "/newsletter/agents/advisor/permissions/preview",
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft_version"] == 1
    assert body["revision"] == _revision(config_path)
    assert body["catalog_id"] == catalog_id(catalog)
    assert body["issues"] == []
    assert body["summary_html"] is not None
    assert "edited-preview" in body["summary_html"]
    assert config_path.read_bytes() == before


def test_permissions_preview_allows_default_incomplete_copilot_catalog(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/permissions")
    payload = initial_payload(page.text)["draft"]
    payload["draft_version"] = 7

    response = client.post(
        "/newsletter/agents/advisor/permissions/preview",
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft_version"] == 7
    assert body["issues"] == []
    assert body["summary_html"] is not None
    assert "Configured policy" in body["summary_html"]
    assert "Actual enforcement depends on the integration." in body["summary_html"]


def test_permissions_preview_rejects_malformed_draft_version_with_422(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload["draft_version"] = "bad"

    response = client.post(
        "/newsletter/agents/advisor/permissions/preview",
        json=payload,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid-request"
    assert body["summary_html"] is None
    assert any(issue["field"] == "draft_version" for issue in body["issues"])


def test_permissions_save_updates_agent_policy_and_redirects(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload["draft_version"] = 2
    save_path = tmp_path / "edited-save"
    save_path.mkdir()
    payload["draft"]["rules"][0]["path"] = str(save_path)

    response = client.post(
        "/newsletter/agents/advisor/permissions",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents/advisor/permissions"
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["teams"]["newsletter"]["agents"][0]["permissions"]["rules"][0]["path"] == str(save_path)


def test_permissions_save_conflict_retains_submitted_draft(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload["draft_version"] = 4
    payload["draft"]["rules"][0]["path"] = str(tmp_path / "stale-draft")
    stale_revision = payload["revision"]
    before = config_path.read_bytes()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["flowgency"]["title"] = "New title elsewhere"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    after = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/permissions",
        data={"payload": json.dumps(payload)},
    )

    assert before != after
    assert response.status_code == 409
    initial = initial_payload(response.text)
    assert initial["draft"]["revision"] == stale_revision
    assert initial["draft"]["draft"]["rules"][0]["path"] == str(tmp_path / "stale-draft")
    assert initial["conflict"] is True
    assert "Current saved permissions" in response.text
    assert config_path.read_bytes() == after


def test_permissions_save_rejects_catalog_drift_with_retained_draft(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload["catalog_id"] = "0" * 64
    payload["draft_version"] = 3
    payload["draft"]["rules"][0]["path"] = str(tmp_path / "catalog-stale")

    response = client.post(
        "/newsletter/agents/advisor/permissions",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 409
    initial = initial_payload(response.text)
    assert initial["draft"]["catalog_id"] == "0" * 64
    assert initial["draft"]["draft"]["rules"][0]["path"] == str(tmp_path / "catalog-stale")
    assert initial["conflict"] is True


def test_permissions_save_validation_error_marks_summary_as_noncurrent(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload["draft_version"] = 5
    payload["draft"]["rules"][0]["path"] = ""

    response = client.post(
        "/newsletter/agents/advisor/permissions",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    initial = initial_payload(response.text)
    assert initial["draft"]["draft"]["rules"][0]["path"] == ""
    assert "Path rules must include a nonblank path." in response.text
    assert "Current saved permissions" in response.text


def test_permissions_preview_rejects_missing_revision_and_catalog(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload.pop("revision")
    payload.pop("catalog_id")

    response = client.post(
        "/newsletter/agents/advisor/permissions/preview",
        json=payload,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid-request"
    assert body["summary_html"] is None
    assert {issue["field"] for issue in body["issues"]} >= {"revision", "catalog_id"}


def test_permissions_save_rejects_malformed_transport_without_writing(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/permissions",
        data={"payload": "{"},
    )

    assert response.status_code == 422
    assert "Payload must be valid JSON." in response.text
    assert config_path.read_bytes() == before


def test_permissions_page_keeps_unsupported_saved_policy_editable(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["integration"] = "script"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/permissions")

    assert response.status_code == 200
    assert "Preview unavailable" in response.text
    assert "unsupported-permission-mode" in response.text
    initial = initial_payload(response.text)
    assert initial["draft"]["draft"]["mode"] == "inherit"
    assert initial["baseline"]["draft"]["rules"][0]["path"].replace("\\", "/").endswith("Research/additional")


def test_permissions_page_renders_no_path_scope_as_no_path_tools(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["permissions"] = {
        "rules": [
            {"tools": []},
        ],
    }
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/permissions")

    assert response.status_code == 200
    assert "No-path tools" in response.text
    assert "All paths" not in response.text
    assert "No tools" in response.text
    assert "Configured policy" in response.text
    assert "Actual enforcement depends on the integration." in response.text


def test_permissions_initial_payload_escapes_custom_values(monkeypatch, tmp_path, raw_config):
    _pin_catalog(monkeypatch)
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["permissions"] = {
        "mode": "restricted",
        "rules": [
            {
                "path": '<img src=x onerror=alert(1)>',
                "tools": ["read", '<script>alert("x")</script>'],
            }
        ],
    }
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/permissions")

    assert response.status_code == 200
    assert '<script>alert("x")</script>' not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    initial = initial_payload(response.text)
    assert initial["draft"]["draft"]["rules"][0]["path"] == '<img src=x onerror=alert(1)>'
    assert '<script>alert("x")</script>' in initial["draft"]["draft"]["rules"][0]["selected"]


def test_permissions_preview_returns_preview_unavailable_when_catalog_is_unavailable(monkeypatch, tmp_path, raw_config):
    _pin_catalog(
        monkeypatch,
        ToolCatalog(
            integration="copilot",
            version="unavailable",
            complete=False,
            warning="Tool availability could not be determined.",
            tools=(),
        ),
    )
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = initial_payload(client.get("/newsletter/agents/advisor/permissions").text)["draft"]
    payload["draft_version"] = 9

    response = client.post(
        "/newsletter/agents/advisor/permissions/preview",
        json=payload,
    )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "preview-unavailable"
    assert body["summary_html"] is None