from pathlib import Path
from types import SimpleNamespace

import yaml

from agency.integrations import FileChange, RunResult
from agency.jobs.execution import execute_job
from agency.jobs.models import JobRecord, JobSpec
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

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            seen["running"] = read_job(path).status
            seen["prompt"] = prompt_file.read_text()
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


def test_execute_job_records_exception_as_failed(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    context = SimpleNamespace(
        agent_dir=tmp_path,
        timeout=30,
        sandbox_root=None,
        group_path=tmp_path / "group",
        integration=SimpleNamespace(
            run=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
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
                run=lambda *args, **kwargs: RunResult(0, "done", "", 0.1)
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
                run=lambda *args, **kwargs: RunResult(124, "partial", "timeout", 30.0)
            ),
        ),
    )

    result = execute_job(path)

    assert result.status == "failed"
    assert result.exit_code == 124
    assert result.execution_summary == "Agent exited with code 124."


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
            integration=SimpleNamespace(run=lambda *args, **kwargs: superseded_result),
        ),
    )

    result = execute_job(path)

    assert result.status == "complete"
    assert result.changed_files == []


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
