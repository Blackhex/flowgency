from pathlib import Path
import threading
import time
from types import SimpleNamespace

import os
import subprocess

import yaml

from agency.integrations import FileChange, RunResult
from agency.integrations.models import IntegrationRunRequest
from agency.jobs.artifacts import JobArtifact
from agency.jobs.execution import execute_job
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import cancel_job
from agency.jobs.reconciliation import worker_alive
from agency.jobs.store import read_job, write_job
from agency.jobs.worker import main as worker_main
from agency.memory import MemoryStore
from agency.memory.selectors import resolve_memory_selector
from agency.configuration.models import MemorySelector
from agency.blueprints.cache import active_pins, pin_artifact
from agency.fs.locks import exclusive_lock


def queued_job(tmp_path: Path, *, decision_context=None):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups: {}\n", encoding="utf-8")
    cache_path = tmp_path / ".compat-cache" / "script" / "v1" / "unresolved"
    runtime_path = cache_path / "runtime"
    runtime_path.mkdir(parents=True, exist_ok=True)
    (runtime_path / "agent.md").write_text("run\n", encoding="utf-8")
    resolved = resolve_memory_selector(
        MemorySelector(scope="run"),
        job_id="placeholder",
        group_key="test",
        agent_name="product",
        routine_id=None,
        channels={},
        store_root=tmp_path / ".compat-memory-root",
    )
    group_path = tmp_path / "group"
    spec = JobSpec(
        schema_version=2,
        job_id="queued-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="test",
        group_path=str(group_path.resolve()),
        agent_name="product",
        workspace_dir=str(group_path.resolve()),
        trigger="decision" if decision_context else "manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="compat-unresolved",
            source_digest="compat-unresolved",
            integration="script",
            projector_version="v1",
            cache_path=str(cache_path.resolve()),
        ),
        routine_id=None if decision_context else "daily-review",
        skill=None if decision_context else "daily-review",
        skill_arguments=(),
        task_input="Immutable instructions",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "run"},
            canonical_json=resolved.canonical_json,
            memory_hash=resolved.memory_hash,
            path=str(resolved.directory.resolve()),
        ),
        trigger_context=decision_context,
        prompt_source={"type": "decision" if decision_context else "saved_prompt"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    path = group_path / "shared" / "jobs" / f"{spec.job_id}.yaml"
    write_job(path, JobRecord.from_spec(spec))
    return path, spec


def memory_bound_job(tmp_path: Path):
    group_path = tmp_path / "group"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups: {}\n", encoding="utf-8")
    cache_path = tmp_path / "compiled-agents" / "script" / "v1" / "digest"
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="placeholder",
        group_key="test",
        agent_name="product",
        routine_id="daily-review",
        channels={},
        store_root=tmp_path / "memory-store",
    )
    spec = JobSpec(
        schema_version=2,
        job_id="memory-bound-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="test",
        group_path=str(group_path.resolve()),
        agent_name="product",
        workspace_dir=str(group_path.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={"command": "echo ok"},
        blueprint=BlueprintRef(
            key="builder-blueprint",
            source_digest="digest",
            integration="script",
            projector_version="v1",
            cache_path=str(cache_path.resolve()),
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=(),
        task_input="Immutable instructions",
        runtime_policy=RuntimePolicySnapshot(
            timeout=30,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent"},
            canonical_json=resolved.canonical_json,
            memory_hash=resolved.memory_hash,
            path=str(resolved.directory.resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "saved_prompt", "path": "shared/prompts/routine.md"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    path = group_path / "shared" / "jobs" / f"{spec.job_id}.yaml"
    write_job(path, JobRecord.from_spec(spec))
    return path, spec


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "tracked.txt").write_text("line1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")


class MemoryJobFixture:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.job_path, self.spec = memory_bound_job(tmp_path)
        self.group_path = Path(self.spec.group_path)
        self.memory_root = tmp_path / "memory-store"
        self.store = MemoryStore(self.memory_root)
        self.resolved = resolve_memory_selector(
            MemorySelector(scope="agent"),
            job_id=self.spec.job_id,
            group_key=self.spec.group_key,
            agent_name=self.spec.agent_name,
            routine_id=self.spec.routine_id,
            channels={},
            store_root=self.memory_root,
        )
        seeded = self.store.ensure(self.resolved)
        self.store.try_save(self.resolved, seeded.revision, {"memory.md": b"old"})

    def read(self):
        return read_job(self.job_path)


def test_execute_job_waits_for_memory_before_starting_run(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    seen = {}
    finished = threading.Event()
    held_lock = fixture.store._lock_path(fixture.resolved)

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["started_at"] = read_job(fixture.job_path).started_at
            seen["status"] = read_job(fixture.job_path).status
            finished.set()
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_dir=fixture.group_path,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=fixture.group_path,
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    with exclusive_lock(held_lock, wait=True):
        worker = threading.Thread(target=execute_job, args=(fixture.job_path,))
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if fixture.read().status == "waiting_for_memory":
                break
            time.sleep(0.02)
        record = fixture.read()
        assert record.status == "waiting_for_memory"
        assert record.started_at is None
        assert not finished.is_set()

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert seen == {
        "started_at": read_job(fixture.job_path).started_at,
        "status": "running",
    }
    assert read_job(fixture.job_path).status == "complete"


def test_execute_job_cancellation_while_waiting_terminalizes_without_run(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    held_lock = fixture.store._lock_path(fixture.resolved)
    called = {"run": 0}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            called["run"] += 1
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_dir=fixture.group_path,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=fixture.group_path,
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    with exclusive_lock(held_lock, wait=True):
        worker = threading.Thread(target=execute_job, args=(fixture.job_path,))
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if fixture.read().status == "waiting_for_memory":
                break
            time.sleep(0.02)
        assert cancel_job(fixture.job_path).status == "cancelled"

    worker.join(timeout=5)
    assert not worker.is_alive()
    record = read_job(fixture.job_path)
    assert record.status == "cancelled"
    assert record.started_at is None
    assert called["run"] == 0


def test_job_execution_has_no_selector_lock_authority():
    import inspect
    import agency.jobs.execution as execution

    source = inspect.getsource(execution)
    assert ".selectors" not in source
    assert "_selector_lock_path" not in source


def test_execute_job_failed_run_keeps_canonical_memory_and_retains_stage(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["memory_working_dir"] = request.memory_working_dir
            assert request.memory_working_dir is not None
            Path(request.memory_working_dir, "memory.md").write_text("new", encoding="utf-8")
            return RunResult(1, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_dir=fixture.group_path,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=fixture.group_path,
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(fixture.job_path)

    assert result.status == "failed"
    assert fixture.store.read(fixture.resolved).files == {"memory.md": b"old"}
    assert any(
        JobArtifact(**artifact).name == "memory.diff"
        for artifact in (result.memory_publication or {}).get("failed_artifacts", [])
    )
    assert seen["memory_working_dir"]


def test_execute_job_releases_cache_pin_after_terminal_state(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    artifact = fixture.spec.blueprint.to_artifact()
    artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(fixture.spec.blueprint.cache_root, artifact.ref, fixture.spec.job_id)

    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_dir=fixture.group_path,
            integration=SimpleNamespace(run=lambda request: RunResult(0, "done", "", 0.1)),
            timeout=30,
            sandbox_root=None,
            group_path=fixture.group_path,
        ),
    )

    result = execute_job(fixture.job_path)

    assert result.status == "complete"
    assert active_pins(fixture.spec.blueprint.cache_root, artifact.ref) == ()


def test_execute_job_persists_execution_evidence_when_publication_failure_pre_fails_job(
    tmp_path,
    monkeypatch,
):
    _init_repo(tmp_path / "group")
    fixture = MemoryJobFixture(tmp_path)
    artifact = fixture.spec.blueprint.to_artifact()
    artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(
        fixture.spec.blueprint.cache_root,
        artifact.ref,
        fixture.spec.job_id,
    )

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            Path(request.memory_working_dir, "memory.md").write_text(
                "new",
                encoding="utf-8",
            )
            return RunResult(
                0,
                "done",
                "warn",
                1.5,
                [FileChange("a.py", "modified", 2, 1)],
            )

    context = SimpleNamespace(
        workspace_dir=fixture.group_path,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=fixture.group_path,
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: context,
    )

    from agency.memory.publication import MemoryPublicationError

    def fail_after_task9_terminalization(prepared, **kwargs):
        from agency.jobs.store import transition_job

        transition_job(
            fixture.job_path,
            "running",
            "failed",
            completed_at="2026-07-15T12:00:00+00:00",
            execution_summary="Memory publication failed: simulated",
        )
        raise MemoryPublicationError("simulated")

    monkeypatch.setattr(
        "agency.jobs.execution.apply_publication",
        fail_after_task9_terminalization,
    )

    result = execute_job(fixture.job_path)

    assert result.status == "failed"
    assert result.stdout_path is not None
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "done"
    assert result.stderr_path is not None
    assert Path(result.stderr_path).read_text(encoding="utf-8") == "warn"
    assert result.exit_code == 0
    assert result.duration_seconds == 1.5
    assert result.changed_files == [
        {
            "path": "a.py",
            "status": "modified",
            "lines_added": 2,
            "lines_removed": 1,
        }
    ]
    assert result.base_sha is not None
    assert result.completed_at == "2026-07-15T12:00:00+00:00"
    assert result.memory_publication is not None
    assert result.memory_publication.get("failed_artifacts")
    assert active_pins(fixture.spec.blueprint.cache_root, artifact.ref) == ()


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
            seen["workspace_dir"] = request.workspace_dir
            return RunResult(
                0,
                "done",
                "warning",
                1.25,
                [FileChange("a.py", "modified", 2, 1)],
            )

    context = SimpleNamespace(
        workspace_dir=tmp_path / "group",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=tmp_path / "group",
    )
    context.workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(path)

    assert seen == {
        "running": "running",
        "prompt": "Immutable instructions",
        "workspace_dir": tmp_path / "group",
    }
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


def test_execute_job_uses_resolved_skill_from_canonical_snapshot(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["skill"] = request.skill
            seen["sandbox_mode"] = request.runtime_policy.sandbox_mode
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_dir=tmp_path / "group",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        group_path=tmp_path / "group",
    )
    context.workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(path)

    assert result.status == "complete"
    assert seen == {"skill": "daily-review", "sandbox_mode": "unrestricted"}


def test_execute_job_does_not_create_empty_error_log(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    workspace_dir = tmp_path / "group"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_dir=workspace_dir,
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
        workspace_dir=tmp_path / "group",
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
            workspace_dir=tmp_path / "group",
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
            workspace_dir=tmp_path / "group",
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
            workspace_dir=tmp_path / "group",
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
            workspace_dir=tmp_path / "group",
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
