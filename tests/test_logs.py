from agency.app import build_agent_timeline, collect_logs, get_agent_logs


def test_collect_logs_omits_empty_error_files(tmp_path):
    logs_dir = tmp_path / "shared" / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    (logs_dir / "agent-run.out").write_text("completed")
    (logs_dir / "agent-run.err").write_text("")

    logs = collect_logs({"shared": tmp_path / "shared"})

    assert [entry["name"] for entry in logs["2026-07-12"]] == ["agent-run.out"]


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
