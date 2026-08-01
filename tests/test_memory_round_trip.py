from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agency.records.outbox import _is_plain_regular_file, copy_outbox_memory_to_stage, create_outbox


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
