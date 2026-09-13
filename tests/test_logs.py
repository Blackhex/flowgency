import asyncio
import json
import os
import threading
from html import escape
from types import SimpleNamespace
import yaml
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import flowgency.app as app_mod
from flowgency.app import build_agent_timeline, collect_logs, get_agent_logs
from flowgency.web.logs import collect_agent_logs
from tests.test_agent_detail import _seed_activity_app
from tests.test_local_ticket_storage import _make_reparse


def _job_log_record(team_id: str, agent_name: str, *, stdout_path: str | None = None, stderr_path: str | None = None):
    return SimpleNamespace(
        spec=SimpleNamespace(team_key=team_id, agent_name=agent_name),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def test_collect_logs_omits_empty_error_files(tmp_path):
    logs_dir = tmp_path / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-run.out").write_text("completed")
    (logs_dir / "agent-run.err").write_text("")

    logs = collect_logs({"logs": tmp_path / "logs"})

    assert [entry["name"] for entry in logs["2026-07-12"]] == ["agent-run.out"]


def test_collect_logs_orders_by_mtime_and_prefers_out_for_ties(tmp_path):
    logs_dir = tmp_path / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)

    older = logs_dir / "agent-z-older.out"
    newer_out = logs_dir / "agent-a-newer.out"
    newer_err = logs_dir / "agent-a-newer.err"

    older.write_text("older")
    newer_out.write_text("newer out")
    newer_err.write_text("newer err")

    older_mtime = datetime(2026, 7, 12, 19, 45).timestamp()
    newer_mtime = datetime(2026, 7, 12, 20, 6).timestamp()
    os.utime(older, (older_mtime, older_mtime))
    os.utime(newer_out, (newer_mtime, newer_mtime))
    os.utime(newer_err, (newer_mtime, newer_mtime))

    logs = collect_logs({"logs": tmp_path / "logs"})
    entries = logs["2026-07-12"]

    assert [entry["name"] for entry in entries] == [
        "agent-a-newer.out",
        "agent-a-newer.err",
        "agent-z-older.out",
    ]
    assert [entry["timestamp"] for entry in entries] == [
        datetime.fromtimestamp(newer_out.stat().st_mtime),
        datetime.fromtimestamp(newer_err.stat().st_mtime),
        datetime.fromtimestamp(older.stat().st_mtime),
    ]


def test_collect_logs_skips_hidden_non_files_and_empty_error_only_groups(tmp_path):
    logs_dir = tmp_path / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (logs_dir / ".hidden.out").write_text("hidden", encoding="utf-8")
    (logs_dir / "agent-run.err").write_text("", encoding="utf-8")
    (logs_dir / "not-a-log.out").mkdir()

    logs = collect_logs({"logs": tmp_path / "logs"})

    assert logs == {}


def test_collect_logs_rejects_reparse_date_directory(tmp_path):
    logs_root = tmp_path / "logs"
    logs_root.mkdir()
    target = tmp_path / "outside"
    day = target / "2026-07-12"
    day.mkdir(parents=True)
    (day / "agent-run.out").write_text("outside", encoding="utf-8")
    _make_reparse(logs_root / "2026-07-12", day)

    logs = collect_logs({"logs": logs_root})

    assert logs == {}


def test_collect_agent_logs_keeps_more_than_eight_files(tmp_path):
    day = tmp_path / "logs" / "2026-07-12"
    day.mkdir(parents=True)
    for index in range(9):
        (day / f"advisor-run-{index}.out").write_text(f"log {index}", encoding="utf-8")

    logs = collect_agent_logs(tmp_path / "logs", "test", "advisor", (), ("advisor",))

    assert sum(len(entries) for entries in logs.values()) == 9


def test_collect_agent_logs_uses_exact_record_owner_before_filename_fallback(tmp_path):
    day = tmp_path / "logs" / "2026-07-12"
    day.mkdir(parents=True)
    path = day / "advisor-run.out"
    path.write_text("owned elsewhere", encoding="utf-8")
    record = _job_log_record("test", "advisor-extra", stdout_path=str(path.resolve()))

    logs = collect_agent_logs(
        tmp_path / "logs",
        "test",
        "advisor",
        (record,),
        ("advisor", "advisor-extra"),
    )

    assert logs == {}


