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
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from flowgency.configuration.paths import is_symlink_or_reparse
from flowgency.configuration.store import ConfigSnapshot, ConfigStore
from flowgency.fs.locks import exclusive_lock
from flowgency.jobs.store import revision_bound_team_operation
from flowgency.workflows.models import ContractError

_BLUEPRINT_LOCK_DIR = ".locks"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def blueprint_lock_paths(
    workflow_library: Path | None, blueprint_ids: tuple[str, ...]
) -> tuple[Path, ...]:
    """Return canonical, de-duplicated blueprint lock paths in stable order.

    Each id must be a permitted slug whose lock leaf stays inside the library's
    ``.locks`` directory. The library root and lock directory are checked for
    link/reparse metadata, and a missing configured library is rejected rather
    than being created as a side effect of acquiring a lock.
    """
    if workflow_library is None or not blueprint_ids:
        return ()
    library_root = Path(workflow_library)
    if not library_root.exists():
        raise ContractError(
            "missing-library", "Configured workflow library does not exist"
        )
    if is_symlink_or_reparse(library_root):
        raise ContractError(
            "unsafe-library", "Workflow library root crosses a link or reparse point"
        )
    lock_dir = library_root / _BLUEPRINT_LOCK_DIR
    if is_symlink_or_reparse(lock_dir):
        raise ContractError(
            "unsafe-library", "Blueprint lock directory crosses a link or reparse point"
        )
    resolved_lock_dir = lock_dir.resolve(strict=False)
    unique: dict[str, Path] = {}
    for blueprint_id in blueprint_ids:
        if not _SLUG.match(blueprint_id):
            raise ContractError(
                "unsafe-blueprint",
                f"Blueprint id is not a permitted slug: {blueprint_id!r}",
            )
        leaf = lock_dir / f"{blueprint_id}.lock"
        resolved = leaf.resolve(strict=False)
        try:
            resolved.relative_to(resolved_lock_dir)
        except ValueError as error:
            raise ContractError(
                "unsafe-blueprint", "Blueprint lock path escapes the library"
            ) from error
        if is_symlink_or_reparse(leaf):
            raise ContractError(
                "unsafe-blueprint", "Blueprint lock leaf crosses a link or reparse point"
            )
        unique[os.path.normcase(str(resolved))] = leaf
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
