from pathlib import Path
import inspect
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml

import agency.jobs.execution as execution_module
import agency.jobs.models as job_models_module
import agency.jobs.store as store_module
from agency.jobs.authority import JobStore
from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobRequest,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)
from agency.jobs.store import (
    InvalidJobTransition,
    active_jobs,
    cancel_job,
    group_operation_lock_path,
    job_path,
    read_job,
    transition_job,
    write_job,
)
import agency.config as strict_config_module


def _canonical_group_store(tmp_path: Path) -> Path:
    return JobStore(tmp_path / "memory-store").group_root("newsletter")


def make_spec(tmp_path: Path, agent: str = "product") -> JobSpec:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")
    workspace_root = tmp_path / "workspace"
    group_root = tmp_path / "group"
    return JobSpec(
        schema_version=3,
        job_id=uuid4().hex,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_root=str(group_root.resolve()),
        agent_name=agent,
        workspace_root=str(workspace_root.resolve()),
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="writer",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path="C:/cache/copilot/v1/digest-1",
        ),
        routine_id="routine-1",
        skill="daily-review",
        skill_arguments=("--fast",),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=("C:/repo",),
            tool_mode="allowlist",
            tool_names=("shell", "write"),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "placeholder"},
            canonical_json='{"job":"placeholder","scope":"run","version":1}',
            memory_hash="memory-hash-1",
            path="C:/memory/memory-hash-1",
        ),
        trigger_context={"source": "test"},
        prompt_source={"type": "routine", "routine_id": "routine-1"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )


def test_job_record_round_trips_through_atomic_store(tmp_path):
    spec = make_spec(tmp_path)
    record = JobRecord.from_spec(spec)
    path = job_path(tmp_path / "group", spec.job_id)

    write_job(path, record)

    assert read_job(path) == record
    assert spec.config_path == str((tmp_path / "config.yaml").resolve())


