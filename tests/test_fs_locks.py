from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest

from flowgency.fs.locks import ResourceBusyError, exclusive_lock, try_exclusive_lock


def _hold_lock(path: str, acquired: Event, release: Event) -> None:
    with exclusive_lock(Path(path), wait=True):
        acquired.set()
        release.wait(5)


@pytest.mark.parametrize("parent_exists", [False, True])
def test_exclusive_lock_can_skip_parent_creation(tmp_path, parent_exists):
    lock_path = tmp_path / "missing" / "nested" / "x.lock"
    if parent_exists:
        lock_path.parent.mkdir(parents=True)
        with exclusive_lock(lock_path, wait=True, create_parent=False):
            assert lock_path.is_file()
    else:
        with pytest.raises(FileNotFoundError):
            with exclusive_lock(lock_path, wait=True, create_parent=False):
                pass
        assert list(tmp_path.rglob("*")) == []


def test_exclusive_lock_preserves_default_parent_creation(tmp_path):
    lock_path = tmp_path / "missing" / "nested" / "x.lock"

    with exclusive_lock(lock_path, wait=True):
        assert lock_path.is_file()


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