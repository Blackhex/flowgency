from dataclasses import replace
from pathlib import Path

from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.reconciliation import reconcile_jobs, worker_alive
from agency.jobs.store import job_path, read_job, write_job


def running_decision_job(tmp_path: Path, pid: int = 999999):
    group = tmp_path / "group"
    decision = group / "shared" / "decisions" / "change.md"
    decision.parent.mkdir(parents=True)
    spec = JobSpec.create(
        config_path=tmp_path / "config.yaml",
        group_key="test",
        agent_name="product",
        trigger="decision",
        prompt_source={"type": "decision"},
        prompt_content="run",
        decision_context={
            "decision_path": str(decision),
            "proposal_path": "proposal.md",
        },
    )
    decision.write_text(
        f"---\nexecution_status: running\nexecution_job_id: {spec.job_id}\n---\n"
    )
    path = job_path(group, spec.job_id)
    write_job(
        path,
        replace(JobRecord.from_spec(spec), status="running", worker_pid=pid),
    )
    return group, decision, path


def test_reconcile_leaves_live_worker_running(tmp_path, monkeypatch):
    group, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: True)
    result = reconcile_jobs({"test": {"path": str(group)}})
    assert result.left_running == 1
    assert read_job(path).status == "running"
    assert "execution_status: running" in decision.read_text()


def test_reconcile_marks_confirmed_dead_worker_failed(tmp_path, monkeypatch):
    group, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)
    result = reconcile_jobs({"test": {"path": str(group)}})
    assert result.failed == 1
    record = read_job(path)
    assert record.status == "failed"
    assert record.completed_at is not None
    assert record.execution_summary == "Worker process (PID 999999) was not found."
    assert "execution_status: failed" in decision.read_text()


def test_reconcile_leaves_uncertain_worker_running(tmp_path, monkeypatch):
    group, _, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: None)
    result = reconcile_jobs({"test": {"path": str(group)}})
    assert result.left_running == 1
    assert read_job(path).status == "running"


def test_superseded_running_decision_without_job_id_is_not_failed(tmp_path):
    group = tmp_path / "group"
    decision = group / "shared" / "decisions" / "superseded.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("---\nexecution_status: running\n---\n")
    reconcile_jobs({"test": {"path": str(group)}})
    assert "execution_status: running" in decision.read_text()


def test_reconcile_ignores_malformed_job_and_logs_warning(tmp_path, caplog):
    jobs = tmp_path / "group" / "shared" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "broken.yaml").write_text("spec: [")

    result = reconcile_jobs({"test": {"path": str(tmp_path / "group")}})

    assert result.failed == 0
    assert result.left_running == 0
    assert "broken.yaml" in caplog.text


def test_worker_alive_rejects_missing_and_invalid_pids():
    assert worker_alive(None) is None
    assert worker_alive(0) is None
    assert worker_alive(-1) is None