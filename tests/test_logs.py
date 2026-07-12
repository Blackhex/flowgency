import os
from datetime import datetime

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
