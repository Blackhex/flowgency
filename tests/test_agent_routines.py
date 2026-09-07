from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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


def summary_row(html: str, key: str) -> str:
    match = re.search(
        rf'<section[^>]*data-summary-row[^>]*data-key="{re.escape(key)}"[\s\S]*?</section>',
        html,
    )
    assert match is not None
    return match.group(0)


def write_config(config_path, raw: dict) -> None:
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()


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


def test_routines_initial_payload_exposes_non_channel_inherited_memory_label(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    initial = initial_payload(response.text)
    assert initial["inherited_memory_label"] == "Agent memory (Agent default)"


def test_routines_initial_payload_uses_run_memory_when_agent_default_is_omitted(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0].pop("default_memory", None)
    write_config(config_path, raw)

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    initial = initial_payload(response.text)
    assert initial["inherited_memory_label"] == "Run memory"


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


def test_routines_summary_marks_new_rows_not_saved_without_unavailable_saved_values(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"].append(
        {
            "key": "new-1",
            "source_index": None,
            "id": "daily-review",
            "prompt_scope": "instance",
            "prompt_name": "local-triage",
            "enabled": True,
            "arguments": ["--fresh"],
            "schedule": {"mode": "every", "amount": "6", "unit": "h", "time": ""},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "inherit", "channel": ""},
        }
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    unsaved_row = summary_row(response.text, "new-1")
    assert "Not saved" in unsaved_row
    assert "Saved value unavailable" not in unsaved_row


def test_routines_end_to_end_preview_save_and_reload_preserves_edited_order(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "release-review",
            "prompt": {"scope": "instance", "name": "local-triage"},
            "enabled": False,
            "arguments": ["--queued"],
            "schedule": {"every": "6h"},
            "memory": {"scope": "channel", "channel": "support"},
        }
    )
    write_config(config_path, raw)

    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial, draft_version=9)
    first = deepcopy(payload["draft"]["routines"][0])
    second = deepcopy(payload["draft"]["routines"][1])
    first["id"] = "daily-review-renamed"
    first["arguments"] = ["--edited"]
    second["arguments"] = ["--queued", "--rerun"]
    payload["draft"]["routines"] = [
        second,
        first,
        {
            "key": "new-2",
            "source_index": None,
            "id": "fresh-review",
            "prompt_scope": "blueprint",
            "prompt_name": "pr-review",
            "enabled": True,
            "arguments": ["--fresh"],
            "schedule": {"mode": "at", "amount": "", "unit": "d", "time": "17:30"},
            "recovery": {"mode": "default", "amount": "", "unit": "h"},
            "memory": {"scope": "inherit", "channel": ""},
        },
    ]

    preview = client.post(
        "/newsletter/agents/advisor/routines/preview",
        json=payload,
    )

    assert preview.status_code == 200
    assert [row["id"] for row in preview.json()["rows"]] == [
        "release-review",
        "daily-review-renamed",
        "fresh-review",
    ]

    saved = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == "/newsletter/agents/advisor/routines"
    reloaded = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    assert [row["id"] for row in reloaded["draft"]["draft"]["routines"]] == [
        "release-review",
        "daily-review-renamed",
        "fresh-review",
    ]
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert [
        row["id"] for row in persisted["teams"]["newsletter"]["agents"][0]["routines"]
    ] == ["release-review", "daily-review-renamed", "fresh-review"]
    assert persisted["teams"]["newsletter"]["agents"][0]["routines"][1]["arguments"] == ["--edited"]


