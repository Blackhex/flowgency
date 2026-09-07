from __future__ import annotations

import json
import os
import re
from datetime import datetime

import yaml

from flowgency import app as app_mod
from flowgency.dispatch.schedule import every_marker_path
from tests.test_agent_detail import _seed_app


def initial_payload(html: str) -> dict:
    match = re.search(
        r'<script id="routines-initial" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


def draft_payload(initial: dict, *, draft_version: int = 1) -> dict:
    payload = json.loads(json.dumps(initial["draft"]))
    payload["draft_version"] = draft_version
    return payload


def test_routines_uses_structured_form(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert 'name="routines_json"' not in response.text
    assert '<textarea' not in response.text
    initial = initial_payload(response.text)
    assert initial["draft"]["draft"]["routines"][0]["id"] == "daily-review"
    assert initial["saved_status"][0]["original_id"] == "daily-review"
    assert 'data-routine-list' in response.text


def test_old_raw_form_cannot_clear_the_list(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"routines_json": "[]"},
    )

    assert response.status_code == 422
    assert config_path.read_bytes() == before


def test_routines_post_replaces_ordered_list(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"] = [
        {
            "key": "saved-0",
            "source_index": 0,
            "id": "triage",
            "prompt_scope": "blueprint",
            "prompt_name": "pr-review",
            "enabled": False,
            "arguments": ["--triage"],
            "schedule": {"mode": "every", "amount": "6", "unit": "h", "time": ""},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "routine", "channel": ""},
        },
        {
            "key": "new-1",
            "id": "digest",
            "prompt_scope": "instance",
            "prompt_name": "local-triage",
            "enabled": True,
            "arguments": ["--digest"],
            "schedule": {"mode": "at", "amount": "", "unit": "d", "time": "17:30"},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "inherit", "channel": ""},
        },
    ]

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    routines = saved["teams"]["newsletter"]["agents"][0]["routines"]
    assert [routine["id"] for routine in routines] == ["triage", "digest"]
    assert routines[0]["enabled"] is False
    assert routines[1].get("enabled", True) is True
    assert routines[0]["memory"] == {"scope": "routine"}
    assert routines[1]["schedule"] == {"at": "17:30"}


