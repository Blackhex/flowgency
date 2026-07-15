from agency.configuration.models import MemorySelector
from agency.jobs.artifacts import JobArtifact, retain_failed_stage
from agency.memory import MemoryStore, resolve_memory_selector


def test_retain_failed_stage_persists_stage_files_and_diff(tmp_path):
    group_path = tmp_path / "group"
    memory_root = tmp_path / "memory-store"
    store = MemoryStore(memory_root)
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="job-123",
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
    stage = store.stage(resolved, job_id="job-123")
    (stage.directory / "memory.md").write_bytes(b"new\n")

    artifacts = retain_failed_stage(
        group_path=group_path,
        job_id="job-123",
        stage_directory=stage.directory,
        diff_bytes=b"--- old\n+++ new\n",
    )

    assert {artifact.name for artifact in artifacts} == {
        "memory.diff",
        "memory.md",
        "notes.md",
    }
    assert all(isinstance(artifact, JobArtifact) for artifact in artifacts)
    artifact_root = (
        group_path / "shared" / "jobs" / "artifacts" / "job-123"
    )
    assert (artifact_root / "memory.md").read_bytes() == b"new\n"
    assert (artifact_root / "notes.md").read_bytes() == b"stable\n"
    assert (artifact_root / "memory.diff").read_bytes() == (
        b"--- old\n+++ new\n"
    )