def test_collect_agent_logs_rejects_overlapping_fallback_names(tmp_path):
    day = tmp_path / "logs" / "2026-07-12"
    day.mkdir(parents=True)
    path = day / "advisor-extra-run.out"
    path.write_text("overlap", encoding="utf-8")

    advisor_logs = collect_agent_logs(
        tmp_path / "logs",
        "test",
        "advisor",
        (),
        ("advisor", "advisor-extra"),
    )
    advisor_extra_logs = collect_agent_logs(
        tmp_path / "logs",
        "test",
        "advisor-extra",
        (),
        ("advisor", "advisor-extra"),
    )

    assert advisor_logs == {}
    assert [entry["name"] for entry in advisor_extra_logs["2026-07-12"]] == [path.name]


def test_collect_agent_logs_skips_missing_and_outside_recorded_paths(tmp_path):
    day = tmp_path / "logs" / "2026-07-12"
    day.mkdir(parents=True)
    outside = tmp_path / "elsewhere.out"
    outside.write_text("outside", encoding="utf-8")
    record = _job_log_record(
        "test",
        "advisor",
        stdout_path=str(outside.resolve()),
        stderr_path=str((day / "missing.err").resolve()),
    )

    logs = collect_agent_logs(tmp_path / "logs", "test", "advisor", (record,), ("advisor",))

    assert logs == {}


def test_agent_log_views_omit_empty_error_files(tmp_path):
    logs_dir = tmp_path / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-run.out").write_text("completed")
    (logs_dir / "agent-run.err").write_text("")
    team_dict = {"logs": tmp_path / "logs"}

    recent = get_agent_logs(team_dict, "agent")
    timeline = build_agent_timeline(team_dict, "agent", agent_observations=[])

    assert [entry["name"] for entry in recent] == ["agent-run.out"]
    assert [event["name"] for event in timeline] == ["agent-run.out"]