def test_routines_post_keeps_the_recovery_bound(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["recovery"] = {"mode": "duration", "amount": "48", "unit": "h"}

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    routines = saved["teams"]["newsletter"]["agents"][0]["routines"]
    assert routines[0]["schedule"] == {"at": "09:00", "catch_up": "48h"}

    reloaded = client.get("/newsletter/agents/advisor/routines")
    assert "48h" in reloaded.text


def test_routines_post_rejects_an_unusable_recovery_bound(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["recovery"] = {"mode": "duration", "amount": "sometimes", "unit": "h"}

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    assert "Recovery duration amount must use digits only" in response.text


def test_routines_get_preserves_disabled_state(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["enabled"] = False
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    initial = initial_payload(response.text)
    assert initial["draft"]["draft"]["routines"][0]["enabled"] is False
    assert "Disabled" in response.text


def test_routines_post_rejects_duplicate_ids(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"] = [
        {
            "key": "saved-0",
            "source_index": 0,
            "id": "dup",
            "prompt_scope": "blueprint",
            "prompt_name": "pr-review",
            "enabled": True,
            "arguments": [],
            "schedule": {"mode": "at", "amount": "", "unit": "d", "time": "09:00"},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "routine", "channel": ""},
        },
        {
            "key": "new-1",
            "id": "dup",
            "prompt_scope": "instance",
            "prompt_name": "local-triage",
            "enabled": True,
            "arguments": [],
            "schedule": {"mode": "every", "amount": "6", "unit": "h", "time": ""},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "inherit", "channel": ""},
        },
    ]

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    assert "Duplicate routine id" in response.text


def test_routine_editor_accepts_explicit_blueprint_and_instance_prompts(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"] = [
        {
            "key": "saved-0",
            "source_index": 0,
            "id": "morning-review",
            "prompt_scope": "blueprint",
            "prompt_name": "pr-review",
            "enabled": True,
            "arguments": [],
            "schedule": {"mode": "at", "amount": "", "unit": "d", "time": "09:00"},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "routine", "channel": ""},
        },
        {
            "key": "new-1",
            "id": "local-review",
            "prompt_scope": "instance",
            "prompt_name": "local-triage",
            "enabled": False,
            "arguments": ["--focused"],
            "schedule": {"mode": "every", "amount": "6", "unit": "h", "time": ""},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "channel", "channel": "support"},
        },
    ]

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_routine_editor_rejects_unknown_scope_name_and_shorthand(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    invalid_scope = {
        "revision": initial_payload(client.get("/newsletter/agents/advisor/routines").text)["draft"]["revision"],
        "draft_version": 1,
        "draft": {
            "routines": [
                {
                    "key": "saved-0",
                    "source_index": 0,
                    "id": "bad-scope",
                    "prompt_scope": "team",
                    "prompt_name": "pr-review",
                    "enabled": True,
                    "arguments": [],
                    "schedule": {"mode": "at", "amount": "", "unit": "d", "time": "09:00"},
                    "recovery": {"mode": "default", "amount": "", "unit": "h"},
                    "memory": {"scope": "routine", "channel": ""},
                }
            ]
        },
    }
    invalid_name = draft_payload(initial_payload(client.get("/newsletter/agents/advisor/routines").text))
    invalid_name["draft"]["routines"][0]["id"] = "bad-name"
    invalid_name["draft"]["routines"][0]["prompt_name"] = "unknown"
    shorthand = {
        "revision": invalid_name["revision"],
        "draft_version": 1,
        "draft": {
            "routines": [
                {
                    "key": "saved-0",
                    "source_index": 0,
                    "id": "string-prompt",
                    "prompt": "pr-review",
                    "enabled": True,
                    "arguments": [],
                    "schedule": {"mode": "every", "amount": "2", "unit": "h", "time": ""},
                    "recovery": {"mode": "default", "amount": "", "unit": "h"},
                    "memory": {"scope": "routine", "channel": ""},
                }
            ]
        },
    }

    invalid_scope_response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(invalid_scope)},
    )
    invalid_name_response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(invalid_name)},
    )
    shorthand_response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(shorthand)},
    )

    assert invalid_scope_response.status_code == 422
    assert "Choose a supported value" in invalid_scope_response.text
    assert invalid_name_response.status_code == 422
    assert "Routine prompt must be selected from the effective prompt catalog" in invalid_name_response.text
    assert shorthand_response.status_code == 422
    assert "Unexpected field" in shorthand_response.text


def test_routines_get_lists_saved_status_in_summary(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "Last fired" in response.text
    assert "Next due" in response.text
    assert "at 09:00" in response.text
    assert "never" in response.text


def test_routines_get_disabled_routine_shows_dash_for_next_due(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "weekly-digest",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "enabled": False,
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "weekly-digest" in response.text
    assert "—" in response.text


def test_routines_get_fired_routine_shows_timestamp_and_next_occurrence(monkeypatch, tmp_path, raw_config):
    fixed_now = datetime(2026, 9, 6, 10, 0)
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", fixed_now.isoformat())
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "hourly-check",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    logs_root = tmp_path / "groups" / "newsletter" / "logs"
    marker = every_marker_path(logs_root, "advisor", "hourly-check")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    stamp = fixed_now.timestamp()
    os.utime(marker, (stamp, stamp))

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "hourly-check" in response.text
    assert "due in 6h" in response.text
    assert fixed_now.strftime("%Y-%m-%d") in response.text


def test_routines_dispatch_disabled_shows_dispatch_disabled(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": False}
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "dispatch disabled" in response.text
    assert "overdue" not in response.text


def test_routines_preview_reorders_and_renames_with_structured_rows(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial, draft_version=7)
    payload["draft"]["routines"][0]["id"] = "daily-review-renamed"

    response = client.post(
        "/newsletter/agents/advisor/routines/preview",
        json=payload,
    )

    assert response.status_code == 200
    preview = response.json()
    assert preview["draft_version"] == 7
    assert preview["rows"][0]["id"] == "daily-review-renamed"
    assert preview["warnings"] == []