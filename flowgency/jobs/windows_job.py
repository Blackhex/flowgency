"""A single spawned Windows helper process tree, contained by a Job Object before it can run.

Unlike the durable headless runner in `processes.py`, a `WindowsJobOwner` does not run or
supervise the helper to completion; it only proves the helper (and anything it spawns) is
assigned to a kill-on-close Job before its suspended thread is ever resumed, and later proves
the whole tree is gone.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from flowgency.jobs.processes import _job_exit_status, create_kill_on_close_windows_job


class WindowsJobLaunchError(RuntimeError):
    """Raised when a job-contained helper process could not be launched or confirmed stopped."""

    def __init__(
        self,
        message: str,
        *,
        cleanup_confirmed: bool,
        process_terminated: bool | None = None,
        job_empty_confirmed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed
        self.process_terminated = process_terminated
        self.job_empty_confirmed = job_empty_confirmed


@dataclass
class _UncertainWindowsJob:
    job_handle: object
    process_handle: object
    pid: int


# Job/process handles whose emptiness after a failed assignment could not be confirmed; a new
# launch refuses to proceed until every entry here resolves, mirroring the POSIX connected
# adapter's `_unconfirmed` registry in `connected_process.py`.
_uncertain_lock = threading.Lock()
_uncertain: list[_UncertainWindowsJob] = []


def _close_uncertain(entry: _UncertainWindowsJob) -> None:
    import win32api

    with contextlib.suppress(Exception):
        win32api.CloseHandle(entry.process_handle)
    with contextlib.suppress(Exception):
        win32api.CloseHandle(entry.job_handle)
    with _uncertain_lock:
        if entry in _uncertain:
            _uncertain.remove(entry)


def _process_terminated(process_handle, deadline: float) -> bool:
    import pywintypes
    import win32con
    import win32event
    import win32process

    timeout_ms = max(0, int((deadline - time.monotonic()) * 1000))
    try:
        wait_status = win32event.WaitForSingleObject(process_handle, timeout_ms)
    except pywintypes.error:
        return False
    if wait_status == getattr(win32event, "WAIT_OBJECT_0", 0):
        try:
            return win32process.GetExitCodeProcess(process_handle) != win32con.STILL_ACTIVE
        except pywintypes.error:
            return False
    if wait_status == getattr(win32event, "WAIT_TIMEOUT", 258):
        return False
    return False


def _retry_uncertain(deadline: float) -> None:
    with _uncertain_lock:
        pending = list(_uncertain)
    for entry in pending:
        process_terminated = _process_terminated(entry.process_handle, deadline)
        if process_terminated and _job_exit_status(entry.job_handle, deadline) == "empty":
            _close_uncertain(entry)
    with _uncertain_lock:
        remaining = len(_uncertain)
    if remaining:
        raise WindowsJobLaunchError(
            f"{remaining} earlier windows job(s) could not be confirmed stopped",
            cleanup_confirmed=False,
        )


def _fail_before_resume(job_handle, process_handle, pid: int, cause: Exception) -> None:
    import win32api
    import win32process

    deadline = time.monotonic() + 5
    with contextlib.suppress(Exception):
        win32process.TerminateProcess(process_handle, 1)
    process_terminated = _process_terminated(process_handle, deadline)
    status = _job_exit_status(job_handle, deadline)
    if process_terminated and status == "empty":
        with contextlib.suppress(Exception):
            win32api.CloseHandle(process_handle)
        with contextlib.suppress(Exception):
            win32api.CloseHandle(job_handle)
        raise WindowsJobLaunchError(
            f"Assigning helper process {pid} to its job failed: {cause}",
            cleanup_confirmed=True,
            process_terminated=True,
            job_empty_confirmed=True,
        ) from cause
    with _uncertain_lock:
        _uncertain.append(_UncertainWindowsJob(job_handle, process_handle, pid))
    raise WindowsJobLaunchError(
        f"Assigning helper process {pid} to its job failed and its job could not be "
        f"confirmed stopped: {cause}",
        cleanup_confirmed=False,
        process_terminated=process_terminated,
        job_empty_confirmed=(status == "empty"),
    ) from cause


class WindowsJobOwner:
    """Owns the non-inheritable Job handle and process handle for one contained helper tree."""

    def __init__(self, job_handle, process_handle, pid: int) -> None:
        self._job_handle = job_handle
        self._process_handle = process_handle
        self.pid = pid
        self._confirmed_empty = False
        self._closed = False

    @classmethod
    def launch(
        cls,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        stdin_handle: int,
        stdout_handle: int,
    ) -> "WindowsJobOwner":
        import pywintypes
        import win32api
        import win32process

        _retry_uncertain(time.monotonic())

        job_handle = create_kill_on_close_windows_job()
        process_handle = None
        thread_handle = None
        try:
            startup = win32process.STARTUPINFO()
            startup.dwFlags |= win32process.STARTF_USESTDHANDLES
            startup.hStdInput = int(stdin_handle)
            startup.hStdOutput = int(stdout_handle)
            startup.hStdError = int(stdout_handle)
            creationflags = win32process.CREATE_SUSPENDED | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process_handle, thread_handle, pid, _ = win32process.CreateProcess(
                    None,
                    subprocess.list2cmdline([str(item) for item in argv]),
                    None,
                    None,
                    True,
                    creationflags,
                    env,
                    str(cwd),
                    startup,
                )
            except pywintypes.error as error:
                with contextlib.suppress(Exception):
                    win32api.CloseHandle(job_handle)
                raise WindowsJobLaunchError(
                    f"Could not start {argv[0]}: {error}",
                    cleanup_confirmed=True,
                ) from error

            try:
                import win32job

                win32job.AssignProcessToJobObject(job_handle, process_handle)
            except pywintypes.error as error:
                _fail_before_resume(job_handle, process_handle, pid, error)

            try:
                win32process.ResumeThread(thread_handle)
            except Exception as error:
                _fail_before_resume(job_handle, process_handle, pid, error)
        finally:
            if thread_handle is not None:
                with contextlib.suppress(Exception):
                    win32api.CloseHandle(thread_handle)
        return cls(job_handle, process_handle, pid)

    def contains(self, pid: int) -> bool:
        import pywintypes
        import win32api
        import win32con
        import win32job

        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
        except pywintypes.error:
            return False
        try:
            return bool(win32job.IsProcessInJob(handle, self._job_handle))
        finally:
            win32api.CloseHandle(handle)

    def stop(self, deadline: float) -> tuple[bool, str]:
        import win32job

        if self._confirmed_empty:
            return True, "stopped"
        with contextlib.suppress(Exception):
            win32job.TerminateJobObject(self._job_handle, 1)
        status = _job_exit_status(self._job_handle, deadline)
        if status == "empty":
            self._confirmed_empty = True
            return True, "stopped"
        if status == "unknown":
            return False, "job-accounting-unavailable"
        return False, "job-active-processes"

    def alive(self) -> bool:
        return _job_exit_status(self._job_handle, time.monotonic()) != "empty"

    def exit_code(self) -> int | None:
        import win32con
        import win32process

        code = win32process.GetExitCodeProcess(self._process_handle)
        return None if code == win32con.STILL_ACTIVE else int(code)

    def close_confirmed(self) -> None:
        if not self._confirmed_empty:
            raise WindowsJobLaunchError(
                "close_confirmed called before Stop proved the job empty",
                cleanup_confirmed=False,
            )
        if self._closed:
            return
        import win32api

        with contextlib.suppress(Exception):
            win32api.CloseHandle(self._process_handle)
        with contextlib.suppress(Exception):
            win32api.CloseHandle(self._job_handle)
        self._closed = True
