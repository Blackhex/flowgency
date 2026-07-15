from pathlib import Path

import pytest
import yaml

from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)
from agency.jobs.store import (
    InvalidJobTransition,
    active_jobs,
    job_path,
    read_job,
    transition_job,
    write_job,
)
from agency.config import load_config_path


def make_spec(tmp_path: Path, agent: str = "product") -> JobSpec:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups: {}\n", encoding="utf-8")
    return JobSpec.create(
        config_path=config_path,
        group_key="newsletter",
        agent_name=agent,
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        config_revision="cfg-1",
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
            tool_names=("shell", "write"),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "placeholder"},
            canonical_json='{"job":"placeholder","scope":"run","version":1}',
            memory_hash="memory-hash-1",
            path="C:/memory/memory-hash-1",
        ),
        routine_id="routine-1",
        skill="daily-review",
        skill_arguments=("--fast",),
        task_input="# Routine\n",
        trigger_context={"source": "test"},
    )


def test_job_record_round_trips_through_atomic_store(tmp_path):
    spec = make_spec(tmp_path)
    record = JobRecord.from_spec(spec)
    path = job_path(tmp_path / "group", spec.job_id)

    write_job(path, record)

    assert read_job(path) == record
    assert spec.config_path == str((tmp_path / "config.yaml").resolve())


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
    config_path.write_text("groups: {}\n", encoding="utf-8")

    for trigger in ("manual_prompt", "scheduled_prompt"):
        with pytest.raises(ValueError, match="routine_id and skill"):
            JobSpec.create(
                config_path=config_path,
                group_key="newsletter",
                agent_name="product",
                trigger=trigger,
                integration_name="copilot",
                integration_config={},
                config_revision="cfg-1",
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
            )


def test_decision_jobs_require_null_routine_and_skill(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups: {}\n", encoding="utf-8")

    spec = JobSpec.create(
        config_path=config_path,
        group_key="newsletter",
        agent_name="product",
        trigger="decision",
        integration_name="copilot",
        integration_config={},
        config_revision="cfg-1",
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
    )

    assert spec.routine_id is None
    assert spec.skill is None


def test_job_record_rejects_superseded_schema_version_one(tmp_path):
    spec = make_spec(tmp_path)
    record = JobRecord.from_spec(spec).to_dict()
    record["spec"]["schema_version"] = 1

    with pytest.raises(ValueError, match="Unsupported job schema version"):
        JobRecord.from_dict(record)


def test_active_jobs_includes_waiting_for_memory(tmp_path):
    group_path = tmp_path / "group"
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


def test_active_jobs_returns_queued_and_running_for_agent(tmp_path):
    group_path = tmp_path / "group"
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
    group_path = tmp_path / "group"
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


def test_load_config_path_is_independent_of_current_working_directory(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "explicit" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        "agency:\n  title: Explicit Agency\ngroups:\n  explicit: {}\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "config.yaml").write_text(
        "agency:\n  title: Wrong Agency\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(elsewhere)

    config = load_config_path(config_path)

    assert config["agency"]["title"] == "Explicit Agency"
    assert "explicit" in config["groups"]
