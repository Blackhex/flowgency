import pytest

from agency.jobs.artifacts import JobArtifact, retain_failed_stage


def test_retain_failed_stage_persists_stage_files_and_diff(tmp_path):
    group_path = tmp_path / "group"
    job_store = group_path / "shared" / "jobs"
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
    artifact_root = (
        group_path / "shared" / "jobs" / "artifacts" / "job-123"
    )
    assert (artifact_root / "memory.md").read_bytes() == b"new\n"
    assert (artifact_root / "notes.md").read_bytes() == b"stable\n"
    assert (artifact_root / "memory.diff").read_bytes() == (
        b"--- old\n+++ new\n"
    )


def test_retain_failed_stage_rejects_symlinked_job_store(tmp_path):
    group_path = tmp_path / "group"
    external_root = tmp_path / "external"
    external_root.mkdir()
    job_store = group_path / "shared" / "jobs"
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
