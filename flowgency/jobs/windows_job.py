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
from dataclasses import dataclass, field
from pathlib import Path

from flowgency.jobs.processes import (
    ProcessStopEvidence,
    RuntimeProcessLifecycle,
    _job_exit_status,
    create_kill_on_close_windows_job,
)
from flowgency.jobs.windows_conpty import (
    WindowsConPTYCreateProcessError,
    create_suspended_conpty_process,
)


class WindowsJobLaunchError(RuntimeError):
    """Raised when a job-contained helper process could not be launched or confirmed stopped."""

    def __init__(
        self,
        message: str,
        *,
        cleanup_confirmed: bool,
        process_terminated: bool | None = None,
        job_empty_confirmed: bool | None = None,
        _cleanup_owners: tuple[_UncertainWindowsJob, ...] = (),
    ) -> None:
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed
        self.process_terminated = process_terminated
        self.job_empty_confirmed = job_empty_confirmed
        self._cleanup_owners = _cleanup_owners
        self._cleanup = _WindowsJobLaunchCleanup(_cleanup_owners) if _cleanup_owners else None


@dataclass(eq=False)
class _UncertainWindowsJob:
    job_handle: object
    process_handle: object | None
    pid: int
    thread_handle: object | None = None
    process_terminated: bool = False
    job_empty_confirmed: bool = False
    _confirmed_empty: bool = False
    _process_closed: bool = False
    _job_closed: bool = False
    _thread_closed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def stop(self, deadline: float) -> tuple[bool, str]:
        import win32job
        import win32process

        with self._lock:
            if self._confirmed_empty:
                return True, "stopped"
            if self.process_handle is not None:
                with contextlib.suppress(Exception):
                    win32process.TerminateProcess(self.process_handle, 1)
            with contextlib.suppress(Exception):
                win32job.TerminateJobObject(self.job_handle, 1)
            self.process_terminated = self.process_handle is None or _process_terminated(self.process_handle, deadline)
            status = _job_exit_status(self.job_handle, deadline)
            self.job_empty_confirmed = status == "empty"
            if self.process_terminated and self.job_empty_confirmed:
                self._confirmed_empty = True
                return True, "stopped"
            if not self.process_terminated:
                return False, "process-termination-unconfirmed"
            return False, "job-accounting-unavailable" if status == "unknown" else "job-active-processes"

    def close_confirmed(self) -> None:
        import win32api

        with self._lock:
            if not self._confirmed_empty:
                raise WindowsJobLaunchError("Failed launch cleanup is unconfirmed", cleanup_confirmed=False)
            if not self._process_closed:
                if self.process_handle is not None:
                    win32api.CloseHandle(self.process_handle)
                self._process_closed = True
            if not self._thread_closed:
                if self.thread_handle is not None:
                    win32api.CloseHandle(self.thread_handle)
                self._thread_closed = True
            if not self._job_closed:
                win32api.CloseHandle(self.job_handle)
                self._job_closed = True
            with _uncertain_lock:
                if self in _uncertain:
                    _uncertain.remove(self)


class _WindowsJobLaunchCleanup:
    def __init__(self, owners: tuple[_UncertainWindowsJob, ...]) -> None:
        self._owners = owners
        self._error: Exception | None = None

    def stop(self, lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence:
        deadline = time.monotonic() + 5.0
        confirmed, reason = True, "stopped"
        for owner in self._owners:
            try:
                stopped, stop_reason = owner.stop(deadline)
                if stopped:
                    owner.close_confirmed()
            except Exception as error:
                self._error = error
                stopped, stop_reason = False, "job-cleanup-failed"
            if not stopped:
                confirmed, reason = False, stop_reason
        return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, confirmed, reason)


# Job/process handles whose emptiness after a failed assignment could not be confirmed; a new
# launch refuses to proceed until every entry here resolves, mirroring the POSIX connected
# adapter's `_unconfirmed` registry in `connected_process.py`.
_uncertain_lock = threading.Lock()
_uncertain: list[_UncertainWindowsJob] = []


