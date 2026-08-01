"""Per-job outbox that agents write records and memory into."""

from __future__ import annotations

import itertools
import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agency.fs.atomic import atomic_write_bytes
from agency.permissions.zones import ZONE_MEMORY, ZONE_OUTBOX

OUTBOX_RELATIVE_OBSERVATIONS = f"{ZONE_OUTBOX}/observations"
OUTBOX_RELATIVE_PROPOSALS = f"{ZONE_OUTBOX}/proposals"
OUTBOX_RELATIVE_MEMORY = ZONE_MEMORY

# Memory lands in canonical storage and is read by the worker, so it carries the
# same exposure as records and is bounded the same way.
MAX_MEMORY_FILES = 20
MAX_MEMORY_FILE_BYTES = 65536
MAX_MEMORY_ENTRIES = 100

_AGENCY_DIRNAME = ".agency"


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    """Check if a file is a symlink or reparse point."""
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _is_plain_regular_file(entry_stat: os.stat_result) -> bool:
    """Return True only for plain regular files (not symlinks, not reparse points)."""
    return stat.S_ISREG(entry_stat.st_mode) and not _is_reparse_point(entry_stat)


@dataclass(frozen=True)
class OutboxPaths:
    root: Path
    observations: Path
    proposals: Path
    memory: Path


def create_outbox(
    launch_view: Path,
    *,
    memory_files: Mapping[str, bytes],
) -> OutboxPaths:
    launch_view = Path(launch_view)
    if not launch_view.is_dir():
        raise ValueError(f"launch view does not exist: {launch_view}")

    for name in memory_files:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError(f"invalid memory file name: {name!r}")

    root = launch_view / _AGENCY_DIRNAME
    if root.exists():
        shutil.rmtree(root)

    paths = OutboxPaths(
        root=root,
        observations=launch_view.joinpath(*OUTBOX_RELATIVE_OBSERVATIONS.split("/")),
        proposals=launch_view.joinpath(*OUTBOX_RELATIVE_PROPOSALS.split("/")),
        memory=launch_view.joinpath(*OUTBOX_RELATIVE_MEMORY.split("/")),
    )
    for directory in (paths.observations, paths.proposals, paths.memory):
        directory.mkdir(parents=True, exist_ok=True)

    for name, payload in memory_files.items():
        atomic_write_bytes(paths.memory / name, payload)

    return paths


def copy_outbox_memory_to_stage(outbox: OutboxPaths, stage_directory: Path) -> None:
    """Mirror the agent-visible memory directory onto the publication stage.

    Bounded the same way records are, and streamed one file at a time, so a
    looping agent cannot exhaust the worker through its memory directory.
    """
    stage_directory = Path(stage_directory)
    stage_directory.mkdir(parents=True, exist_ok=True)

    scanned = list(
        itertools.islice(outbox.memory.iterdir(), MAX_MEMORY_ENTRIES + 1)
    )
    if len(scanned) > MAX_MEMORY_ENTRIES:
        raise ValueError(
            f"memory directory holds more than {MAX_MEMORY_ENTRIES} entries"
        )

    produced: set[str] = set()
    for entry in sorted(scanned, key=lambda item: item.name.casefold()):
        entry_stat = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            raise ValueError(
                f"memory directory must not contain subdirectories: {entry.name}"
            )
        if not _is_plain_regular_file(entry_stat):
            raise ValueError(
                f"memory directory must not contain symlinks or reparse points: {entry.name}"
            )
        if entry.suffix != ".md":
            continue
        if len(produced) >= MAX_MEMORY_FILES:
            raise ValueError(
                f"memory directory holds more than {MAX_MEMORY_FILES} markdown files"
            )
        if entry_stat.st_size > MAX_MEMORY_FILE_BYTES:
            raise ValueError(
                f"memory file {entry.name} is {entry_stat.st_size} bytes, "
                f"over the {MAX_MEMORY_FILE_BYTES} byte limit"
            )
        produced.add(entry.name)
        payload = entry.read_bytes()
        stage_file = stage_directory / entry.name
        existing = stage_file.read_bytes() if stage_file.exists() else None
        if existing != payload:
            atomic_write_bytes(stage_file, payload)

    for entry in list(stage_directory.iterdir()):
        if entry.is_file():
            if entry.name not in produced:
                entry.unlink()
        elif entry.is_dir():
            raise ValueError(
                f"stage directory must not contain subdirectories: {entry.name}"
            )
