def test_dashboard_and_jobs_do_not_touch_retired_records(workflow_web_env, tmp_path):
    env = workflow_web_env
    retired_record = env.team_root / "observations" / "old.md"
    retired_record.parent.mkdir(parents=True)
    retired_record.write_text(
        "---\nstatus: open\nttl_days: 1\ndate: 2000-01-01\n---\nOld record\n",
        encoding="utf-8",
    )
    before = retired_record.read_bytes()

    for url in ("/newsletter/", "/newsletter/agents", "/newsletter/jobs", env.base_path):
        assert env.client.get(url).status_code == 200

    assert retired_record.read_bytes() == before

    for url in ("/newsletter/observations", "/newsletter/proposals", "/newsletter/decisions"):
        assert env.client.get(url).status_code in (404, 410)
        assert env.client.post(url, data={}).status_code in (404, 405, 410)