def test_logs_page_displays_local_modification_time(tmp_path, monkeypatch):
    workspace_path = tmp_path / "workspace" / "test"
    team_root = tmp_path / "groups" / "test"
    workspace_path.mkdir(parents=True)
    logs_dir = team_root / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (team_root / "observations").mkdir(parents=True)
    (team_root / "proposals").mkdir(parents=True)
    (team_root / "decisions").mkdir(parents=True)
    (team_root / "locks").mkdir(parents=True)

    log_file = logs_dir / "agent-run.out"
    log_file.write_text("completed", encoding="utf-8")
    mtime = datetime(2026, 7, 12, 20, 6).timestamp()
    os.utime(log_file, (mtime, mtime))

    blueprint_root = tmp_path / "agent-library" / "agent-blueprint"
    blueprint_root.mkdir(parents=True, exist_ok=True)
    (blueprint_root / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,

                "flowgency": {
                    "title": "Flowgency",
                    "default_team": "test",
                    "ai_backend": "claude-code",
                    "agent_library": str((tmp_path / "agent-library").resolve()),
                    "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
                    "memory_store": str((tmp_path / "memory").resolve()),
                    "prompt_store": str((tmp_path / "prompts").resolve()),
                },
                "teams": {
                    "test": {
                        "name": "Test Group",
                        "workspace_path": str(workspace_path.resolve()),
                        "path": str(team_root.resolve()),
                        "default_integration": "script",
                        "agents": [
                            {
                                "name": "agent",
                                "blueprint": "agent-blueprint",
                                "integration": "script",
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()

    client = TestClient(app_mod.app)
    response = client.get("/test/logs")

    assert response.status_code == 200
    time_pos = response.text.index("20:06")
    badge_pos = response.text.index(">OUT<", time_pos)
    assert time_pos < badge_pos


def test_log_view_rejects_workspace_file_outside_team_logs(tmp_path, monkeypatch):
    workspace_path = tmp_path / "workspace" / "test"
    team_root = tmp_path / "groups" / "test"
    workspace_path.mkdir(parents=True)
    (team_root / "logs" / "2026-07-12").mkdir(parents=True)
    for name in ("observations", "proposals", "decisions", "locks"):
        (team_root / name).mkdir(parents=True)
    log_file = team_root / "logs" / "2026-07-12" / "agent-run.out"
    log_file.write_text("completed", encoding="utf-8")
    workspace_file = workspace_path / "notes.out"
    workspace_file.write_text("workspace data", encoding="utf-8")

    blueprint_root = tmp_path / "agent-library" / "agent-blueprint"
    blueprint_root.mkdir(parents=True, exist_ok=True)
    (blueprint_root / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flowgency": {
                    "title": "Flowgency",
                    "default_team": "test",
                    "ai_backend": "claude-code",
                    "agent_library": str((tmp_path / "agent-library").resolve()),
                    "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
                    "memory_store": str((tmp_path / "memory").resolve()),
                    "prompt_store": str((tmp_path / "prompts").resolve()),
                },
                "teams": {
                    "test": {
                        "name": "Test Group",
                        "workspace_path": str(workspace_path.resolve()),
                        "path": str(team_root.resolve()),
                        "default_integration": "script",
                        "agents": [
                            {
                                "name": "agent",
                                "blueprint": "agent-blueprint",
                                "integration": "script",
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()

    client = TestClient(app_mod.app)
    allowed = client.get(f"/test/logs/view?path={log_file}")
    denied = client.get(f"/test/logs/view?path={workspace_file}")

    assert allowed.status_code == 200
    assert denied.status_code == 403


@pytest.fixture
def preview_team(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    group = {"key": "test", "name": "Test", "logs": logs}
    monkeypatch.setattr(app_mod, "get_team", lambda team: group)
    monkeypatch.setattr(app_mod, "team_context", lambda group: {
        "team": "test", "team_name": "Test", "teams": {"test": "Test"},
        "flowgency_title": "Flowgency", "workspaces": [], "workspaces_available": False,
        "nav_open_observations": 0, "nav_actionable": 0, "nav_actionable_proposals": 0,
        "nav_agent_count": 0, "nav_running_decisions": 0, "show_tips": False,
        "tips_dismissed": [], "theme_css": "",
    })
    return logs


def test_log_route_recovers_historical_json(preview_team):
    path = preview_team / "historic.out"
    path.write_text(json.dumps({"type": "assistant.message", "data": {"content": "## Readable\n\n**Restored**"}}), encoding="utf-8")
    before = path.read_bytes()
    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(path)})
    assert response.status_code == 200
    assert "<h2>Readable</h2>" in response.text
    assert "<strong>Restored</strong>" in response.text
    assert '"type": "assistant.message"' not in response.text
    assert path.read_bytes() == before


@pytest.mark.parametrize("suffix", [".out", ".err"])
def test_log_route_does_not_inject_markup(preview_team, suffix):
    path = preview_team / f"malicious{suffix}"
    path.write_text('<img src="/log-resource" onerror="window.logExecuted=1">', encoding="utf-8")
    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(path)})
    assert response.status_code == 200
    assert '<img src="/log-resource"' not in response.text


def test_log_route_missing_file_is_404(preview_team):
    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(preview_team / "missing.out")})
    assert response.status_code == 404


def test_log_route_forbids_non_log_file_inside_root_before_preview(preview_team, monkeypatch):
    path = preview_team / "secrets.md"
    path.write_text("top secret", encoding="utf-8")
    before = path.read_bytes()

    def fail_preview(_path: Path):
        raise AssertionError("read_log_preview must not run for unsupported files")

    monkeypatch.setattr(app_mod, "read_log_preview", fail_preview)

    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(path)})

    assert response.status_code == 403
    assert path.read_bytes() == before


def test_log_route_forbids_hidden_and_reparse_ancestor_paths_before_preview(preview_team, monkeypatch, tmp_path):
    hidden_dir = preview_team / ".hidden"
    hidden_dir.mkdir()
    hidden_path = hidden_dir / "agent-run.out"
    hidden_path.write_text("hidden", encoding="utf-8")

    real_dir = preview_team / "real"
    real_dir.mkdir()
    safe_target = real_dir / "agent-run.out"
    safe_target.write_text("linked", encoding="utf-8")
    linked_dir = preview_team / "linked"
    _make_reparse(linked_dir, real_dir)
    linked_path = linked_dir / "agent-run.out"

    def fail_preview(_path: Path):
        raise AssertionError("read_log_preview must not run for unsafe files")

    monkeypatch.setattr(app_mod, "read_log_preview", fail_preview)

    hidden_response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(hidden_path)})
    linked_response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(linked_path)})

    assert hidden_response.status_code == 403
    assert linked_response.status_code == 403


