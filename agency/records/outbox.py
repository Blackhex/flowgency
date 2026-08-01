"""Per-job outbox that agents write records and memory into."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agency.fs.atomic import atomic_write_bytes

OUTBOX_RELATIVE_OBSERVATIONS = ".agency/outbox/observations"
OUTBOX_RELATIVE_PROPOSALS = ".agency/outbox/proposals"
OUTBOX_RELATIVE_MEMORY = ".agency/memory"

_AGENCY_DIRNAME = ".agency"


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
        if entry.is_dir():
            raise ValueError(
                f"memory directory must not contain subdirectories: {entry.name}"
            )
        if entry.suffix.casefold() != ".md":
            continue
        produced[entry.name] = entry.read_bytes()

    for name, payload in produced.items():
        atomic_write_bytes(stage_directory / name, payload)

    for entry in list(stage_directory.iterdir()):
        if entry.is_file() and entry.name not in produced:
            entry.unlink()
