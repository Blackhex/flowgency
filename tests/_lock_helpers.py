from __future__ import annotations

from pathlib import Path

from agency.fs.locks import exclusive_lock


def hold_exclusive_lock(lock_path: str, acquired, release, release_timeout: float) -> None:
    with exclusive_lock(Path(lock_path), wait=True):
        acquired.set()
        release.wait(release_timeout)