def test_read_job_retries_transient_windows_permission_error(
    tmp_path,
    monkeypatch,
):
    spec = make_spec(tmp_path)
    path = job_path(tmp_path / "group", spec.job_id)
    record = JobRecord.from_spec(spec)
    payload = yaml.safe_dump(record.to_dict(), sort_keys=False)
    attempts = {"count": 0}
    sleeps = []

    def fake_open(self, *args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            error = PermissionError(13, "Access is denied", str(self))
            error.winerror = 5
            raise error
        return io.StringIO(payload)

    monkeypatch.setattr(
        store_module,
        "os",
        SimpleNamespace(name="nt"),
        raising=False,
    )
    monkeypatch.setattr(store_module.Path, "open", fake_open)
    monkeypatch.setattr(
        store_module,
        "_WINDOWS_READ_RETRIES",
        3,
        raising=False,
    )
    monkeypatch.setattr(
        store_module,
        "_WINDOWS_READ_DELAY_SECONDS",
        0.25,
        raising=False,
    )
    monkeypatch.setattr(
        store_module,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    loaded = read_job(path)

    assert loaded == record
    assert attempts["count"] == 3
    assert sleeps == [0.25, 0.25]


def test_read_job_raises_immediately_for_non_windows_permission_error(
    tmp_path,
    monkeypatch,
):
    path = job_path(tmp_path / "group", "job-123")
    attempts = {"count": 0}

    def fake_open(self, *args, **kwargs):
        attempts["count"] += 1
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(
        store_module,
        "os",
        SimpleNamespace(name="posix"),
        raising=False,
    )
    monkeypatch.setattr(store_module.Path, "open", fake_open)
    monkeypatch.setattr(
        store_module,
        "time",
        SimpleNamespace(sleep=lambda seconds: None),
        raising=False,
    )

    with pytest.raises(PermissionError, match="Permission denied"):
        read_job(path)

    assert attempts["count"] == 1


def test_read_job_raises_immediately_for_non_transient_windows_permission_error(
    tmp_path,
    monkeypatch,
):
    path = job_path(tmp_path / "group", "job-123")
    attempts = {"count": 0}

    def fake_open(self, *args, **kwargs):
        attempts["count"] += 1
        error = PermissionError(13, "Permission denied", str(self))
        error.winerror = 32
        raise error

    monkeypatch.setattr(
        store_module,
        "os",
        SimpleNamespace(name="nt"),
        raising=False,
    )
    monkeypatch.setattr(store_module.Path, "open", fake_open)
    monkeypatch.setattr(
        store_module,
        "time",
        SimpleNamespace(sleep=lambda seconds: None),
        raising=False,
    )

    with pytest.raises(PermissionError, match="Permission denied"):
        read_job(path)

    assert attempts["count"] == 1


def test_read_job_raises_last_error_after_transient_windows_retry_exhaustion(
    tmp_path,
    monkeypatch,
):
    path = job_path(tmp_path / "group", "job-123")
    attempts = {"count": 0}
    sleeps = []
    last_error = PermissionError(13, "Access is denied", str(path))
    last_error.winerror = 5

    def fake_open(self, *args, **kwargs):
        attempts["count"] += 1
        raise last_error

    monkeypatch.setattr(
        store_module,
        "os",
        SimpleNamespace(name="nt"),
        raising=False,
    )
    monkeypatch.setattr(store_module.Path, "open", fake_open)
    monkeypatch.setattr(
        store_module,
        "_WINDOWS_READ_RETRIES",
        3,
        raising=False,
    )
    monkeypatch.setattr(
        store_module,
        "_WINDOWS_READ_DELAY_SECONDS",
        0.5,
        raising=False,
    )
    monkeypatch.setattr(
        store_module,
        "time",
        SimpleNamespace(sleep=sleeps.append),
        raising=False,
    )

    with pytest.raises(PermissionError) as error_info:
        read_job(path)

    assert error_info.value is last_error
    assert attempts["count"] == 3
    assert sleeps == [0.5, 0.5]


def test_transition_job_requires_expected_status(tmp_path):
    spec = make_spec(tmp_path)
    path = job_path(tmp_path / "group", spec.job_id)
    write_job(path, JobRecord.from_spec(spec))

    running = transition_job(path, "queued", "running", worker_pid=123)

    assert running.status == "running"
    assert running.worker_pid == 123
    with pytest.raises(InvalidJobTransition):
        transition_job(path, "queued", "failed")


def test_job_spec_requires_routine_and_skill_for_manual_and_scheduled_jobs(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")

    for trigger in ("manual_prompt", "scheduled_prompt"):
        with pytest.raises(ValueError, match="routine_id and skill"):
            JobSpec(
                schema_version=3,
                job_id=uuid4().hex,
                config_path=str(config_path.resolve()),
                config_revision="cfg-1",
                group_key="newsletter",
                group_root=str(tmp_path.resolve()),
                agent_name="product",
                workspace_root=str(tmp_path.resolve()),
                trigger=trigger,
                integration_name="copilot",
                integration_config={},
                blueprint=BlueprintRef(
                    key="writer",
                    source_digest="digest-1",
                    integration="copilot",
                    projector_version="v1",
                    cache_path="C:/cache/copilot/v1/digest-1",
                ),
                runtime_policy=RuntimePolicySnapshot(
                    timeout=1800,
                    sandbox_mode="restricted",
                    sandbox_roots=("C:/repo",),
                    tool_mode="allowlist",
                    tool_names=("shell",),
                ),
                memory=MemoryBinding(
                    selector={"scope": "run", "version": 1, "job": "placeholder"},
                    canonical_json='{"job":"placeholder","scope":"run","version":1}',
                    memory_hash="memory-hash-1",
                    path="C:/memory/memory-hash-1",
                ),
                routine_id=None,
                skill=None,
                skill_arguments=(),
                task_input="run",
                trigger_context={"source": "test"},
                prompt_source={"type": "routine", "routine_id": None},
                timeout_override=None,
                created_at="2026-07-15T00:00:00+00:00",
            ).validate()


def test_job_spec_serializes_distinct_workspace_and_group_roots(tmp_path):
    spec = make_spec(tmp_path)

    payload = spec.to_dict()

    assert payload["workspace_root"] == str(spec.resolved_workspace_root)
    assert payload["group_root"] == str(spec.resolved_group_root)
    assert spec.resolved_workspace_root == Path(spec.workspace_root).resolve()
    assert spec.resolved_group_root == Path(spec.group_root).resolve()


def test_operation_lock_is_under_group_locks(tmp_path):
    assert group_operation_lock_path(tmp_path) == (
        tmp_path / "locks" / ".operations.lock"
    )


def test_job_request_no_longer_accepts_extra_prompt_source(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")

    with pytest.raises(TypeError):
        JobRequest(
            config_path=config_path,
            group_key="newsletter",
            agent_name="product",
            trigger="manual_prompt",
            task_input="run",
            routine_id="routine-1",
            saved_prompt_source={"type": "prompt", "path": "routine.md"},
        )


def test_job_spec_no_longer_exposes_create_constructor():
    assert not hasattr(JobSpec, "create")


def test_decision_jobs_require_null_routine_and_skill(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")

    spec = JobSpec(
        schema_version=3,
        job_id="job-456",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_root=str(tmp_path.resolve()),
        agent_name="product",
        workspace_root=str(tmp_path.resolve()),
        trigger="decision",
        integration_name="copilot",
        integration_config={},
        blueprint=BlueprintRef(
            key="writer",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path="C:/cache/copilot/v1/digest-1",
        ),
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=("C:/repo",),
            tool_mode="allowlist",
            tool_names=("shell",),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "group": "newsletter", "agent": "product"},
            canonical_json='{"agent":"product","group":"newsletter","scope":"agent","version":1}',
            memory_hash="memory-hash-2",
            path="C:/memory/memory-hash-2",
        ),
        routine_id=None,
        skill=None,
        skill_arguments=(),
        task_input="run",
        trigger_context={"decision": "change.md"},
        prompt_source={"type": "decision"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )

    assert spec.routine_id is None
    assert spec.skill is None


def test_job_record_rejects_older_schema_version_one(tmp_path):
    spec = make_spec(tmp_path)
    record = JobRecord.from_spec(spec).to_dict()
    record["spec"]["schema_version"] = 1

    with pytest.raises(ValueError, match="Unsupported job schema version"):
        JobRecord.from_dict(record)


def test_active_jobs_includes_waiting_for_memory(tmp_path):
    group_path = _canonical_group_store(tmp_path)
    queued_spec = make_spec(tmp_path, agent="product")
    waiting_spec = make_spec(tmp_path, agent="product")
    running_spec = make_spec(tmp_path, agent="product")

    write_job(job_path(group_path, queued_spec.job_id), JobRecord.from_spec(queued_spec))
    waiting_path = job_path(group_path, waiting_spec.job_id)
    write_job(waiting_path, JobRecord.from_spec(waiting_spec))
    transition_job(waiting_path, "queued", "waiting_for_memory")
    running_path = job_path(group_path, running_spec.job_id)
    write_job(running_path, JobRecord.from_spec(running_spec))
    transition_job(running_path, "queued", "running")

    records = active_jobs(group_path, "product")

    assert {record.spec.job_id for record in records} == {
        queued_spec.job_id,
        waiting_spec.job_id,
        running_spec.job_id,
    }
    assert {record.status for record in records} == {
        "queued",
        "waiting_for_memory",
        "running",
    }


def test_cancel_job_transitions_waiting_for_memory_without_expected_argument(tmp_path):
    spec = make_spec(tmp_path)
    path = job_path(_canonical_group_store(tmp_path), spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    transition_job(path, "queued", "waiting_for_memory")

    cancelled = cancel_job(path)

    assert cancelled.status == "cancelled"


def test_jobs_context_module_is_removed_from_live_package_surface():
    assert not Path(execution_module.__file__).with_name("context.py").exists()
    assert "jobs.context" not in inspect.getsource(execution_module)
    assert "jobs.context" not in inspect.getsource(store_module)
    assert "jobs.context" not in inspect.getsource(job_models_module)


def test_cancel_job_signature_no_longer_accepts_expected_parameter():
    signature = inspect.signature(cancel_job)

    assert list(signature.parameters) == ["path"]


@pytest.mark.parametrize("status", ["running", "complete", "failed", "cancelled"])
def test_cancel_job_rejects_running_and_terminal_states(tmp_path, status):
    spec = make_spec(tmp_path)
    path = job_path(_canonical_group_store(tmp_path), spec.job_id)
    write_job(path, JobRecord.from_spec(spec))

    if status == "complete":
        transition_job(path, "queued", "running")
        transition_job(path, "running", "complete")
    elif status != "queued":
        transition_job(path, "queued", status)

    with pytest.raises(InvalidJobTransition):
        cancel_job(path)


def test_active_jobs_returns_queued_and_running_for_agent(tmp_path):
    group_path = _canonical_group_store(tmp_path)
    queued_spec = make_spec(tmp_path, agent="product")
    running_spec = make_spec(tmp_path, agent="product")
    other_spec = make_spec(tmp_path, agent="editorial")

    write_job(job_path(group_path, queued_spec.job_id), JobRecord.from_spec(queued_spec))
    running_path = job_path(group_path, running_spec.job_id)
    write_job(running_path, JobRecord.from_spec(running_spec))
    transition_job(running_path, "queued", "running")
    write_job(job_path(group_path, other_spec.job_id), JobRecord.from_spec(other_spec))

    records = active_jobs(group_path, "product")

    assert {record.spec.job_id for record in records} == {
        queued_spec.job_id,
        running_spec.job_id,
    }
    assert {record.status for record in records} == {"queued", "running"}


def test_active_jobs_ignores_malformed_field_types(tmp_path):
    group_path = _canonical_group_store(tmp_path)
    valid_spec = make_spec(tmp_path)
    valid_record = JobRecord.from_spec(valid_spec)
    write_job(job_path(group_path, valid_spec.job_id), valid_record)

    malformed = valid_record.to_dict()
    malformed["spec"]["job_id"] = "malformed"
    malformed["spec"]["prompt_content"] = {"unexpected": "mapping"}
    malformed_path = job_path(group_path, "malformed")
    malformed_path.write_text(
        yaml.safe_dump(malformed, sort_keys=False),
        encoding="utf-8",
    )

    records = active_jobs(group_path)

    assert [record.spec.job_id for record in records] == [valid_spec.job_id]


def test_runtime_config_surface_exposes_only_current_symbols():
    assert not hasattr(strict_config_module, "load_config_path")
