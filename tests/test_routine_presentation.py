from __future__ import annotations

import os
from datetime import datetime

import yaml

from flowgency import app as app_mod
from flowgency.configuration import ConfigStore
from flowgency.dispatch.schedule import at_marker_path, every_marker_path
from flowgency.routines.editor import RoutinesRequest, load_choices, prepare_routines
from flowgency.routines.forms import MemoryDraft, RoutineDraft, build_form
from flowgency.routines.presentation import saved_status, summarize
from tests.test_agent_detail import _seed_app


def _request(snapshot, *, draft_version: int = 1) -> RoutinesRequest:
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    return RoutinesRequest(
        revision=snapshot.revision,
        draft_version=draft_version,
        draft=build_form(agent).draft,
    )


def test_saved_status_preserves_original_indices_ids_and_timestamps(monkeypatch, tmp_path, raw_config):
    fixed_now = datetime(2026, 9, 7, 10, 0)
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", fixed_now.isoformat())
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "hourly-check",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "enabled": False,
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    snapshot = ConfigStore(config_path).load()
    logs_root = tmp_path / "groups" / "newsletter" / "logs"
    first = at_marker_path(logs_root, "advisor", "daily-review", fixed_now.strftime("%Y-%m-%d"))
    second = every_marker_path(logs_root, "advisor", "hourly-check")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    stamp = fixed_now.timestamp()
    os.utime(first, (stamp, stamp))
    os.utime(second, (stamp, stamp))

    rows = saved_status(snapshot, "newsletter", "advisor")

    assert rows[0].source_index == 0
    assert rows[0].original_id == "daily-review"
    assert rows[0].last_fired == "2026-09-07 10:00"
    assert rows[1].source_index == 1
    assert rows[1].original_id == "hourly-check"
    assert rows[1].next_due == "—"


def test_summarize_keeps_draft_identity_and_original_source_index_on_reorder_and_rename(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "release-review",
            "prompt": {"scope": "instance", "name": "local-triage"},
            "schedule": {"every": "6h"},
            "memory": {"scope": "channel", "channel": "support"},
        }
    )
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    services = app_mod.app.state.services
    snapshot = ConfigStore(config_path).load()
    request = _request(snapshot)
    request.draft.routines.reverse()
    request.draft.routines[1].id = "daily-review-renamed"
    request.draft.routines.append(
        RoutineDraft(
            key="new-1",
            id="fresh-review",
            prompt_scope="blueprint",
            prompt_name="pr-review",
            enabled=True,
            arguments=["--fresh"],
            schedule=request.draft.routines[0].schedule.model_copy(deep=True),
            recovery=request.draft.routines[0].recovery.model_copy(deep=True),
            memory=MemoryDraft(scope="inherit"),
        )
    )
    choices = load_choices(
        snapshot,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )
    prepared = prepare_routines(snapshot, "newsletter", "advisor", request, choices)

    rows = summarize(prepared, "newsletter", "advisor", request.draft)

    assert [row.key for row in rows] == ["saved-1", "saved-0", "new-1"]
    assert [row.source_index for row in rows] == [1, 0, None]
    assert [row.id for row in rows] == ["release-review", "daily-review-renamed", "fresh-review"]
    assert rows[0].memory == "Channel: Support"


def test_summarize_uses_run_memory_fallback_when_agent_default_is_omitted(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0].pop("default_memory", None)
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    services = app_mod.app.state.services
    snapshot = ConfigStore(config_path).load()
    request = _request(snapshot)
    request.draft.routines[0].memory = MemoryDraft(scope="inherit")
    choices = load_choices(
        snapshot,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )
    prepared = prepare_routines(snapshot, "newsletter", "advisor", request, choices)

    rows = summarize(prepared, "newsletter", "advisor", request.draft)

    assert rows[0].memory == "Run memory"


def test_summarize_shows_agent_default_provenance_and_unsupported_saved_values(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["default_memory"] = {"scope": "team"}
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["memory"] = None
    raw["teams"]["newsletter"]["agents"][0]["routines"][0]["schedule"] = {
        "at": "9am",
    }
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    services = app_mod.app.state.services
    snapshot = ConfigStore(config_path).load()
    request = _request(snapshot)
    choices = load_choices(
        snapshot,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )
    prepared = prepare_routines(snapshot, "newsletter", "advisor", request, choices)

    row = summarize(prepared, "newsletter", "advisor", request.draft)[0]

    assert row.schedule == "9am (Unsupported saved daily time: 9am)"
    assert row.recovery == "today"
    assert row.memory == "Team memory (Agent default)"