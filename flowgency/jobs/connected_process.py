"""Connected PTY processes whose whole process tree Flowgency owns and must prove stopped."""

from __future__ import annotations

import codecs
import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Protocol

from flowgency.integrations.errors import IntegrationError
from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.processes import (
    OwnedPosixProcessGroup,
    ProcessStopEvidence,
    RuntimeProcessLifecycle,
    _capture_posix_group_identity,
    _group_exit_status,
    _job_exit_status,
    _owned_posix_group_state,
    _parse_proc_stat_snapshot,
    terminate_owned_posix_group,
)


_STOP_TIMEOUT_SECONDS = 5.0
_MAX_TERMINAL_DIMENSION = 32767
_POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


class ConnectedLaunchError(IntegrationError):
    def __init__(self, message: str, *, cleanup_confirmed: bool):
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed


class ConnectedProcess(Protocol):
    pid: int

    def read(self, size: int = 65536) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def alive(self) -> bool: ...

    def exit_code(self) -> int | None: ...

    def stop(self, lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence: ...


# Trees whose stop could not be confirmed; they block new launches until a retry confirms them.
_unconfirmed_lock = threading.Lock()
_unconfirmed: list["_OwnedTerminal"] = []


def connected_process_available() -> bool:
    if os.name == "nt":
        try:
            import win32job  # noqa: F401
            import winpty

            probe = winpty.PTY(80, 24, backend=winpty.Backend.ConPTY)
        except Exception:
            return False
        del probe
        return True
    try:
        import ptyprocess  # noqa: F401
    except ImportError:
        return False
    return _posix_group_proof_available()


def start_connected_process(launch: RuntimeLaunch, *, rows: int = 24, cols: int = 80) -> ConnectedProcess:
    if launch.mode != "connected":
        raise ValueError("A connected PTY requires connected launch mode")
    _validate_size(rows, cols)
    _validate_launch(launch)
    _retry_unconfirmed()
    if os.name == "nt":
        return WindowsConnectedProcess.spawn(launch, rows=rows, cols=cols)
    return PosixConnectedProcess.spawn(launch, rows=rows, cols=cols)


def _validate_size(rows: int, cols: int) -> None:
    for value in (rows, cols):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_TERMINAL_DIMENSION:
            raise ValueError(f"Terminal size must be 1..{_MAX_TERMINAL_DIMENSION} rows and columns")


def _validate_launch(launch: RuntimeLaunch) -> None:
    if not launch.argv:
        raise ValueError("A connected launch needs a command")
    if any("\0" in str(item) for item in launch.argv):
        raise ConnectedLaunchError("Connected launch arguments must not contain NUL", cleanup_confirmed=True)
    for name, value in launch.env.items():
        if not name or "=" in name or "\0" in name or "\0" in value:
            raise ConnectedLaunchError(
                f"Connected launch environment has an invalid entry: {name!r}",
                cleanup_confirmed=True,
            )


def _retry_unconfirmed() -> None:
    with _unconfirmed_lock:
        pending = list(_unconfirmed)
    for process in pending:
        process._stop_tree()
    with _unconfirmed_lock:
        remaining = len(_unconfirmed)
    if remaining:
        raise ConnectedLaunchError(
            f"{remaining} earlier connected process tree(s) could not be confirmed stopped",
            cleanup_confirmed=False,
        )


def _track(process: "_OwnedTerminal", confirmed: bool) -> None:
    with _unconfirmed_lock:
        if confirmed:
            if process in _unconfirmed:
                _unconfirmed.remove(process)
        elif process not in _unconfirmed:
            _unconfirmed.append(process)


def _posix_group_proof_available() -> bool:
    try:
        text = Path("/proc/self/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return _parse_proc_stat_snapshot(text) is not None


class _OwnedTerminal:
    """Shared read/stop bookkeeping; platform classes own the tree and terminal handles."""

    _reads_cancellable = False

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._stop_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._io_closed = False
        self._ops_in_flight = 0
        self._pending_output = b""
        self._stopped = False

    def read(self, size: int = 65536) -> bytes:
        if size <= 0:
            raise ValueError("read size must be positive")
        with self._io_lock:
            if self._pending_output:
                chunk, self._pending_output = self._pending_output[:size], self._pending_output[size:]
                return chunk
            if self._io_closed:
                return b""
            self._ops_in_flight += 1
        try:
            data = self._read_terminal(size)
        finally:
            self._end_op()
        if len(data) <= size:
            return data
        with self._io_lock:
            self._pending_output += data[size:]
        return data[:size]

    def write(self, data: bytes) -> None:
        payload = bytes(data)
        self._begin_op()
        try:
            self._write_terminal(payload)
        finally:
            self._end_op()

    def resize(self, rows: int, cols: int) -> None:
        _validate_size(rows, cols)
        self._begin_op()
        try:
            self._resize_terminal(rows, cols)
        finally:
            self._end_op()

    def _begin_op(self) -> None:
        with self._io_lock:
            if self._io_closed:
                raise BrokenPipeError("The connected process has been stopped")
            self._ops_in_flight += 1

    def _end_op(self) -> None:
        with self._io_lock:
            self._ops_in_flight -= 1

    def stop(self, lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence:
        confirmed, reason = self._stop_tree()
        return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, confirmed, reason)

    def _stop_tree(self) -> tuple[bool, str]:
        with self._stop_lock:
            if self._stopped:
                return True, "stopped"
            deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
            confirmed, reason = self._terminate_tree(deadline)
            with self._io_lock:
                self._io_closed = True
            drained = self._release_reader(deadline if confirmed or self._reads_cancellable else time.monotonic())
            if confirmed:
                self._release(drained)
                self._stopped = True
        _track(self, confirmed)
        return confirmed, reason

    def _release_reader(self, deadline: float) -> bool:
        while True:
            self._cancel_reads()
            with self._io_lock:
                if self._ops_in_flight == 0:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def _read_terminal(self, size: int) -> bytes:
        raise NotImplementedError

    def _write_terminal(self, data: bytes) -> None:
        raise NotImplementedError

    def _resize_terminal(self, rows: int, cols: int) -> None:
        raise NotImplementedError

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        raise NotImplementedError

    def _cancel_reads(self) -> None:
        return None

    def _release(self, drained: bool) -> None:
        raise NotImplementedError


class PosixConnectedProcess(_OwnedTerminal):
    def __init__(self, pty, group: OwnedPosixProcessGroup | None) -> None:
        super().__init__(int(pty.pid))
        self._pty = pty
        self._group = group
        self._tree_finished = False
        self._exit_code: int | None = None

    @classmethod
    def spawn(cls, launch: RuntimeLaunch, *, rows: int, cols: int) -> "PosixConnectedProcess":
        if not _posix_group_proof_available():
            raise ConnectedLaunchError(
                "A connected PTY needs readable /proc process-group evidence",
                cleanup_confirmed=True,
            )
        try:
            from ptyprocess import PtyProcess
        except ImportError as error:
            raise ConnectedLaunchError(f"POSIX PTY support is unavailable: {error}", cleanup_confirmed=True) from error
        try:
            pty = PtyProcess.spawn(
                list(launch.argv),
                cwd=str(launch.cwd),
                env=dict(launch.env),
                dimensions=(rows, cols),
            )
        except Exception as error:
            # ptyprocess raises before forking or after the child reported a failed exec.
            raise ConnectedLaunchError(
                f"Could not start {launch.argv[0]} in a PTY: {error}",
                cleanup_confirmed=True,
            ) from error
        process = cls(pty, _capture_posix_group_identity(pty.pid))
        if process._group is None:
            confirmed, _ = process._stop_tree()
            raise ConnectedLaunchError(
                "Could not prove ownership of the connected PTY process group",
                cleanup_confirmed=confirmed,
            )
        return process

    def alive(self) -> bool:
        if self._stopped or self._tree_finished:
            return False
        if self._group is None or _owned_posix_group_state(self._group) != "empty":
            return True
        with self._stop_lock:
            # ptyprocess.isalive() reaps the leader, so only call it once the whole group is gone.
            if not self._tree_finished and self._reap_leader(time.monotonic()):
                self._tree_finished = True
        return not self._tree_finished

    def exit_code(self) -> int | None:
        if self.alive():
            return None
        return self._exit_code

    def _read_terminal(self, size: int) -> bytes:
        try:
            return self._pty.read(size)
        except (EOFError, OSError, ValueError):
            return b""

    def _write_terminal(self, data: bytes) -> None:
        try:
            self._pty.write(data)
        except (OSError, ValueError) as error:
            raise BrokenPipeError(str(error)) from error

    def _resize_terminal(self, rows: int, cols: int) -> None:
        self._pty.setwinsize(rows, cols)

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        if self._tree_finished:
            return True, "stopped"
        if self._group is None:
            return self._terminate_unproven_group(deadline)
        status = terminate_owned_posix_group(self._group, timeout=max(0.0, deadline - time.monotonic()))
        if status != "empty":
            return False, status
        if not self._reap_leader(deadline):
            return False, "root-unreaped"
        self._tree_finished = True
        return True, "stopped"

    def _terminate_unproven_group(self, deadline: float) -> tuple[bool, str]:
        # The unreaped leader pins its pid, so neither number can name someone else's process or group.
        for kill in (os.killpg, os.kill):
            with contextlib.suppress(OSError):
                kill(self.pid, _POSIX_KILL_SIGNAL)
        if not self._reap_leader(deadline):
            return False, "root-unreaped"
        status = _group_exit_status(self.pid, deadline)
        if status != "empty":
            return False, status
        self._tree_finished = True
        return True, "stopped"

    def _reap_leader(self, deadline: float) -> bool:
        while True:
            try:
                if not self._pty.isalive():
                    status = self._pty.exitstatus
                    self._exit_code = status if status is not None else -int(self._pty.signalstatus or 0)
                    return True
            except Exception:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def _release(self, drained: bool) -> None:
        # Closing an fd an operation is still inside could hand its number to an unrelated file.
        if drained:
            with contextlib.suppress(Exception):
                self._pty.close(force=True)


_WINDOWS_ESCAPE_ACCESS = 0x1000 | 0x0001 | 0x00100000  # QUERY_LIMITED_INFORMATION | TERMINATE | SYNCHRONIZE
_WINDOWS_QUERY_ACCESS = 0x1000
_WINDOWS_ERROR_INVALID_PARAMETER = 87


class WindowsConnectedProcess(_OwnedTerminal):
    _reads_cancellable = True

    def __init__(self, pty, job, pid: int) -> None:
        super().__init__(pid)
        self._pty = pty
        self._job = job
        self._handle = None
        self._assigned = False
        self._lineage: dict[int, object] = {}
        self._lineage_lock = threading.Lock()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._exit_code: int | None = None

    @classmethod
    def spawn(cls, launch: RuntimeLaunch, *, rows: int, cols: int) -> "WindowsConnectedProcess":
        try:
            import win32api
            import winpty
        except ImportError as error:
            raise ConnectedLaunchError(f"Windows ConPTY support is unavailable: {error}", cleanup_confirmed=True) from error
        arguments = " " + subprocess.list2cmdline(list(launch.argv[1:])) if len(launch.argv) > 1 else None
        environment = "\0".join(f"{name}={value}" for name, value in launch.env.items()) + "\0"
        try:
            job = _create_kill_on_close_job()
        except Exception as error:
            raise ConnectedLaunchError(f"Could not create a Job Object: {error}", cleanup_confirmed=True) from error
        try:
            # Request ConPTY explicitly; PtyProcess.spawn would let PYWINPTY_BACKEND override it.
            pty = winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)
        except Exception as error:
            win32api.CloseHandle(job)
            raise ConnectedLaunchError(f"ConPTY is unavailable: {error}", cleanup_confirmed=True) from error
        spawn_error: Exception | None = None
        try:
            spawned = pty.spawn(str(launch.argv[0]), cmdline=arguments, cwd=str(launch.cwd), env=environment)
        except Exception as error:
            spawned, spawn_error = False, error
        try:
            pid = pty.pid
        except Exception:
            pid = None
        if not pid:
            del pty
            win32api.CloseHandle(job)
            raise ConnectedLaunchError(
                f"ConPTY could not start {launch.argv[0]}: {spawn_error or 'spawn was refused'}",
                cleanup_confirmed=True,
            )
        process = cls(pty, job, int(pid))
        try:
            process._contain()
            if not spawned:
                raise spawn_error or RuntimeError("ConPTY reported a failed spawn")
        except Exception as error:
            confirmed, _ = process._stop_tree()
            raise ConnectedLaunchError(
                f"Could not contain the connected process tree: {error}",
                cleanup_confirmed=confirmed,
            ) from error
        return process

    def _capture_root(self) -> None:
        import win32api
        import win32con
        import win32process

        # pywinpty holds its own process handle, so this pid cannot be reused before we open it.
        if self._handle is None:
            self._handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA
                | win32con.PROCESS_TERMINATE
                | win32con.PROCESS_QUERY_LIMITED_INFORMATION
                | win32con.SYNCHRONIZE,
                False,
                self.pid,
            )
        if self.pid not in self._lineage:
            self._lineage[self.pid] = win32process.GetProcessTimes(self._handle)["CreationTime"]

    def _contain(self) -> None:
        import win32job

        self._capture_root()
        win32job.AssignProcessToJobObject(self._job, self._handle)
        self._assigned = True
        # The root ran unsuspended until assignment; anything it started before then is outside the job.
        escaped = self._scan_uncontained()
        if escaped is None:
            raise RuntimeError("the process tree could not be inspected after Job assignment")
        for _, handle in escaped:
            handle.Close()
        if escaped:
            raise RuntimeError(f"{len(escaped)} descendant process(es) started outside the Job Object")

    def alive(self) -> bool:
        job = self._job
        if self._stopped or job is None:
            return False
        if _job_exit_status(job, time.monotonic()) != "empty":
            return True
        # Brokered creation (for example packaged apps) leaves a descendant outside the job.
        uncontained = self._scan_uncontained()
        if uncontained is None:
            return True
        _close_all(uncontained)
        return bool(uncontained)

    def exit_code(self) -> int | None:
        if self._exit_code is None and not self.alive():
            self._record_exit()
        return self._exit_code

    def _record_exit(self) -> None:
        pty = self._pty
        if pty is not None:
            with contextlib.suppress(Exception):
                self._exit_code = pty.get_exitstatus()

    def _read_terminal(self, size: int) -> bytes:
        pty = self._pty
        if pty is None:
            return b""
        try:
            text = pty.read(blocking=True)
        except Exception:
            return b""
        return text.encode("utf-8", errors="replace")

    def _write_terminal(self, data: bytes) -> None:
        pty = self._pty
        if pty is None:
            raise BrokenPipeError("The connected process has been stopped")
        text = self._decoder.decode(data)
        if not text:
            return
        # PTY.write reports 0 even after a complete write, so its count must not drive a retry loop.
        try:
            pty.write(text)
        except Exception as error:
            raise BrokenPipeError(str(error)) from error

    def _resize_terminal(self, rows: int, cols: int) -> None:
        pty = self._pty
        if pty is None:
            raise BrokenPipeError("The connected process has been stopped")
        pty.set_size(cols, rows)

    def _cancel_reads(self) -> None:
        pty = self._pty
        if pty is not None:
            with contextlib.suppress(Exception):
                pty.cancel_io()

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        import win32job
        import win32process

        with contextlib.suppress(Exception):
            self._capture_root()
        uncontained = self._scan_uncontained()
        if self._assigned:
            with contextlib.suppress(Exception):
                win32job.TerminateJobObject(self._job, 1)
        elif self._handle is not None:
            with contextlib.suppress(Exception):
                win32process.TerminateProcess(self._handle, 1)
        descendants_stopped = self._stop_uncontained(uncontained, deadline)
        if self._assigned:
            status = _job_exit_status(self._job, deadline)
            if status == "unknown":
                return False, "job-accounting-unavailable"
            if status == "active":
                return False, "job-active-processes"
        if self._handle is None or not _wait_for_handle(self._handle, deadline):
            return False, "root-exit-unconfirmed"
        if not descendants_stopped:
            return False, "uncontained-descendants"
        return True, "stopped"

    def _stop_uncontained(self, pending, deadline: float) -> bool:
        import win32process

        while True:
            if pending is None:
                return False
            if not pending:
                return True
            for _, handle in pending:
                with contextlib.suppress(Exception):
                    win32process.TerminateProcess(handle, 1)
            for _, handle in pending:
                _wait_for_handle(handle, deadline)
                handle.Close()
            pending = self._scan_uncontained()
            if pending and time.monotonic() >= deadline:
                for _, handle in pending:
                    handle.Close()
                return False

    def _scan_uncontained(self):
        """Live descendants outside the job (all of them before assignment); None if unprovable."""
        with self._lineage_lock:
            return self._scan_uncontained_locked()

    def _scan_uncontained_locked(self):
        import pywintypes
        import win32api
        import win32job
        import win32process

        if self.pid not in self._lineage:
            return None
        try:
            parents = _windows_parent_pids()
        except OSError:
            return None
        found: list[tuple[int, object]] = []
        visited: set[int] = {self.pid}
        progressed = True
        while progressed:
            progressed = False
            for pid, parent in parents.items():
                if pid in visited or (pid not in self._lineage and parent not in self._lineage):
                    continue
                visited.add(pid)
                try:
                    handle = win32api.OpenProcess(_WINDOWS_ESCAPE_ACCESS, False, pid)
                    killable = True
                except pywintypes.error as error:
                    if error.winerror == _WINDOWS_ERROR_INVALID_PARAMETER:
                        continue
                    try:
                        handle = win32api.OpenProcess(_WINDOWS_QUERY_ACCESS, False, pid)
                        killable = False
                    except pywintypes.error as retry_error:
                        if retry_error.winerror == _WINDOWS_ERROR_INVALID_PARAMETER:
                            continue
                        return _close_all(found)
                try:
                    created = win32process.GetProcessTimes(handle)["CreationTime"]
                except pywintypes.error:
                    handle.Close()
                    return _close_all(found)
                known = self._lineage.get(pid) == created
                # A recycled parent id names an older, unrelated process.
                if not known and (parent not in self._lineage or created < self._lineage[parent]):
                    handle.Close()
                    continue
                if not known:
                    self._lineage[pid] = created
                    progressed = True
                if self._assigned and win32job.IsProcessInJob(handle, self._job):
                    handle.Close()
                    continue
                if not killable:
                    handle.Close()
                    return _close_all(found)
                found.append((pid, handle))
        return found

    def _release(self, drained: bool) -> None:
        import win32api

        self._record_exit()
        self._pty = None
        for handle in (self._handle, self._job):
            if handle is not None:
                with contextlib.suppress(Exception):
                    win32api.CloseHandle(handle)
        self._handle = None
        self._job = None


def _close_all(found) -> None:
    for _, handle in found:
        with contextlib.suppress(Exception):
            handle.Close()
    return None


def _wait_for_handle(handle, deadline: float) -> bool:
    import win32event

    remaining = max(0, int((deadline - time.monotonic()) * 1000))
    try:
        return win32event.WaitForSingleObject(handle, remaining) == win32event.WAIT_OBJECT_0
    except Exception:
        return False


def _create_kill_on_close_job():
    import win32api
    import win32job

    job = win32job.CreateJobObject(None, "")
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    except Exception:
        win32api.CloseHandle(job)
        raise
    return job


def _windows_parent_pids() -> dict[int, int]:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel32, name)
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        function.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        parents: dict[int, int] = {}
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return parents
    finally:
        kernel32.CloseHandle(snapshot)
