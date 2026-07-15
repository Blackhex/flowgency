from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from agency.blueprints.cache import CompiledArtifact


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _overlaps(path: Path, other: Path) -> bool:
    return _is_relative_to(path, other) or _is_relative_to(other, path)


def _validate_destination(artifact: CompiledArtifact, destination: Path) -> Path:
    resolved_destination = Path(destination).resolve()
    overlap_roots = (
        artifact.entry_path.resolve(),
        artifact.runtime_path.resolve(),
    )
    if any(_overlaps(resolved_destination, root) for root in overlap_roots):
        raise ValueError(
            "Launch view destination must not overlap the cache artifact"
        )
    return resolved_destination


def create_launch_view(artifact: CompiledArtifact, destination: Path) -> Path:
    destination = _validate_destination(artifact, destination)
    runtime = artifact.runtime_path.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    for directory, _, _ in os.walk(runtime):
        current = Path(directory)
        current_stat = current.stat(follow_symlinks=False)
        if _is_reparse_point(current_stat):
            raise ValueError(
                f"Cached runtime contains a reparse point: {current}"
            )
        relative_dir = current.relative_to(runtime)
        target_dir = destination / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for entry in sorted(
            os.scandir(current),
            key=lambda item: item.name.casefold(),
        ):
            entry_path = Path(entry.path)
            entry_stat = entry.stat(follow_symlinks=False)
            if _is_reparse_point(entry_stat):
                raise ValueError(
                    "Cached runtime contains a symlink or reparse point: "
                    f"{entry_path}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise ValueError(
                    "Cached runtime contains a non-regular file: "
                    f"{entry_path}"
                )
            target = target_dir / entry.name
            shutil.copy2(entry_path, target)
    return destination
