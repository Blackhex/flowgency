from pathlib import Path
from types import SimpleNamespace

import os

import yaml

from agency.integrations import FileChange, RunResult
from agency.integrations.models import IntegrationRunRequest
from agency.jobs.execution import execute_job
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.reconciliation import worker_alive
from agency.jobs.store import read_job, write_job
from agency.jobs.worker import main as worker_main


def queued_job(tmp_path: Path, *, decision_context=None):
    spec = JobSpec.create(
        config_path=tmp_path / "config.yaml",
        group_key="test",
        agent_name="product",
        trigger="decision" if decision_context else "manual_prompt",
        prompt_source={"type": "decision" if decision_context else "saved_prompt"},
        prompt_content="Immutable instructions",
        decision_context=decision_context,
    )
    path = tmp_path / "group" / "shared" / "jobs" / f"{spec.job_id}.yaml"
    write_job(path, JobRecord.from_spec(spec))
    return path, spec


def read_metadata(path: Path) -> dict:
    _, frontmatter, _ = path.read_text().split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def test_execute_job_transitions_writes_logs_and_changes(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["running"] = read_job(path).status
            seen["prompt"] = request.task_file.read_text()
            return RunResult(
                0,
                "done",
                "warning",
                1.25,
                [FileChange("a.py", "modified", 2, 1)],
            )

    context = SimpleNamespace(
        agent_dir=tmp_path / "group" / "product",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=tmp_path / "group",
    )
    context.agent_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(path)

    assert seen == {"running": "running", "prompt": "Immutable instructions"}
    assert result.status == "complete"
    assert Path(result.stdout_path).read_text() == "done"
    assert Path(result.stderr_path).read_text() == "warning"
    assert result.changed_files == [
        {
            "path": "a.py",
            "status": "modified",
            "lines_added": 2,
            "lines_removed": 1,
        }
    ]
    assert not path.with_suffix(".prompt").exists()
    assert read_job(path) == result


def test_execute_job_does_not_create_empty_error_log(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    agent_dir = tmp_path / "group" / "product"
    agent_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            agent_dir=agent_dir,
            integration=SimpleNamespace(
                run=lambda request: RunResult(0, "done", "", 0.1)
            ),
            timeout=30,
            sandbox_root=None,
            group_path=tmp_path / "group",
        ),
    )

    result = execute_job(path)

    assert result.stderr_path is None
    assert not list((tmp_path / "group" / "shared" / "logs").rglob("*.err"))


def test_execute_job_records_exception_as_failed(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    context = SimpleNamespace(
        agent_dir=tmp_path,
        timeout=30,
        sandbox_root=None,
        group_path=tmp_path / "group",
        integration=SimpleNamespace(
            run=lambda request: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(path)

    assert result.status == "failed"
    assert "boom" in result.execution_summary
    assert result.completed_at is not None
    assert not path.with_suffix(".prompt").exists()


def test_old_decision_job_cannot_overwrite_current_retry(tmp_path, monkeypatch):
    decisions = tmp_path / "group" / "shared" / "decisions"
    decisions.mkdir(parents=True)
    decision = decisions / "proposal.md"
    decision.write_text(
        "---\nexecution_job_id: newer-job\nexecution_status: running\n---\n"
    )
    path, _ = queued_job(
        tmp_path,
        decision_context={
            "decision_path": str(decision),
            "proposal_path": "proposal.md",
        },
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            agent_dir=tmp_path,
            timeout=30,
            sandbox_root=None,
            group_path=tmp_path / "group",
            integration=SimpleNamespace(
                run=lambda request: RunResult(0, "done", "", 0.1)
            ),
        ),
    )

    execute_job(path)

    assert read_metadata(decision) == {
        "execution_job_id": "newer-job",
        "execution_status": "running",
    }


def test_execute_job_treats_timeout_exit_code_as_failed(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            agent_dir=tmp_path,
            timeout=30,
            sandbox_root=None,
            group_path=tmp_path / "group",
            integration=SimpleNamespace(
                run=lambda request: RunResult(124, "partial", "timeout", 30.0)
            ),
        ),
    )

    result = execute_job(path)

    assert result.status == "failed"
    assert result.exit_code == 124
    assert result.execution_summary == "Agent timed out after 30 seconds."


def test_execute_job_accepts_result_without_changed_files(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    superseded_result = SimpleNamespace(
        exit_code=0,
        stdout="done",
        stderr="",
        duration_seconds=0.2,
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            agent_dir=tmp_path,
            timeout=30,
            sandbox_root=None,
            group_path=tmp_path / "group",
            integration=SimpleNamespace(run=lambda request: superseded_result),
        ),
    )

    result = execute_job(path)

    assert result.status == "complete"
    assert result.changed_files == []


def test_execute_job_projection_failure_before_run_still_completes(tmp_path, monkeypatch, caplog):
    path, _ = queued_job(tmp_path)
    calls = {"count": 0}

    def flaky_project(record):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("projection write failed")

    monkeypatch.setattr("agency.jobs.execution.project_decision", flaky_project)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            agent_dir=tmp_path,
            timeout=30,
            sandbox_root=None,
            group_path=tmp_path / "group",
            integration=SimpleNamespace(
                run=lambda request: RunResult(0, "done", "", 0.1)
            ),
        ),
    )

    result = execute_job(path)

    assert calls["count"] == 2
    assert result.status == "complete"
    assert read_job(path).status == "complete"
    assert "projection write failed" in caplog.text


def test_execute_job_projection_failure_before_run_still_fails(tmp_path, monkeypatch, caplog):
    path, _ = queued_job(tmp_path)
    calls = {"count": 0}

    def flaky_project(record):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("projection read failed")

    monkeypatch.setattr("agency.jobs.execution.project_decision", flaky_project)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            agent_dir=tmp_path,
            timeout=30,
            sandbox_root=None,
            group_path=tmp_path / "group",
            integration=SimpleNamespace(
                run=lambda request: (_ for _ in ()).throw(RuntimeError("boom"))
            ),
        ),
    )

    result = execute_job(path)

    assert calls["count"] == 2
    assert result.status == "failed"
    assert read_job(path).status == "failed"
    assert "projection read failed" in caplog.text


def test_execute_job_records_live_worker_pid_for_reconciliation(tmp_path, monkeypatch):
    """SystemdRunLauncher reports no PID to the submitter (LaunchResult.worker_pid
    is None). This proves execute_job's own queued->running transition records
    the worker's real, confirmable PID regardless of what the launcher reported,
    so reconciliation always has a usable PID for a running job."""
    path, _ = queued_job(tmp_path)
    assert read_job(path).worker_pid is None

    captured = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            running = read_job(path)
            captured["status"] = running.status
            captured["pid"] = running.worker_pid
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        agent_dir=tmp_path / "group" / "product",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=tmp_path / "group",
    )
    context.agent_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(path)

    assert captured["status"] == "running"
    assert captured["pid"] == os.getpid()
    assert worker_alive(captured["pid"]) is True
    assert result.status == "complete"
    assert result.worker_pid == os.getpid()


def test_worker_returns_status_as_exit_code(tmp_path, monkeypatch):
    job_path = tmp_path / "job.yaml"
    seen = []

    def fake_execute(path):
        seen.append(path)
        return SimpleNamespace(status="complete")

    monkeypatch.setattr("agency.jobs.worker.execute_job", fake_execute)
    assert worker_main([str(job_path)]) == 0

    monkeypatch.setattr(
        "agency.jobs.worker.execute_job",
        lambda path: SimpleNamespace(status="failed"),
    )
    assert worker_main([str(job_path)]) == 1
    assert seen == [job_path.resolve()]
