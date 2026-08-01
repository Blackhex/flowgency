from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agency.integrations import RunResult
from agency.jobs.execution import execute_job
from agency.records.outbox import (
    MAX_MEMORY_ENTRIES,
    MAX_MEMORY_FILE_BYTES,
    MAX_MEMORY_FILES,
    _is_plain_regular_file,
    copy_outbox_memory_to_stage,
    create_outbox,
)
from test_job_execution import MemoryJobFixture


@pytest.fixture
def launch(tmp_path: Path):
    path = tmp_path / "launch"
    path.mkdir()
    return path


def test_edited_memory_replaces_the_stage_contents(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})
    (outbox.memory / "memory.md").write_text("edited", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert (stage / "memory.md").read_text(encoding="utf-8") == "edited"


def test_untouched_memory_leaves_the_stage_byte_identical(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})

    copy_outbox_memory_to_stage(outbox, stage)

    assert (stage / "memory.md").read_bytes() == b"canonical"


def test_a_new_memory_file_is_copied(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "decisions.md").write_text("new", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert (stage / "decisions.md").read_text(encoding="utf-8") == "new"


def test_a_deleted_memory_file_is_removed_from_the_stage(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})
    (outbox.memory / "memory.md").unlink()

    copy_outbox_memory_to_stage(outbox, stage)

    assert not (stage / "memory.md").exists()


def test_non_markdown_files_in_the_memory_directory_are_ignored(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "scratch.txt").write_text("junk", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert not (stage / "scratch.txt").exists()


def test_subdirectories_in_the_memory_directory_are_rejected(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "nested").mkdir()

    with pytest.raises(ValueError, match="subdirector"):
        copy_outbox_memory_to_stage(outbox, stage)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlinked_markdown_file_in_the_memory_directory_is_rejected(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    target = tmp_path / "outside.md"
    target.write_text("secret", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "link.md").symlink_to(target)

    with pytest.raises(ValueError, match="symlink|reparse"):
        copy_outbox_memory_to_stage(outbox, stage)


def test_untouched_memory_preserves_stage_mtime(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    stage_file = stage / "memory.md"
    stage_file.write_text("canonical", encoding="utf-8")
    # Set mtime to a known past time
    os.utime(stage_file, (1000000000, 1000000000))
    original_mtime_ns = stage_file.stat().st_mtime_ns
    
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})
    # Wait a tiny bit to ensure time would move forward if we rewrote
    time.sleep(0.01)

    copy_outbox_memory_to_stage(outbox, stage)

    # Verify bytes are unchanged
    assert stage_file.read_bytes() == b"canonical"
    # Verify mtime was NOT updated (proving the file was not rewritten)
    assert stage_file.stat().st_mtime_ns == original_mtime_ns


def test_subdirectory_in_the_stage_is_rejected_as_corruption(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "nested").mkdir()
    outbox = create_outbox(launch, memory_files={})

    with pytest.raises(ValueError, match="subdirector"):
        copy_outbox_memory_to_stage(outbox, stage)


def test_posix_symlink_st_mode_rejected_by_plain_regular_file_guard():
    fake = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
    assert not _is_plain_regular_file(fake)


def test_reparse_point_rejected_by_plain_regular_file_guard():
    reparse_flag = 0x0400  # FILE_ATTRIBUTE_REPARSE_POINT
    fake = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_file_attributes=reparse_flag)
    with patch.object(stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag, create=True):
        assert not _is_plain_regular_file(fake)


def test_too_many_memory_files_are_rejected(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    for index in range(MAX_MEMORY_FILES + 1):
        (outbox.memory / f"m{index:02d}.md").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="markdown files"):
        copy_outbox_memory_to_stage(outbox, stage)


def test_an_oversized_memory_file_is_rejected(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "big.md").write_text(
        "x" * (MAX_MEMORY_FILE_BYTES + 1), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="over the .* byte limit"):
        copy_outbox_memory_to_stage(outbox, stage)


