"""Cross-process exclusive lock helpers."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, ContextManager, Iterator

import portalocker


class ResourceBusyError(RuntimeError):
    pass


class LockCancelledError(RuntimeError):
    pass


_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _canonical_lock_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def _local_lock_for(path: Path) -> threading.Lock:
    key = _canonical_lock_key(path)
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCAL_LOCKS[key] = lock
        return lock


def _acquire_portalocker(lock_file) -> None:
    portalocker.lock(lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)


@contextmanager
def exclusive_lock(
    path: Path,
    *,
    wait: bool,
    timeout: float | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    local_lock = _local_lock_for(path)
    deadline = None if timeout is None else time.monotonic() + timeout
    poll_interval = 0.05
    lock_file = path.open("a+b")
    acquired_local = False
    acquired_remote = False
    try:
        while True:
            if cancelled is not None and cancelled():
                raise LockCancelledError(f"Lock acquisition cancelled for {path}")
            acquired_local = local_lock.acquire(blocking=False)
            if not acquired_local:
                if not wait:
                    raise ResourceBusyError(f"Lock is busy: {path}")
            else:
                try:
                    _acquire_portalocker(lock_file)
                except portalocker.exceptions.LockException as error:
                    local_lock.release()
                    acquired_local = False
                    if not wait:
                        raise ResourceBusyError(f"Lock is busy: {path}") from error
                else:
                    acquired_remote = True
                    break
            if not wait:
                raise ResourceBusyError(f"Lock is busy: {path}")
            if deadline is not None and time.monotonic() >= deadline:
                raise ResourceBusyError(f"Lock acquisition timed out for {path}")
            if cancelled is not None and cancelled():
                raise LockCancelledError(f"Lock acquisition cancelled for {path}")
            time.sleep(poll_interval)
        yield
    finally:
        if acquired_remote:
            try:
                portalocker.unlock(lock_file)
            except portalocker.exceptions.LockException:
                pass
        lock_file.close()
        if acquired_local:
            local_lock.release()


def try_exclusive_lock(path: Path) -> ContextManager[None]:
    return exclusive_lock(path, wait=False)