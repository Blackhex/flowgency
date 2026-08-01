from __future__ import annotations

from pathlib import Path

import pytest

from agency.records.outbox import copy_outbox_memory_to_stage, create_outbox


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
