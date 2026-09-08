"""Ordered locking for cooperating workflow writers.

``workflow_operation`` layers blueprint locks on top of the existing
revision-checked team-operation guard. The acquisition order is fixed – team
operation locks first (canonical order, via ``revision_bound_team_operation``),
then blueprint locks in canonical order – so cooperating writers never deadlock.
Configuration-file locks are taken only through :class:`ConfigStore` operations
and never recursively; this module holds neither the config lock nor any ticket
lock, and it never spans agent work.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from flowgency.configuration.store import ConfigSnapshot, ConfigStore
from flowgency.fs.locks import exclusive_lock
from flowgency.jobs.store import revision_bound_team_operation

_BLUEPRINT_LOCK_DIR = ".locks"


def blueprint_lock_paths(
    workflow_library: Path | None, blueprint_ids: tuple[str, ...]
) -> tuple[Path, ...]:
    """Return canonical, de-duplicated blueprint lock paths in stable order."""
    if workflow_library is None or not blueprint_ids:
        return ()
    base = Path(workflow_library) / _BLUEPRINT_LOCK_DIR
    unique: dict[str, Path] = {}
    for blueprint_id in blueprint_ids:
        path = (base / f"{blueprint_id}.lock").resolve(strict=False)
        unique[os.path.normcase(str(path))] = path
    return tuple(unique[key] for key in sorted(unique))


@contextmanager
def workflow_operation(
    store: ConfigStore,
    team_ids: tuple[str, ...],
    blueprint_ids: tuple[str, ...],
    *,
    expected_revision: str | None = None,
) -> Iterator[ConfigSnapshot]:
    """Yield the reloaded config snapshot under team and blueprint locks."""
    with revision_bound_team_operation(
        store, team_ids=team_ids, expected_revision=expected_revision
    ) as snapshot:
        lock_paths = blueprint_lock_paths(
            snapshot.config.flowgency.workflow_library, blueprint_ids
        )
        with ExitStack() as stack:
            for lock_path in lock_paths:
                stack.enter_context(exclusive_lock(lock_path, wait=True))
            yield snapshot
