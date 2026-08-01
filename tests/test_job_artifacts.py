import pytest

from agency.jobs.authority import JobStore
from agency.jobs.artifacts import JobArtifact, retain_failed_stage, retain_rejected_records


def test_retain_rejected_records_labels_both_directories_without_collision(tmp_path):
    job_store = JobStore(tmp_path / "memory").group_root("group")
    job_store.mkdir(parents=True)
    observations = tmp_path / "outbox" / "observations"
    proposals = tmp_path / "outbox" / "proposals"
    observations.mkdir(parents=True)
    proposals.mkdir(parents=True)
    (observations / "a.md").write_bytes(b"observed\n")
    (proposals / "a.md").write_bytes(b"proposed\n")

    artifacts = retain_rejected_records(
        job_store=job_store,
        job_id="job-123",
        sources={"observations": observations, "proposals": proposals},
    )

    assert {artifact.name for artifact in artifacts} == {
        "observations-a.md",
        "proposals-a.md",
    }
    artifact_root = job_store / "artifacts" / "job-123"
    assert (artifact_root / "observations-a.md").read_bytes() == b"observed\n"
    assert (artifact_root / "proposals-a.md").read_bytes() == b"proposed\n"


def test_retain_rejected_records_skips_what_it_cannot_retain(tmp_path):
    """The unretainable entries are the reason for the rejection; keep the rest."""
    job_store = JobStore(tmp_path / "memory").group_root("group")
    job_store.mkdir(parents=True)
    observations = tmp_path / "outbox" / "observations"
    observations.mkdir(parents=True)
    (observations / "good.md").write_bytes(b"kept\n")
    (observations / "notes.txt").write_bytes(b"dropped\n")
    (observations / "shout.MD").write_bytes(b"dropped\n")
    (observations / "nested").mkdir()
    (observations / ".hidden.md").write_bytes(b"dropped\n")

    artifacts = retain_rejected_records(
        job_store=job_store,
        job_id="job-123",
        sources={"observations": observations},
    )

    assert {artifact.name for artifact in artifacts} == {"observations-good.md"}


def test_retain_failed_stage_persists_stage_files_and_diff(tmp_path):
    job_store = JobStore(tmp_path / "memory").group_root("group")
    job_store.mkdir(parents=True)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "memory.md").write_bytes(b"new\n")
    (stage_dir / "notes.md").write_bytes(b"stable\n")

    artifacts = retain_failed_stage(
        job_store=job_store,
        job_id="job-123",
        stage_directory=stage_dir,
        diff_bytes=b"--- old\n+++ new\n",
    )

    assert {artifact.name for artifact in artifacts} == {
        "memory.diff",
        "memory.md",
        "notes.md",
    }
    assert all(isinstance(artifact, JobArtifact) for artifact in artifacts)
    artifact_root = job_store / "artifacts" / "job-123"
    assert (artifact_root / "memory.md").read_bytes() == b"new\n"
    assert (artifact_root / "notes.md").read_bytes() == b"stable\n"
    assert (artifact_root / "memory.diff").read_bytes() == (
        b"--- old\n+++ new\n"
    )


def test_retain_failed_stage_rejects_symlinked_job_store(tmp_path):
    external_root = tmp_path / "external"
    external_root.mkdir()
    job_store = tmp_path / "memory" / ".jobs" / "group"
    job_store.parent.mkdir(parents=True)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "memory.md").write_bytes(b"new\n")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "agency.jobs.artifacts._is_symlink_or_reparse",
        lambda path: path == job_store,
    )
    try:
        with pytest.raises(ValueError, match="unsafe|directory"):
            retain_failed_stage(
                job_store=job_store,
                job_id="job-123",
                stage_directory=stage_dir,
                diff_bytes=b"diff",
            )
    finally:
        monkeypatch.undo()

    assert external_root.exists()
