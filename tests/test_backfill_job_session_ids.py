from dataclasses import replace

from agency.jobs.store import read_job, write_job
from tests.test_job_routes import _seed_app, _write_job_record
from tools.backfill_job_session_ids import backfill


STDERR = "Changes    +1 -0\nResume     copilot --resume=abc-123\n"


def _seed(monkeypatch, tmp_path, raw_config, *, job_id, session_id=None):
    _client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id=job_id, status="queued")
    stderr_path = tmp_path / f"{job_id}.err"
    stderr_path.write_text(STDERR, encoding="utf-8")
    write_job(
        path,
        replace(
            read_job(path),
            status="complete",
            session_id=session_id,
            stderr_path=str(stderr_path),
        ),
    )
    return config_path, path


def test_backfill_fills_empty_session_id(monkeypatch, tmp_path, raw_config):
    config_path, path = _seed(monkeypatch, tmp_path, raw_config, job_id="job-fill")

    backfill(config_path, team="newsletter")

    assert read_job(path).session_id == "abc-123"


def test_backfill_leaves_populated_session_id(monkeypatch, tmp_path, raw_config):
    config_path, path = _seed(
        monkeypatch,
        tmp_path,
        raw_config,
        job_id="job-keep",
        session_id="already",
    )

    backfill(config_path, team="newsletter")

    assert read_job(path).session_id == "already"


def test_backfill_dry_run_writes_nothing(monkeypatch, tmp_path, raw_config):
    config_path, path = _seed(monkeypatch, tmp_path, raw_config, job_id="job-dry")

    results = backfill(config_path, team="newsletter", dry_run=True)

    assert read_job(path).session_id is None
    assert ("job-dry", "filled") in results
