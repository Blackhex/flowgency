from dataclasses import replace
from pathlib import Path

import pytest

from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import latest_terminal_job, write_job


def _spec(tmp_path: Path, job_id: str, agent_name: str, created_at: str) -> JobSpec:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 4\ngroups: {}\n", encoding="utf-8")
    return JobSpec(
        schema_version=3,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="grp",
        group_root=str(tmp_path.resolve()),
        agent_name=agent_name,
        workspace_root=str(tmp_path.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="product-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "cache" / "entry.py").resolve()),
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "group": "grp", "agent": agent_name},
            canonical_json='{"scope":"agent"}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "prompt", "path": "routine.md"},
        timeout_override=None,
        created_at=created_at,
    )


@pytest.fixture
def store(tmp_path):
    memory_root = tmp_path / "memory"
    job_store = JobStore(memory_root)
    job_store.group_root("grp").mkdir(parents=True, exist_ok=True)
    return job_store


def _write(store, tmp_path, job_id, *, agent_name="product", status, completed_at=None, created_at="2026-07-20T00:00:00+00:00"):
    spec = _spec(tmp_path, job_id, agent_name, created_at)
    record = replace(
        JobRecord.from_spec(spec),
        status=status,
        completed_at=completed_at,
    )
    write_job(store.path("grp", job_id), record)


def test_returns_none_when_no_jobs_exist(store):
    assert latest_terminal_job(tuple(store.paths("grp")), "product") is None


def test_ignores_active_records(store, tmp_path):
    _write(store, tmp_path, "job-running", status="running")
    _write(store, tmp_path, "job-queued", status="queued")
    assert latest_terminal_job(tuple(store.paths("grp")), "product") is None


def test_returns_the_newest_terminal_record_by_completed_at(store, tmp_path):
    _write(store, tmp_path, "job-old", status="complete", completed_at="2026-07-20T10:00:00+00:00")
    _write(store, tmp_path, "job-new", status="failed", completed_at="2026-07-21T10:00:00+00:00")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-new"
    assert record.status == "failed"


def test_falls_back_to_created_at_when_completed_at_is_absent(store, tmp_path):
    _write(store, tmp_path, "job-early", status="cancelled", created_at="2026-07-20T00:00:00+00:00")
    _write(store, tmp_path, "job-late", status="cancelled", created_at="2026-07-22T00:00:00+00:00")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-late"


def test_filters_by_agent_name(store, tmp_path):
    _write(store, tmp_path, "job-other", agent_name="writer", status="failed", completed_at="2026-07-22T10:00:00+00:00")
    _write(store, tmp_path, "job-mine", status="complete", completed_at="2026-07-21T10:00:00+00:00")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-mine"


def test_skips_unreadable_records(store, tmp_path):
    _write(store, tmp_path, "job-good", status="complete", completed_at="2026-07-21T10:00:00+00:00")
    broken = store.path("grp", "job-broken")
    broken.write_text("not: [valid", encoding="utf-8")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-good"