def test_routines_save_conflict_retains_stale_draft_and_clears_saved_provenance(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "release-review",
            "prompt": {"scope": "instance", "name": "local-triage"},
            "arguments": ["--queued"],
            "schedule": {"every": "6h"},
        }
    )
    write_config(config_path, raw)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial, draft_version=5)
    payload["draft"]["routines"] = [
        {
            **deepcopy(payload["draft"]["routines"][1]),
            "id": "release-review-renamed",
            "arguments": ["--queued", "--local"],
        },
        {
            **deepcopy(payload["draft"]["routines"][0]),
            "id": "daily-review-renamed",
            "arguments": ["--edited"],
        },
    ]
    stale_revision = payload["revision"]

    newer = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    newer["teams"]["newsletter"]["agents"][0]["routines"] = [
        {
            "id": "server-live",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "arguments": ["--server"],
            "schedule": {"every": "12h"},
        },
        {
            "id": "daily-review",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "arguments": ["--brief"],
            "schedule": {"at": "09:00"},
        },
    ]
    write_config(config_path, newer)
    newer_bytes = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 409
    initial = initial_payload(response.text)
    assert initial["conflict"] is True
    assert initial["draft"]["revision"] == stale_revision
    assert [row["id"] for row in initial["draft"]["draft"]["routines"]] == [
        "release-review-renamed",
        "daily-review-renamed",
    ]
    assert [row["arguments"] for row in initial["draft"]["draft"]["routines"]] == [
        ["--queued", "--local"],
        ["--edited"],
    ]
    assert initial["saved_status"] == []
    assert initial["original_ids"] == []
    assert "Saved value unavailable" in summary_row(response.text, "saved-1")
    assert config_path.read_bytes() == newer_bytes


