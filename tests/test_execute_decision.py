from pathlib import Path
from types import SimpleNamespace

import os

import yaml

import agency.app as app_mod
from agency.config import SandboxSpec
from agency.integrations import FileChange, RunResult
from agency.jobs import JobSubmissionError
from agency.jobs.execution import execute_job
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import write_job
from test_proposal_questions import _setup_decision_group


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


def test_decide_submits_embedded_snapshot_and_persists_job_id(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: captured.append(spec) or SimpleNamespace(job_id=spec.job_id))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "engineer"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "approved" in captured[0].prompt_content
    assert "Proposal body" in captured[0].prompt_content
    metadata, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert metadata["execution_agent"] == "engineer"
    assert metadata["execution_job_id"] == captured[0].job_id
    assert metadata["execution_job_history"] == []


def test_retry_defaults_to_persisted_executor_and_appends_history(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    decision_path.write_text(
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    captured = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: captured.append(spec) or SimpleNamespace(job_id=spec.job_id))
    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "engineer"}, follow_redirects=False,
    )
    metadata, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert response.status_code == 303
    assert metadata["execution_job_history"] == ["old-job"]
    assert metadata["execution_job_id"] == captured[0].job_id


def test_launch_failure_rolls_back_new_decision(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    monkeypatch.setattr("agency.app.submit_job", lambda spec: (_ for _ in ()).throw(JobSubmissionError("spawn denied", proposal_path)))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "engineer"},
    )
    assert response.status_code == 400
    assert "spawn denied" in response.text
    assert "status: proposed" in proposal_path.read_text()
    assert not decision_path.exists()


def test_retry_launch_failure_restores_original_decision_text(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    original_text = (
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    decision_path.write_text(original_text)
    monkeypatch.setattr(
        "agency.app.submit_job",
        lambda spec: (_ for _ in ()).throw(JobSubmissionError("spawn denied", decision_path)),
    )
    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "engineer"},
    )
    assert response.status_code == 400
    assert "spawn denied" in response.text
    assert decision_path.read_text() == original_text


def _spy_os_replace(monkeypatch):
    """Patch os.replace (used by the app module) to record (tmp_dir, dst) pairs
    while still performing the real rename, so tests can prove the atomic
    same-directory-temp-file + os.replace pattern is actually used."""
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((Path(src).parent, Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    return calls


def test_decide_creates_decision_via_atomic_replace(tmp_path, monkeypatch):
    """Decision creation must write via a same-directory temp file + os.replace,
    not a plain write_text, so a crash mid-write never leaves a truncated file."""
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    monkeypatch.setattr("agency.app.submit_job", lambda spec: SimpleNamespace(job_id=spec.job_id))
    calls = _spy_os_replace(monkeypatch)

    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "engineer"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    matching = [c for c in calls if c[1] == decision_path]
    assert matching, f"expected os.replace(..., {decision_path}); calls={calls}"
    assert matching[0][0] == decision_path.parent


def test_retry_updates_decision_via_atomic_replace(tmp_path, monkeypatch):
    """Retry's decision update (new job id, history append) must go through
    the atomic temp-file + os.replace helper, not plain write_text."""
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    decision_path.write_text(
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    monkeypatch.setattr("agency.app.submit_job", lambda spec: SimpleNamespace(job_id=spec.job_id))
    calls = _spy_os_replace(monkeypatch)

    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "engineer"}, follow_redirects=False,
    )

    assert response.status_code == 303
    matching = [c for c in calls if c[1] == decision_path]
    assert matching, f"expected os.replace(..., {decision_path}); calls={calls}"
    assert matching[0][0] == decision_path.parent


def test_retry_launch_failure_restores_decision_via_atomic_replace(tmp_path, monkeypatch):
    """Retry rollback (restoring the pre-retry decision text after a failed
    submission) must also use the atomic temp-file + os.replace helper."""
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    original_text = (
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    decision_path.write_text(original_text)
    monkeypatch.setattr(
        "agency.app.submit_job",
        lambda spec: (_ for _ in ()).throw(JobSubmissionError("spawn denied", decision_path)),
    )
    calls = _spy_os_replace(monkeypatch)

    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "engineer"},
    )

    assert response.status_code == 400
    assert decision_path.read_text() == original_text
    matching = [c for c in calls if c[1] == decision_path]
    # Two atomic writes hit this decision path during retry+rollback: the
    # pending-update write, then the rollback-to-original write.
    assert len(matching) == 2, f"expected 2 os.replace(..., {decision_path}) calls; calls={calls}"
    assert all(parent == decision_path.parent for parent, _ in matching)


def test_retry_invalid_executor_rerenders_decision_detail_with_error(tmp_path, monkeypatch):
    """An invalid executor on retry must re-render decision_detail.html with a
    visible inline error and HTTP 400, not a bare HTTPException JSON body, and
    must leave the decision file untouched."""
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    original_text = (
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    decision_path.write_text(original_text)

    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "sdk-agent"},
    )

    assert response.status_code == 400
    assert "does not support execution" in response.text
    assert "text/html" in response.headers["content-type"]
    assert decision_path.read_text() == original_text


def test_retry_launch_failure_rerenders_decision_detail_with_error(tmp_path, monkeypatch):
    """A submission failure on retry must re-render decision_detail.html with a
    visible inline error and HTTP 400, not a bare HTTPException JSON body."""
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    original_text = (
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    decision_path.write_text(original_text)
    monkeypatch.setattr(
        "agency.app.submit_job",
        lambda spec: (_ for _ in ()).throw(JobSubmissionError("spawn denied", decision_path)),
    )

    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "engineer"},
    )

    assert response.status_code == 400
    assert "spawn denied" in response.text
    assert "text/html" in response.headers["content-type"]
    assert decision_path.read_text() == original_text
