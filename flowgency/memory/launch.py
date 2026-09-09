from __future__ import annotations

import itertools
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from flowgency.fs.atomic import atomic_write_bytes
from flowgency.memory.limits import (
    MAX_MEMORY_ENTRIES,
    MAX_MEMORY_FILE_BYTES,
    MAX_MEMORY_FILES,
)
from flowgency.permissions.zones import ZONE_MEMORY


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        stat_result = Path(path).lstat()
    except FileNotFoundError:
        return False
    return bool(stat.S_ISLNK(stat_result.st_mode) or _is_reparse_point(stat_result))


def _is_plain_regular_file(entry_stat: os.stat_result) -> bool:
    return stat.S_ISREG(entry_stat.st_mode) and not _is_reparse_point(entry_stat)


@dataclass(frozen=True)
class LaunchMemory:
    root: Path
    memory: Path


def require_memory_name_and_size(name: str, payload: bytes) -> None:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError(f"invalid memory file name: {name!r}")
    if len(payload) > MAX_MEMORY_FILE_BYTES:
        raise ValueError(
            f"memory file {name} is {len(payload)} bytes, over the {MAX_MEMORY_FILE_BYTES} byte limit"
        )


def require_safe_memory_directory(memory: Path) -> None:
    memory = Path(memory)
    root = memory.parent
    if root.name != ".flowgency":
        raise ValueError(f"memory directory must live under .flowgency: {memory}")
    if not memory.parent.parent.is_dir():
        raise ValueError(f"launch view does not exist: {memory.parent.parent}")
    for candidate, label in ((root, ".flowgency root"), (memory, "memory directory")):
        if candidate.exists():
            if _is_symlink_or_reparse(candidate):
                raise ValueError(f"{label} must be a real directory: {candidate}")
            if not candidate.is_dir():
                raise ValueError(f"{label} must be a directory: {candidate}")
    root.mkdir(parents=True, exist_ok=True)
    memory.mkdir(parents=True, exist_ok=True)
    if _is_symlink_or_reparse(root) or _is_symlink_or_reparse(memory):
        raise ValueError(f"memory directory must be a real directory: {memory}")


def _reset_memory_directory(memory: Path) -> None:
    if not memory.exists():
        return
    scanned = list(itertools.islice(memory.iterdir(), MAX_MEMORY_ENTRIES + 1))
    if len(scanned) > MAX_MEMORY_ENTRIES:
        raise ValueError(
            f"memory directory holds more than {MAX_MEMORY_ENTRIES} entries"
        )
    for entry in scanned:
        entry_stat = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            raise ValueError(
                f"memory directory must not contain subdirectories: {entry.name}"
            )
        if not _is_plain_regular_file(entry_stat):
            raise ValueError(
                f"memory directory must not contain symlinks or reparse points: {entry.name}"
            )
        entry.unlink()


def prepare_launch_memory(
    launch_view: Path,
    *,
    memory_files: Mapping[str, bytes],
) -> LaunchMemory:
    launch_view = Path(launch_view)
    if not launch_view.is_dir():
        raise ValueError(f"launch view does not exist: {launch_view}")
    if len(memory_files) > MAX_MEMORY_FILES:
        raise ValueError(
            f"memory directory holds more than {MAX_MEMORY_FILES} markdown files"
        )

    memory = launch_view.joinpath(*ZONE_MEMORY.split("/"))
    require_safe_memory_directory(memory)
    _reset_memory_directory(memory)
    for name, payload in memory_files.items():
        require_memory_name_and_size(name, payload)
        atomic_write_bytes(memory / name, payload)
    return LaunchMemory(root=launch_view, memory=memory)


def copy_launch_memory_to_stage(launch: LaunchMemory, stage_directory: Path) -> None:
    stage_directory = Path(stage_directory)
    stage_directory.mkdir(parents=True, exist_ok=True)

    scanned = list(
        itertools.islice(launch.memory.iterdir(), MAX_MEMORY_ENTRIES + 1)
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
                f"memory file {entry.name} is {entry_stat.st_size} bytes, over the {MAX_MEMORY_FILE_BYTES} byte limit"
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