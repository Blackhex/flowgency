import asyncio
import json
import os
import threading
import yaml
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import flowgency.app as app_mod
from flowgency.app import build_agent_timeline, collect_logs, get_agent_logs


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
