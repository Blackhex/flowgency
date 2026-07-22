from dataclasses import replace

import pytest

from agency.configuration.models import MemorySelector
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import read_job, write_job
from agency.memory import MemoryStore, resolve_memory_selector
from agency.memory.publication import (
    MemoryPublicationError,
    apply_publication,
    finalize_publication,
    prepare_publication,
)


@pytest.fixture
def publication_fixture(tmp_path):
    group_path = tmp_path / "group"
    group_path.mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups: {}\n", encoding="utf-8")
    memory_root = tmp_path / "memory-store"
    spec = JobSpec(
        schema_version=3,
        job_id="publication-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="news",
        group_root=str(group_path.resolve()),
        agent_name="writer",
        workspace_root=str(group_path.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="writer-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
        ),
        routine_id="publish-memory",
        skill="publish-memory",
        skill_arguments=(),
        task_input="Publish memory",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent"},
            canonical_json='{"scope":"agent"}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory-store" / "agent").resolve()),
        ),
        trigger_context=None,
        prompt_source={
            "type": "saved_prompt",
            "path": "shared/prompts/routine.md",
        },
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    authority_store = JobStore(memory_root)
    group_job_store = authority_store.group_root("news")
    group_job_store.mkdir(parents=True, exist_ok=True)
    job_path = authority_store.path("news", spec.job_id)
    queued = JobRecord.from_spec(spec)
    write_job(job_path, queued)
    job = read_job(job_path)
    write_job(job_path, replace(job, status="running"))
    store = MemoryStore(memory_root)
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id=spec.job_id,
        group_key="news",
        agent_name="writer",
        routine_id=None,
        channels={},
        store_root=memory_root,
    )
    seeded = store.ensure(resolved)
    store.try_save(
        resolved,
        seeded.revision,
        {"memory.md": b"old\n", "notes.md": b"stable\n"},
    )
    stage = store.stage(resolved, job_id=spec.job_id)
    return {
        "group_root": group_path,
        "job_path": job_path,
        "job_store": group_job_store,
        "job_id": spec.job_id,
        "store": store,
        "resolved": resolved,
        "stage": stage,
    }


def test_zero_exit_is_not_complete_when_memory_validation_fails(
    publication_fixture,
):
    stage = publication_fixture["stage"]
    store = publication_fixture["store"]
    job_store = publication_fixture["job_store"]
    (stage.directory / "nested").mkdir()

    with pytest.raises(MemoryPublicationError):
        prepare_publication(stage, job_store=job_store)

    assert store.read(stage.resolved).files == {
        "memory.md": b"old\n",
        "notes.md": b"stable\n",
    }
    job_path = publication_fixture["job_path"]
    assert read_job(job_path).status == "running"


def test_publication_records_receipt_without_replacing_files_when_unchanged(
    publication_fixture,
):
    stage = publication_fixture["stage"]
    store = publication_fixture["store"]
    job_path = publication_fixture["job_path"]
    job_store = publication_fixture["job_store"]

    prepared = prepare_publication(stage, job_store=job_store)
    receipt = finalize_publication(apply_publication(prepared))

    assert receipt.no_change is True
    assert receipt.old_revision == receipt.new_revision
    assert store.read(stage.resolved).files == {
        "memory.md": b"old\n",
        "notes.md": b"stable\n",
    }
    assert read_job(job_path).status == "complete"


def test_publication_failure_restores_old_canonical_and_fails_job(
    publication_fixture,
):
    stage = publication_fixture["stage"]
    store = publication_fixture["store"]
    job_path = publication_fixture["job_path"]
    job_store = publication_fixture["job_store"]
    (stage.directory / "memory.md").write_bytes(b"new\n")

    prepared = prepare_publication(stage, job_store=job_store)

    with pytest.raises(MemoryPublicationError):
        apply_publication(prepared, fail_after_publish=True)

    assert store.read(stage.resolved).files == {
        "memory.md": b"old\n",
        "notes.md": b"stable\n",
    }
    assert read_job(job_path).status == "failed"


def test_prepare_and_apply_reject_stale_stage_without_touching_canonical(
    publication_fixture,
):
    stage = publication_fixture["stage"]
    store = publication_fixture["store"]
    job_path = publication_fixture["job_path"]
    job_store = publication_fixture["job_store"]

    (stage.directory / "memory.md").write_bytes(b"from-stage\n")
    prepared = prepare_publication(stage, job_store=job_store)

    store.try_save(
        stage.resolved,
        stage.base_revision,
        {"memory.md": b"from-canonical\n", "notes.md": b"stable\n"},
    )

    with pytest.raises(MemoryPublicationError, match="stale|conflict"):
        prepare_publication(stage, job_store=job_store)

    with pytest.raises(MemoryPublicationError, match="stale|conflict"):
        apply_publication(prepared)

    assert store.read(stage.resolved).files == {
        "memory.md": b"from-canonical\n",
        "notes.md": b"stable\n",
    }
    assert not prepared.journal_path.exists()
    assert read_job(job_path).status == "running"


def test_publication_rejects_hostile_external_job_path_and_preserves_sentinel(
    publication_fixture,
):
    stage = publication_fixture["stage"]
    job_store = publication_fixture["job_store"]
    hostile_group = publication_fixture["group_root"].parent / "hostile"
    hostile_job_store = hostile_group / ".jobs" / "hostile"
    hostile_job_store.mkdir(parents=True)
    hostile_job_path = hostile_job_store / f"{publication_fixture['job_id']}.yaml"
    hostile_job_path.write_text("sentinel", encoding="utf-8")
    sentinel = hostile_group / "sentinel.txt"
    sentinel.write_text("keep-me", encoding="utf-8")

    with pytest.raises(MemoryPublicationError, match="job store|unsafe"):
        prepare_publication(
            stage,
            job_store=job_store,
            job_path=hostile_job_path,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep-me"
    assert hostile_job_path.read_text(encoding="utf-8") == "sentinel"
    assert job_store.exists()


def test_publication_rejects_symlinked_jobs_directory(
    publication_fixture,
):
    stage = publication_fixture["stage"]
    job_store = publication_fixture["job_store"]
    linked_jobs = publication_fixture["group_root"].parent / "linked-group" / ".jobs" / "linked-group"
    linked_jobs.parent.mkdir(parents=True)
    with pytest.raises(MemoryPublicationError, match="unsafe|job store"):
        prepare_publication(stage, job_store=linked_jobs)