def test_routines_preview_rejects_malformed_transport_and_bad_draft_version(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    malformed = client.post(
        "/newsletter/agents/advisor/routines/preview",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert malformed.status_code == 422
    assert malformed.json()["code"] == "malformed-transport"
    assert malformed.json()["draft_version"] == 0

    payload = draft_payload(initial_payload(client.get("/newsletter/agents/advisor/routines").text))
    payload["draft_version"] = "bad"
    typed = client.post(
        "/newsletter/agents/advisor/routines/preview",
        json=payload,
    )

    assert typed.status_code == 422
    assert typed.json()["code"] == "invalid-request"
    assert any(issue["field"] == "draft_version" for issue in typed.json()["issues"])


def test_routines_save_rejects_missing_and_malformed_payload_without_writing(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()

    missing = client.post("/newsletter/agents/advisor/routines", data={})
    malformed = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": "{"},
    )

    assert missing.status_code == 422
    assert malformed.status_code == 422
    assert "Reload the Routines editor before saving." in missing.text
    assert "Payload must be valid JSON." in malformed.text
    assert config_path.read_bytes() == before


def test_routines_save_rejects_duplicate_keys_and_source_indices(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = draft_payload(initial_payload(client.get("/newsletter/agents/advisor/routines").text))
    duplicate = deepcopy(payload["draft"]["routines"][0])
    duplicate["id"] = "daily-review-copy"
    payload["draft"]["routines"].append(duplicate)

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    assert "Row key must be unique." in response.text
    assert "Source index must be unique." in response.text
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][1]["key"] == "saved-0"
    assert retained["draft"]["draft"]["routines"][1]["source_index"] == 0


def test_routines_page_escapes_submitted_xss_values_and_drops_extra_fields(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["id"] = '<img src=x onerror=alert(1)>'
    payload["draft"]["routines"][0]["arguments"] = ['<script>alert("x")</script>']

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    assert '<script>alert("x")</script>' not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["id"] == '<img src=x onerror=alert(1)>'
    assert retained["draft"]["draft"]["routines"][0]["arguments"] == ['<script>alert("x")</script>']

    extra_payload = draft_payload(initial)
    extra_payload["draft"]["routines"][0]["extra"] = '<script>alert("extra")</script>'

    extra_response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(extra_payload)},
    )

    assert extra_response.status_code == 422
    assert '<script>alert("extra")</script>' not in extra_response.text


def test_routines_save_revalidates_missing_prompt_after_load(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["prompt"] = {
        "scope": "instance",
        "name": "local-triage",
    }
    write_config(config_path, raw)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    before = config_path.read_bytes()
    prompt_path = tmp_path / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md"
    prompt_path.rename(prompt_path.with_name("local-triage-renamed.prompt.md"))

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["prompt_name"] == "local-triage"
    assert any(issue["code"] == "missing-prompt" for issue in retained["issues"])
    assert config_path.read_bytes() == before


def test_routines_save_revalidates_deleted_channel_after_load(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["memory"] = {"scope": "channel", "channel": "support"}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["memory"]["channels"] = {}
    write_config(config_path, raw)
    payload["revision"] = app_mod.app.state.services.config_store.load().revision
    current_bytes = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["memory"] == {
        "scope": "channel",
        "channel": "support",
    }
    assert any(issue["code"] == "missing-memory-channel" for issue in retained["issues"])
    assert config_path.read_bytes() == current_bytes


def test_routines_save_retains_disabled_invalid_rows_for_fixup(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    payload = draft_payload(initial_payload(client.get("/newsletter/agents/advisor/routines").text))
    payload["draft"]["routines"][0]["enabled"] = False
    payload["draft"]["routines"][0]["id"] = ""

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["enabled"] is False
    assert retained["draft"]["draft"]["routines"][0]["id"] == ""
    assert retained["summary_rows"][0]["id"] == ""
    assert "(blank id)" in response.text


def test_routines_save_preserves_unsupported_loaded_time_when_edit_is_unrelated(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["schedule"] = {"at": "9am"}
    write_config(config_path, raw)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["enabled"] = False

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["teams"]["newsletter"]["agents"][0]["routines"][0]["schedule"] == {"at": "9am"}
    assert saved["teams"]["newsletter"]["agents"][0]["routines"][0]["enabled"] is False


def test_routines_save_unavailable_services_returns_recoverable_503_and_retains_draft(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["id"] = "daily-review-retained"
    before = config_path.read_bytes()
    app_mod.app.state.services = replace(
        app_mod.app.state.services,
        blueprint_library=None,
        prompt_store=None,
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 503
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["id"] == "daily-review-retained"
    assert any(issue["code"] == "routines-unavailable" for issue in retained["issues"])
    assert "AttributeError" not in response.text
    assert config_path.read_bytes() == before


def test_routines_save_uses_corrected_daily_summary_when_unsupported_saved_values_were_edited(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["schedule"] = {
        "at": "9am",
        "catch_up": "always",
    }
    write_config(config_path, raw)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["schedule"] = {
        "mode": "at",
        "amount": "",
        "unit": "d",
        "time": "09:15",
    }
    payload["draft"]["routines"][0]["recovery"] = {
        "mode": "duration",
        "amount": "4",
        "unit": "h",
    }
    payload["draft"]["routines"][0]["id"] = ""
    before = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["schedule"]["time"] == "09:15"
    assert retained["draft"]["draft"]["routines"][0]["recovery"] == {
        "mode": "duration",
        "amount": "4",
        "unit": "h",
    }
    assert retained["summary_rows"][0]["schedule"] == "at 09:15"
    assert retained["summary_rows"][0]["recovery"] == "4h"
    assert any(issue["field"] == "routines.0.id" for issue in retained["issues"])
    assert config_path.read_bytes() == before


def test_routines_save_uses_corrected_interval_summary_when_unsupported_saved_values_were_edited(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["schedule"] = {
        "every": "3600",
        "catch_up": "always",
    }
    write_config(config_path, raw)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["schedule"] = {
        "mode": "every",
        "amount": "6",
        "unit": "h",
        "time": "",
    }
    payload["draft"]["routines"][0]["recovery"] = {
        "mode": "none",
        "amount": "",
        "unit": "h",
    }
    payload["draft"]["routines"][0]["prompt_name"] = "unknown"
    before = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    retained = initial_payload(response.text)
    assert retained["draft"]["draft"]["routines"][0]["schedule"]["amount"] == "6"
    assert retained["draft"]["draft"]["routines"][0]["recovery"]["mode"] == "none"
    assert retained["summary_rows"][0]["schedule"] == "every 6h"
    assert retained["summary_rows"][0]["recovery"] == "none"
    assert any(issue["field"] == "routines.0.prompt" for issue in retained["issues"])
    assert config_path.read_bytes() == before


def test_routines_save_keeps_unsupported_saved_summary_when_schedule_controls_are_unchanged(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["schedule"] = {
        "every": "3600",
        "catch_up": "always",
    }
    write_config(config_path, raw)
    initial = initial_payload(client.get("/newsletter/agents/advisor/routines").text)
    payload = draft_payload(initial)
    payload["draft"]["routines"][0]["prompt_name"] = "unknown"
    before = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 422
    retained = initial_payload(response.text)
    assert retained["summary_rows"][0]["schedule"] == "3600 (Unsupported saved interval: 3600)"
    assert retained["summary_rows"][0]["recovery"] == "always"
    assert any(issue["field"] == "routines.0.prompt" for issue in retained["issues"])
    assert config_path.read_bytes() == before