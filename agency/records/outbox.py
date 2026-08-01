"""Per-job outbox that agents write records and memory into."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agency.fs.atomic import atomic_write_bytes

OUTBOX_RELATIVE_OBSERVATIONS = ".agency/outbox/observations"
OUTBOX_RELATIVE_PROPOSALS = ".agency/outbox/proposals"
OUTBOX_RELATIVE_MEMORY = ".agency/memory"

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
    """Mirror the agent-visible memory directory onto the publication stage."""
    stage_directory = Path(stage_directory)
    stage_directory.mkdir(parents=True, exist_ok=True)

    produced: dict[str, bytes] = {}
    for entry in sorted(outbox.memory.iterdir(), key=lambda item: item.name.casefold()):
        entry_stat = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            raise ValueError(
                f"memory directory must not contain subdirectories: {entry.name}"
            )
        if not _is_plain_regular_file(entry_stat):
            raise ValueError(
                f"memory directory must not contain symlinks or reparse points: {entry.name}"
            )
        if entry.suffix.casefold() != ".md":
            continue
        produced[entry.name] = entry.read_bytes()

    for name, payload in produced.items():
        stage_file = stage_directory / name
        existing = None
        if stage_file.exists():
            existing = stage_file.read_bytes()
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
