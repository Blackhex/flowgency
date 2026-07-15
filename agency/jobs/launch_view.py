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


def create_launch_view(artifact: CompiledArtifact, destination: Path) -> Path:
    destination = Path(destination).resolve()
    runtime = artifact.runtime_path.resolve()
    if destination == runtime:
        raise ValueError(
            "Launch view destination must not be the cache runtime path"
        )
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
