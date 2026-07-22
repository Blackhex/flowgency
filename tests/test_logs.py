import os
import yaml
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.app import build_agent_timeline, collect_logs, get_agent_logs


def test_collect_logs_omits_empty_error_files(tmp_path):
    logs_dir = tmp_path / "shared" / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-run.out").write_text("completed")
    (logs_dir / "agent-run.err").write_text("")

    logs = collect_logs({"shared": tmp_path / "shared"})

    assert [entry["name"] for entry in logs["2026-07-12"]] == ["agent-run.out"]


def test_collect_logs_orders_by_mtime_and_prefers_out_for_ties(tmp_path):
    logs_dir = tmp_path / "shared" / "logs" / "2026-07-12"
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

    logs = collect_logs({"shared": tmp_path / "shared"})
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
    logs_dir = tmp_path / "shared" / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-run.out").write_text("completed")
    (logs_dir / "agent-run.err").write_text("")
    group = {"shared": tmp_path / "shared"}

    recent = get_agent_logs(group, "agent")
    timeline = build_agent_timeline(group, "agent", agent_observations=[])

    assert [entry["name"] for entry in recent] == ["agent-run.out"]
    assert [event["name"] for event in timeline] == ["agent-run.out"]


def test_logs_page_displays_local_modification_time(tmp_path, monkeypatch):
    group_path = tmp_path / "test"
    logs_dir = group_path / "shared" / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (group_path / "shared" / "observations").mkdir(parents=True)
    (group_path / "shared" / "proposals").mkdir(parents=True)
    (group_path / "shared" / "decisions").mkdir(parents=True)
    (group_path / "shared" / "prompts").mkdir(parents=True)
    (group_path / "shared" / "memory.md").write_text("# Shared Memory\n", encoding="utf-8")

    log_file = logs_dir / "agent-run.out"
    log_file.write_text("completed", encoding="utf-8")
    mtime = datetime(2026, 7, 12, 20, 6).timestamp()
    os.utime(log_file, (mtime, mtime))

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 3,

                "agency": {
                    "title": "Agency",
                    "default_group": "test",
                    "ai_backend": "claude-code",
                    "agent_library": str((tmp_path / "agent-library").resolve()),
                    "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
                    "memory_store": str((tmp_path / "memory").resolve()),
                },
                "groups": {
                    "test": {
                        "name": "Test Group",
                        "workspace_path": str(group_path.resolve()),
                        "path": str(group_path.resolve()),
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
