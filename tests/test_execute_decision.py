from pathlib import Path
from types import SimpleNamespace

import yaml

import agency.app as app_mod
from agency.config import SandboxSpec
from agency.integrations import FileChange, RunResult
from agency.jobs.execution import execute_job
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import write_job


def queued_decision_job(tmp_path: Path, *, decision_name: str = "prop.md") -> tuple[Path, Path, JobSpec]:
    group_path = tmp_path / "agents"
    decision_path = group_path / "shared" / "decisions" / decision_name
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    spec = JobSpec.create(
        config_path=tmp_path / "config.yaml",
        group_key="grp",
        agent_name="worker",
        trigger="decision",
        prompt_source={"type": "decision"},
        prompt_content="Immutable instructions",
        decision_context={
            "decision_path": str(decision_path),
            "proposal_path": "proposal.md",
        },
    )
    decision_path.write_text(
        f"---\nexecution_job_id: {spec.job_id}\nexecution_status: pending\n---\n",
        encoding="utf-8",
    )
    job_path = group_path / "shared" / "jobs" / f"{spec.job_id}.yaml"
    write_job(job_path, JobRecord.from_spec(spec))
    return group_path, decision_path, spec


def _read_meta(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    _, frontmatter, _ = text.split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def test_execute_job_projects_running_and_success_with_sandbox(tmp_path, monkeypatch):
    group_path, decision, spec = queued_decision_job(tmp_path)
    seen = {}
    repo = tmp_path / "repo"

    class FakeIntegration:
        supports_execution = True
        name = "copilot"

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            seen["sandbox_root"] = sandbox_root
            seen["prompt"] = prompt_file.read_text(encoding="utf-8")
            meta = _read_meta(decision)
            seen["executed_by"] = meta.get("executed_by")
            seen["execution_status"] = meta.get("execution_status")
            return RunResult(
                exit_code=0,
                stdout="did work",
                stderr="",
                duration_seconds=1.0,
                changed_files=[FileChange("a.txt", "modified", 2, 1)],
            )

    context = SimpleNamespace(
        group_path=group_path,
        agent_dir=group_path / "worker",
        timeout=30,
        sandbox_root=SandboxSpec(roots=(repo,), allowed_tools=()),
        integration=FakeIntegration(),
    )
    context.agent_dir.mkdir(parents=True)
    monkeypatch.setattr("agency.jobs.execution.resolve_job_context", lambda ignored: context)

    execute_job(group_path / "shared" / "jobs" / f"{spec.job_id}.yaml")

    meta = _read_meta(decision)
    assert seen["sandbox_root"] == SandboxSpec(roots=(repo,), allowed_tools=())
    assert seen["prompt"] == "Immutable instructions"
    assert seen["executed_by"] == "worker"
    assert seen["execution_status"] == "running"
    assert meta["execution_status"] == "complete"
    assert meta["execution_agent"] == "worker"
    assert meta["executed_by"] == "worker"
    assert Path(meta["execution_log"]).is_absolute()
    assert meta["execution_log"].endswith(".out")
    assert meta["changed_files"] == [
        {"path": "a.txt", "status": "modified", "lines_added": 2, "lines_removed": 1}
    ]


def test_execute_job_projects_empty_changed_files_on_retry(tmp_path, monkeypatch):
    group_path, decision, spec = queued_decision_job(tmp_path, decision_name="retry.md")

    context = SimpleNamespace(
        group_path=group_path,
        agent_dir=group_path / "worker",
        timeout=30,
        sandbox_root=None,
        integration=SimpleNamespace(
            run=lambda *args, **kwargs: RunResult(
                exit_code=0,
                stdout="no changes made",
                stderr="",
                duration_seconds=0.5,
                changed_files=[],
            )
        ),
    )
    context.agent_dir.mkdir(parents=True)
    monkeypatch.setattr("agency.jobs.execution.resolve_job_context", lambda ignored: context)

    execute_job(group_path / "shared" / "jobs" / f"{spec.job_id}.yaml")

    meta = _read_meta(decision)
    assert meta["execution_status"] == "complete"
    assert meta["changed_files"] == []


def test_execute_job_projects_failed_status(tmp_path, monkeypatch):
    group_path, decision, spec = queued_decision_job(tmp_path, decision_name="failed.md")

    context = SimpleNamespace(
        group_path=group_path,
        agent_dir=group_path / "worker",
        timeout=30,
        sandbox_root=None,
        integration=SimpleNamespace(
            run=lambda *args, **kwargs: RunResult(
                exit_code=3,
                stdout="",
                stderr="error",
                duration_seconds=0.2,
                changed_files=[],
            )
        ),
    )
    context.agent_dir.mkdir(parents=True)
    monkeypatch.setattr("agency.jobs.execution.resolve_job_context", lambda ignored: context)

    execute_job(group_path / "shared" / "jobs" / f"{spec.job_id}.yaml")

    meta = _read_meta(decision)
    assert meta["execution_status"] == "failed"
    assert meta["execution_summary"] == "Agent exited with code 3."


def test_recover_orphaned_executions_resets_running_to_failed(tmp_path, monkeypatch):
    """A decision left at execution_status 'running' after a restart/crash is
    orphaned (its in-process background task is gone). Startup recovery must
    reset it to 'failed' so it surfaces a retry action, while leaving decisions
    in any other state untouched."""
    group_path = tmp_path / "agents"
    decisions = group_path / "shared" / "decisions"
    decisions.mkdir(parents=True)

    stuck = decisions / "stuck.md"
    stuck.write_text("---\nexecution_status: running\n---\n")
    done = decisions / "done.md"
    done.write_text("---\nexecution_status: complete\n---\n")
    fresh = decisions / "fresh.md"
    fresh.write_text("---\ndecided_by: admin\n---\n")

    monkeypatch.setattr(app_mod, "GROUPS", {"g": {"path": str(group_path)}})

    recovered = app_mod.recover_orphaned_executions()

    assert recovered == 1
    meta_stuck, _ = app_mod.parse_frontmatter(stuck.read_text())
    assert meta_stuck["execution_status"] == "failed"
    assert "interrupted" in meta_stuck["execution_summary"].lower()

    meta_done, _ = app_mod.parse_frontmatter(done.read_text())
    assert meta_done["execution_status"] == "complete"
    meta_fresh, _ = app_mod.parse_frontmatter(fresh.read_text())
    assert "execution_status" not in meta_fresh


def test_recover_orphaned_executions_handles_missing_dir(tmp_path, monkeypatch):
    """Groups without a decisions directory must not raise during recovery."""
    monkeypatch.setattr(
        app_mod, "GROUPS",
        {"g": {"path": str(tmp_path / "nonexistent")}, "h": {}},
    )
    assert app_mod.recover_orphaned_executions() == 0

