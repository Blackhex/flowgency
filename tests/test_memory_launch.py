from __future__ import annotations

import os
from pathlib import Path

import pytest

from flowgency.memory.launch import (
    MAX_MEMORY_ENTRIES,
    MAX_MEMORY_FILE_BYTES,
    MAX_MEMORY_FILES,
    copy_launch_memory_to_stage,
    prepare_launch_memory,
)


@pytest.fixture
def launch_view(tmp_path: Path) -> Path:
    path = tmp_path / "launch"
    path.mkdir()
    return path


def test_prepare_launch_memory_creates_memory_directory_and_seeds_files(launch_view: Path):
    launch = prepare_launch_memory(
        launch_view,
        memory_files={"memory.md": b"prior knowledge"},
    )

    assert launch.root == launch_view
    assert launch.memory == launch_view / ".flowgency" / "memory"
    assert launch.memory.is_dir()
    assert (launch.memory / "memory.md").read_bytes() == b"prior knowledge"


def test_prepare_launch_memory_rejects_a_missing_launch_view(tmp_path: Path):
    with pytest.raises(ValueError, match="launch view"):
        prepare_launch_memory(tmp_path / "missing", memory_files={})


def test_prepare_launch_memory_rejects_memory_file_names_with_separators(launch_view: Path):
    with pytest.raises(ValueError, match="memory file name"):
        prepare_launch_memory(launch_view, memory_files={"../escape.md": b"x"})


def test_prepare_launch_memory_preserves_existing_flowgency_siblings(launch_view: Path):
    descriptor = launch_view / ".flowgency" / "ticket-access.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")

    launch = prepare_launch_memory(launch_view, memory_files={"memory.md": b"new"})

    assert descriptor.read_text(encoding="utf-8") == "{}"
    assert (launch.memory / "memory.md").read_bytes() == b"new"


def test_copy_launch_memory_to_stage_replaces_stage_contents(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={"memory.md": b"canonical"})
    (launch.memory / "memory.md").write_text("edited", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")

    copy_launch_memory_to_stage(launch, stage)

    assert (stage / "memory.md").read_text(encoding="utf-8") == "edited"


def test_copy_launch_memory_to_stage_removes_deleted_files(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={"memory.md": b"canonical"})
    (launch.memory / "memory.md").unlink()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")

    copy_launch_memory_to_stage(launch, stage)

    assert not (stage / "memory.md").exists()


def test_copy_launch_memory_to_stage_ignores_non_markdown_files(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={})
    (launch.memory / "scratch.txt").write_text("junk", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()

    copy_launch_memory_to_stage(launch, stage)

    assert not (stage / "scratch.txt").exists()


def test_copy_launch_memory_to_stage_rejects_subdirectories(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={})
    (launch.memory / "nested").mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(ValueError, match="subdirector"):
        copy_launch_memory_to_stage(launch, stage)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_copy_launch_memory_to_stage_rejects_symlinked_markdown_files(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={})
    target = tmp_path / "outside.md"
    target.write_text("secret", encoding="utf-8")
    (launch.memory / "link.md").symlink_to(target)
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(ValueError, match="symlink|reparse"):
        copy_launch_memory_to_stage(launch, stage)


def test_copy_launch_memory_to_stage_rejects_too_many_markdown_files(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={})
    for index in range(MAX_MEMORY_FILES + 1):
        (launch.memory / f"m{index:02d}.md").write_text("x", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(ValueError, match="markdown files"):
        copy_launch_memory_to_stage(launch, stage)


def test_copy_launch_memory_to_stage_rejects_oversized_files(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={})
    (launch.memory / "big.md").write_text(
        "x" * (MAX_MEMORY_FILE_BYTES + 1),
        encoding="utf-8",
    )
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(ValueError, match="over the .* byte limit"):
        copy_launch_memory_to_stage(launch, stage)


def test_copy_launch_memory_to_stage_rejects_too_many_entries(launch_view: Path, tmp_path: Path):
    launch = prepare_launch_memory(launch_view, memory_files={})
    for index in range(MAX_MEMORY_ENTRIES + 1):
        (launch.memory / f"e{index:03d}.txt").write_text("x", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises(ValueError, match="entries"):
        copy_launch_memory_to_stage(launch, stage)