def test_log_view_returns_to_agent_logs_with_valid_context(monkeypatch, tmp_path, raw_config):
    client, _config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)

    response = client.get(
        "/newsletter-prod/logs/view",
        params={"path": str(log_file), "agent": "advisor", "source": "logs"},
    )

    assert response.status_code == 200
    assert "Back to Logs" in response.text
    assert "/newsletter-prod/agents/advisor/logs" in response.text


def test_log_view_returns_to_agent_activity_with_valid_context(monkeypatch, tmp_path, raw_config):
    client, _config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)

    response = client.get(
        "/newsletter-prod/logs/view",
        params={"path": str(log_file), "agent": "advisor", "source": "activity"},
    )

    assert response.status_code == 200
    assert "Back to Activity" in response.text
    assert "/newsletter-prod/agents/advisor/activity" in response.text


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({"agent": "advisor"}, 400),
        ({"source": "logs"}, 400),
        ({"agent": "advisor", "source": "bad"}, 400),
        ({"agent": "missing", "source": "logs"}, 404),
    ],
)
def test_log_view_rejects_invalid_agent_context_without_redirect(monkeypatch, tmp_path, raw_config, params, expected_status):
    client, _config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)

    def fail_preview(_path: Path):
        raise AssertionError("read_log_preview must not run for invalid agent context")

    monkeypatch.setattr(app_mod, "read_log_preview", fail_preview)

    response = client.get(
        "/newsletter-prod/logs/view",
        params={"path": str(log_file), **params},
        follow_redirects=False,
    )

    assert response.status_code == expected_status
    assert "location" not in response.headers


def test_log_view_forbids_agent_context_for_unscoped_file(monkeypatch, tmp_path, raw_config):
    client, config_path, _log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter-prod"]["agents"].append(
        {
            **raw["teams"]["newsletter-prod"]["agents"][0],
            "name": "advisor-extra",
        }
    )
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    other_log = tmp_path / "groups" / "newsletter-workspace" / "logs" / "2026-07-16" / "advisor-extra-run.out"
    other_log.write_text("other agent", encoding="utf-8")
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)

    def fail_preview(_path: Path):
        raise AssertionError("read_log_preview must not run for unscoped agent logs")

    monkeypatch.setattr(app_mod, "read_log_preview", fail_preview)

    response = client.get(
        "/newsletter-prod/logs/view",
        params={"path": str(other_log), "agent": "advisor", "source": "logs"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert "location" not in response.headers


def test_log_view_accepts_top_level_output_with_valid_agent_context(monkeypatch, tmp_path, raw_config):
    client, _config_path, _log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    top_level = tmp_path / "groups" / "newsletter-workspace" / "logs" / "advisor-top-level.out"
    top_level.write_text("top-level", encoding="utf-8")

    response = client.get(
        "/newsletter-prod/logs/view",
        params={"path": str(top_level), "agent": "advisor", "source": "logs"},
    )

    assert response.status_code == 200
    assert "top-level" in response.text


def test_log_view_accepts_special_characters_in_valid_agent_context(monkeypatch, tmp_path, raw_config):
    client, _config_path, _log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    special = tmp_path / "groups" / "newsletter-workspace" / "logs" / "2026-07-16" / "advisor-demo & łog.out"
    special.write_text("special", encoding="utf-8")

    response = client.get(
        "/newsletter-prod/logs/view",
        params={"path": str(special), "agent": "advisor", "source": "logs"},
    )

    assert response.status_code == 200
    assert escape(special.name) in response.text
    assert "Back to Logs" in response.text


def test_log_work_does_not_block_event_loop(preview_team, monkeypatch):
    path = preview_team / "slow.out"
    path.write_text("Readable", encoding="utf-8")
    entered = threading.Event()
    released = threading.Event()
    get_team = app_mod.get_team
    def blocked_team(team):
        entered.set()
        if not released.wait(2):
            raise AssertionError("Log processing blocked the event loop")
        return get_team(team)
    monkeypatch.setattr(app_mod, "get_team", blocked_team)
    async def check():
        transport = httpx.ASGITransport(app=app_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pending = asyncio.create_task(client.get("/test/logs/view", params={"path": str(path)}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await asyncio.wait_for(client.get("/static/manifest.json"), 1)
                assert response.status_code == 200
                assert not pending.done()
            finally:
                released.set()
                await pending
    asyncio.run(check())