def test_too_many_memory_entries_are_rejected(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    for index in range(MAX_MEMORY_ENTRIES + 1):
        (outbox.memory / f"e{index:03d}.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="entries"):
        copy_outbox_memory_to_stage(outbox, stage)


def test_uppercase_suffix_memory_files_are_ignored(launch, tmp_path: Path):
    """The memory store matches `.md` case-sensitively; so must the outbox."""
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "notes.MD").write_text("shouted", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert not (stage / "notes.MD").exists()


# ---------------------------------------------------------------------------
# End-to-end: the launch-view memory wire reaches canonical storage
# ---------------------------------------------------------------------------


def _memory_editing_integration(seen: dict, edit):
    class _Integration:
        supports_execution = True
        name = "fake"

        def run(self, request):
            seen["memory_working_dir"] = request.memory_working_dir
            seen["launch_dir"] = request.launch_dir
            seen["writable_roots"] = request.runtime_policy.writable_roots
            # Reach the directory through the launch view rather than the
            # request field, so a wire pointing elsewhere fails this test.
            edit(request.launch_dir / ".agency" / "memory")
            return RunResult(0, "done", "", 0.1)

    return _Integration()


def _run_memory_job(tmp_path, monkeypatch, edit):
    fixture = MemoryJobFixture(tmp_path)
    seen: dict = {"revision_before": fixture.store.read(fixture.resolved).revision}
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=fixture.group_root,
            integration=_memory_editing_integration(seen, edit),
            timeout=30,
            sandbox_root=None,
            group_root=fixture.group_root,
        ),
    )
    return fixture, seen, execute_job(fixture.authority)


def test_memory_written_into_the_launch_view_reaches_canonical_storage(
    tmp_path, monkeypatch
):
    def edit(memory_dir: Path) -> None:
        assert (memory_dir / "memory.md").read_bytes() == b"old"
        (memory_dir / "memory.md").write_bytes(b"new")
        (memory_dir / "decisions.md").write_bytes(b"added")

    fixture, seen, result = _run_memory_job(tmp_path, monkeypatch, edit)

    assert result.status == "complete"
    assert fixture.store.read(fixture.resolved).files == {
        "memory.md": b"new",
        "decisions.md": b"added",
    }
    assert seen["memory_working_dir"] == seen["launch_dir"] / ".agency" / "memory"


def test_untouched_launch_view_memory_publishes_no_change(tmp_path, monkeypatch):
    fixture, seen, result = _run_memory_job(tmp_path, monkeypatch, lambda memory_dir: None)

    assert result.status == "complete"
    stored = fixture.store.read(fixture.resolved)
    assert stored.files == {"memory.md": b"old"}
    assert stored.revision == seen["revision_before"]


def test_unsafe_launch_view_memory_fails_with_a_structured_report(tmp_path, monkeypatch):
    def edit(memory_dir: Path) -> None:
        (memory_dir / "nested").mkdir()

    fixture, _, result = _run_memory_job(tmp_path, monkeypatch, edit)

    assert result.status == "failed"
    summary = result.execution_summary or ""
    assert summary.startswith("Memory publication failed:")
    assert "subdirector" in summary
    assert (result.memory_publication or {}).get("failed_artifacts")
    assert fixture.store.read(fixture.resolved).files == {"memory.md": b"old"}


def test_the_launch_view_is_never_a_writable_root(tmp_path, monkeypatch):
    """Its writability is implicit and travels on the run request, not the policy."""
    _, seen, result = _run_memory_job(tmp_path, monkeypatch, lambda memory_dir: None)

    assert result.status == "complete"
    launch_dir = seen["launch_dir"].resolve()
    for root in seen["writable_roots"]:
        resolved_root = Path(root).resolve()
        assert resolved_root != launch_dir
        assert resolved_root not in launch_dir.parents