def _close_uncertain(entry: _UncertainWindowsJob) -> None:
    entry.close_confirmed()


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
        confirmed, _reason = entry.stop(deadline)
        if confirmed:
            with contextlib.suppress(Exception):
                _close_uncertain(entry)
    with _uncertain_lock:
        remaining = tuple(_uncertain)
    if remaining:
        raise WindowsJobLaunchError(
            f"{len(remaining)} earlier windows job(s) could not be confirmed stopped",
            cleanup_confirmed=False,
            _cleanup_owners=remaining,
        )


def _fail_before_resume(
    job_handle, process_handle, pid: int, cause: Exception, *,
    message: str | None = None, thread_handle: object | None = None,
) -> None:
    deadline = time.monotonic() + 5
    failure = message or f"Assigning helper process {pid} to its job failed"
    entry = _UncertainWindowsJob(job_handle, process_handle, pid, thread_handle=thread_handle)
    with _uncertain_lock:
        _uncertain.append(entry)
    confirmed, _reason = entry.stop(deadline)
    if confirmed:
        try:
            _close_uncertain(entry)
        except Exception:
            confirmed = False
    if confirmed:
        raise WindowsJobLaunchError(
            f"{failure}: {cause}",
            cleanup_confirmed=True,
            process_terminated=True,
            job_empty_confirmed=True,
        ) from cause
    raise WindowsJobLaunchError(
        f"{failure} and its job could not be confirmed stopped: {cause}",
        cleanup_confirmed=False,
        process_terminated=entry.process_terminated,
        job_empty_confirmed=entry.job_empty_confirmed,
        _cleanup_owners=(entry,),
    ) from cause


class WindowsJobOwner:
    """Owns the non-inheritable Job handle and process handle for one contained helper tree."""

    def __init__(self, job_handle, process_handle, pid: int) -> None:
        self._job_handle = job_handle
        self._process_handle = process_handle
        self.pid = pid
        self._confirmed_empty = False
        self._closed = False
        self._process_closed = False
        self._job_closed = False
        self._thread_handle: object | None = None
        self._thread_closed = False
        self._cleanup_error: Exception | None = None

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

    @classmethod
    def launch_conpty(
        cls,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        pseudoconsole: int,
    ) -> "WindowsJobOwner":
        import win32api
        import win32process

        _retry_uncertain(time.monotonic())

        job_handle = create_kill_on_close_windows_job()
        process_handle = None
        thread_handle = None
        failure_owns_thread = False
        owner: WindowsJobOwner | None = None
        try:
            try:
                process_raw, thread_raw, pid, _thread_id = create_suspended_conpty_process(
                    argv,
                    cwd,
                    env,
                    pseudoconsole,
                    job_handle,
                )
                process_handle = process_raw
                thread_handle = thread_raw
            except WindowsConPTYCreateProcessError as error:
                process_handle = error.process_handle
                thread_handle = error.thread_handle
                failure_owns_thread = True
                _fail_before_resume(job_handle, process_handle, error.pid, error, thread_handle=thread_handle)
            except Exception as error:
                _fail_before_resume(job_handle, None, 0, error, message=f"Could not start {argv[0]}")

            try:
                owner = cls(job_handle, process_handle, pid)
                if not owner.contains(pid):
                    raise RuntimeError("created process was not in its assigned job before resume")
            except Exception as error:
                failure_owns_thread = True
                _fail_before_resume(job_handle, process_handle, pid, error, thread_handle=thread_handle)

            try:
                win32process.ResumeThread(thread_handle)
            except Exception as error:
                failure_owns_thread = True
                _fail_before_resume(job_handle, process_handle, pid, error, thread_handle=thread_handle)
        finally:
            if thread_handle is not None and not failure_owns_thread:
                try:
                    win32api.CloseHandle(thread_handle)
                except Exception as error:
                    if owner is None:
                        _fail_before_resume(job_handle, process_handle, pid, error, thread_handle=thread_handle)
                    owner._thread_handle = thread_handle
                    owner._cleanup_error = error
        assert owner is not None
        return owner

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

        if not self._process_closed:
            win32api.CloseHandle(self._process_handle)
            self._process_closed = True
        if not self._thread_closed:
            if self._thread_handle is not None:
                win32api.CloseHandle(self._thread_handle)
            self._thread_closed = True
        if not self._job_closed:
            win32api.CloseHandle(self._job_handle)
            self._job_closed = True
        self._closed = True
