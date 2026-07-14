from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest

from agency.fs.locks import ResourceBusyError, exclusive_lock, try_exclusive_lock


def _hold_lock(path: str, acquired: Event, release: Event) -> None:
    with exclusive_lock(Path(path), wait=True):
        acquired.set()
        release.wait(5)


def test_try_lock_reports_busy_across_processes(tmp_path):
    acquired, release = Event(), Event()
    process = Process(target=_hold_lock, args=(str(tmp_path / "x.lock"), acquired, release))
    process.start()
    assert acquired.wait(5)
    with pytest.raises(ResourceBusyError):
        with try_exclusive_lock(tmp_path / "x.lock"):
            pass
    release.set()
    process.join(5)
    assert process.exitcode == 0