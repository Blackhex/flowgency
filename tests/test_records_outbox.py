from __future__ import annotations

from pathlib import Path

import pytest

from agency.records.outbox import (
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
    create_outbox,
)


def test_create_outbox_creates_all_directories(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()

    outbox = create_outbox(launch, memory_files={})

    assert outbox.observations.is_dir()
    assert outbox.proposals.is_dir()
    assert outbox.memory.is_dir()
    assert outbox.observations == launch.joinpath(*OUTBOX_RELATIVE_OBSERVATIONS.split("/"))
    assert outbox.proposals == launch.joinpath(*OUTBOX_RELATIVE_PROPOSALS.split("/"))
    assert outbox.memory == launch.joinpath(*OUTBOX_RELATIVE_MEMORY.split("/"))


def test_create_outbox_seeds_memory_files(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()

    outbox = create_outbox(launch, memory_files={"memory.md": b"prior knowledge"})

    assert (outbox.memory / "memory.md").read_bytes() == b"prior knowledge"


def test_create_outbox_replaces_a_previous_outbox(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()
    first = create_outbox(launch, memory_files={"memory.md": b"old"})
    (first.observations / "stale.md").write_text("stale", encoding="utf-8")

    second = create_outbox(launch, memory_files={"memory.md": b"new"})

    assert not (second.observations / "stale.md").exists()
    assert (second.memory / "memory.md").read_bytes() == b"new"


def test_create_outbox_rejects_a_missing_launch_view(tmp_path: Path):
    with pytest.raises(ValueError, match="launch view"):
        create_outbox(tmp_path / "nope", memory_files={})


def test_create_outbox_rejects_memory_file_names_with_separators(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()

    with pytest.raises(ValueError, match="memory file name"):
        create_outbox(launch, memory_files={"../escape.md": b"x"})
