from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


RuntimeProcessOutcome = Literal[
    "exited",
    "timeout",
    "launch-failed",
    "containment-setup-failed",
]


@dataclass(frozen=True)
class ProcessStopEvidence:
    job_id: str
    generation: str
    confirmed: bool
    reason: str


@dataclass(frozen=True)
class RuntimeProcessLifecycle:
    job_id: str
    generation: str


@dataclass(frozen=True)
class RuntimeProcessIdentity:
    pid: int
    created_at: str | None


@dataclass(frozen=True)
class CompletedRuntimeProcess:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    process_stop_evidence: ProcessStopEvidence
    outcome: RuntimeProcessOutcome = "exited"
    root_identity: RuntimeProcessIdentity | None = None


ProcessIdentityState = Literal["alive", "exited", "reused", "unknown"]


def may_clear_active_work(record, evidence: ProcessStopEvidence) -> bool:
    return (
        evidence.confirmed
        and record.active_run is not None
        and record.active_run.job_id == evidence.job_id
        and record.active_run.generation == evidence.generation
    )


def read_process_identity(pid: int) -> RuntimeProcessIdentity | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import win32api
            import win32con
            import win32process

            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )
            try:
                times = win32process.GetProcessTimes(handle)
            finally:
                handle.Close()
        except Exception:
            return None
        return RuntimeProcessIdentity(
            pid=pid,
            created_at=times["CreationTime"].isoformat(),
        )

    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
    except OSError:
        return None
    if len(fields) < 22:
        return None
    return RuntimeProcessIdentity(pid=pid, created_at=fields[21])


def process_identity_matches(identity: RuntimeProcessIdentity) -> bool | None:
    current = read_process_identity(identity.pid)
    if current is None or identity.created_at is None:
        return None
    return current.created_at == identity.created_at


def process_identity_state(identity: RuntimeProcessIdentity) -> ProcessIdentityState:
    if identity.pid <= 0:
        return "unknown"
    if os.name == "nt":
        try:
            import win32api
            import win32con
            import win32process

            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                identity.pid,
            )
        except Exception:
            return "exited"
        try:
            times = win32process.GetProcessTimes(handle)
            if identity.created_at is not None and times["CreationTime"].isoformat() != identity.created_at:
                return "reused"
            exit_code = win32process.GetExitCodeProcess(handle)
            return "alive" if exit_code == win32con.STILL_ACTIVE else "exited"
        except Exception:
            return "unknown"
        finally:
            handle.Close()

    current = read_process_identity(identity.pid)
    if current is None:
        return "exited"
    if identity.created_at is not None and current.created_at != identity.created_at:
        return "reused"
    try:
        os.kill(identity.pid, 0)
    except ProcessLookupError:
        return "exited"
    except PermissionError:
        return "alive"
    except OSError:
        return "unknown"
    return "alive"


def run_supervised(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    lifecycle: RuntimeProcessLifecycle,
) -> CompletedRuntimeProcess:
    start = time.monotonic()
    if os.name == "nt":
        return _run_supervised_windows(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            lifecycle=lifecycle,
            start=start,
        )
    return _run_supervised_posix(
        argv,
        cwd=cwd,
        env=env,
        timeout=timeout,
        lifecycle=lifecycle,
        start=start,
    )


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _remaining_millis(deadline: float) -> int:
    return max(0, int(_remaining_seconds(deadline) * 1000))


def _evidence(
    lifecycle: RuntimeProcessLifecycle,
    *,
    confirmed: bool,
    reason: str,
) -> ProcessStopEvidence:
    return ProcessStopEvidence(
        job_id=lifecycle.job_id,
        generation=lifecycle.generation,
        confirmed=confirmed,
        reason=reason,
    )


def _decode(chunks: list[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", errors="replace")


def _reader(stream, sink: list[bytes]) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            sink.append(chunk)
    finally:
        stream.close()


def _group_exited(group_id: int) -> bool | None:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return None
    return False


def _run_supervised_posix(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    lifecycle: RuntimeProcessLifecycle,
    start: float,
) -> CompletedRuntimeProcess:
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise
    except OSError as error:
        return CompletedRuntimeProcess(
            exit_code=125,
            stdout="",
            stderr=str(error),
            duration_seconds=time.monotonic() - start,
            process_stop_evidence=_evidence(
                lifecycle,
                confirmed=False,
                reason="launch-failed",
            ),
            outcome="launch-failed",
        )

    identity = read_process_identity(process.pid)
    deadline = start + timeout
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        exit_code = process.returncode
        while _remaining_seconds(deadline) > 0:
            exited = _group_exited(process.pid)
            if exited is True:
                return CompletedRuntimeProcess(
                    exit_code=exit_code,
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    duration_seconds=time.monotonic() - start,
                    process_stop_evidence=_evidence(
                        lifecycle,
                        confirmed=True,
                        reason="exited",
                    ),
                    outcome="exited",
                    root_identity=identity,
                )
            if exited is None:
                break
            time.sleep(0.02)
        return CompletedRuntimeProcess(
            exit_code=exit_code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration_seconds=time.monotonic() - start,
            process_stop_evidence=_evidence(
                lifecycle,
                confirmed=False,
                reason="descendants-still-running",
            ),
            outcome="exited",
            root_identity=identity,
        )
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        confirmed = _group_exited(process.pid) is True
        return CompletedRuntimeProcess(
            exit_code=124,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration_seconds=time.monotonic() - start,
            process_stop_evidence=_evidence(
                lifecycle,
                confirmed=confirmed,
                reason="timeout" if confirmed else "timeout-unconfirmed",
            ),
            outcome="timeout",
            root_identity=identity,
        )


def _job_active_processes(job_handle) -> int:
    import win32job

    info = win32job.QueryInformationJobObject(
        job_handle,
        win32job.JobObjectBasicAccountingInformation,
    )
    return int(info["ActiveProcesses"])


def _wait_for_job_exit(job_handle, deadline: float) -> bool:
    while _remaining_seconds(deadline) > 0:
        if _job_active_processes(job_handle) == 0:
            return True
        time.sleep(0.02)
    return _job_active_processes(job_handle) == 0


def _open_pipe_reader(read_handle):
    import msvcrt

    fd = msvcrt.open_osfhandle(int(read_handle.Detach()), os.O_RDONLY)
    return os.fdopen(fd, "rb", closefd=True)


def _windows_tree_identities(root_pid: int) -> tuple[RuntimeProcessIdentity, ...]:
    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return ()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        table: dict[int, int] = {}
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)

    seen = {root_pid}
    queue = [root_pid]
    while queue:
        parent = queue.pop(0)
        children = [pid for pid, ppid in table.items() if ppid == parent and pid not in seen]
        seen.update(children)
        queue.extend(children)
    identities: list[RuntimeProcessIdentity] = []
    for pid in sorted(seen):
        identity = read_process_identity(pid)
        if identity is not None:
            identities.append(identity)
    return tuple(identities)


def _terminate_windows_identity(identity: RuntimeProcessIdentity, exit_code: int) -> None:
    import win32api
    import win32con
    import win32process

    if process_identity_state(identity) != "alive":
        return
    handle = win32api.OpenProcess(
        win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        identity.pid,
    )
    try:
        times = win32process.GetProcessTimes(handle)
        if identity.created_at is not None and times["CreationTime"].isoformat() != identity.created_at:
            return
        win32process.TerminateProcess(handle, exit_code)
    finally:
        handle.Close()


def _wait_for_windows_identities_exit(
    identities: tuple[RuntimeProcessIdentity, ...],
    deadline: float,
) -> bool:
    while _remaining_seconds(deadline) > 0:
        states = [process_identity_state(identity) for identity in identities]
        if not any(state == "alive" for state in states):
            return True
        time.sleep(0.02)
    states = [process_identity_state(identity) for identity in identities]
    return not any(state == "alive" for state in states)


def _terminate_windows_tree(root_pid: int, *, exit_code: int) -> tuple[RuntimeProcessIdentity, ...]:
    identities = _windows_tree_identities(root_pid)
    for identity in reversed(identities):
        with contextlib.suppress(Exception):
            _terminate_windows_identity(identity, exit_code)
    return identities


def _taskkill_windows_tree(*identities: RuntimeProcessIdentity) -> None:
    seen: set[tuple[int, str | None]] = set()
    for identity in identities:
        key = (identity.pid, identity.created_at)
        if key in seen:
            continue
        seen.add(key)
        if process_identity_state(identity) != "alive":
            continue
        subprocess.run(
            ["taskkill", "/PID", str(identity.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )


def _watch_owner_and_kill_tree(owner_pid: int, root_pid: int, root_created_at: str, stop_file: str) -> int:
    stop_path = Path(stop_file)
    owner_identity = read_process_identity(owner_pid)
    root_identity = RuntimeProcessIdentity(pid=root_pid, created_at=root_created_at)
    observed: dict[tuple[int, str | None], RuntimeProcessIdentity] = {
        (root_identity.pid, root_identity.created_at): root_identity,
    }
    while True:
        if stop_path.exists():
            return 0
        for identity in _windows_tree_identities(root_pid):
            observed.setdefault((identity.pid, identity.created_at), identity)
        if owner_identity is None or process_identity_state(owner_identity) != "alive":
            identities = tuple(dict.fromkeys((*observed.values(), *_windows_tree_identities(root_pid))))
            _taskkill_windows_tree(root_identity, *identities)
            for identity in reversed(identities):
                with contextlib.suppress(Exception):
                    _terminate_windows_identity(identity, 125)
            if root_identity not in identities and process_identity_state(root_identity) == "alive":
                with contextlib.suppress(Exception):
                    _terminate_windows_identity(root_identity, 125)
            _wait_for_windows_identities_exit(
                tuple(dict.fromkeys((root_identity, *identities))),
                time.monotonic() + 5,
            )
            return 0
        time.sleep(0.05)


def _track_windows_tree(
    root_pid: int,
    observed: dict[tuple[int, str | None], RuntimeProcessIdentity],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        for identity in _windows_tree_identities(root_pid):
            observed.setdefault((identity.pid, identity.created_at), identity)
        time.sleep(0.02)


def _start_windows_owner_watchdog(
    *,
    owner_pid: int,
    root_identity: RuntimeProcessIdentity,
    stop_file: Path,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from flowgency.jobs.processes import _watch_owner_and_kill_tree; "
                "raise SystemExit(_watch_owner_and_kill_tree(int(__import__('sys').argv[1]), "
                "int(__import__('sys').argv[2]), __import__('sys').argv[3], __import__('sys').argv[4]))"
            ),
            str(owner_pid),
            str(root_identity.pid),
            root_identity.created_at or "",
            str(stop_file),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
        text=True,
    )


def _run_supervised_windows(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    lifecycle: RuntimeProcessLifecycle,
    start: float,
) -> CompletedRuntimeProcess:
    import msvcrt
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32job
    import win32pipe
    import win32process
    import win32security

    deadline = start + timeout
    job_handle = None
    process_handle = None
    thread_handle = None
    stdout_write = None
    stderr_write = None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_thread = None
    stderr_thread = None
    identity = None
    observed_identities: dict[tuple[int, str | None], RuntimeProcessIdentity] = {}
    watchdog_process = None
    watchdog_stop = cwd / ".flowgency-process-watchdog.stop"
    tracker_stop = threading.Event()
    tracker_thread = None

    try:
        with contextlib.suppress(OSError):
            watchdog_stop.unlink()
        security = win32security.SECURITY_ATTRIBUTES()
        security.bInheritHandle = 1

        stdout_read, stdout_write = win32pipe.CreatePipe(security, 0)
        stderr_read, stderr_write = win32pipe.CreatePipe(security, 0)
        win32api.SetHandleInformation(stdout_read, win32con.HANDLE_FLAG_INHERIT, 0)
        win32api.SetHandleInformation(stderr_read, win32con.HANDLE_FLAG_INHERIT, 0)
        stdout_reader = _open_pipe_reader(stdout_read)
        stderr_reader = _open_pipe_reader(stderr_read)

        job_handle = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(
            job_handle,
            win32job.JobObjectExtendedLimitInformation,
        )
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            job_handle,
            win32job.JobObjectExtendedLimitInformation,
            limits,
        )

        startup = win32process.STARTUPINFO()
        startup.dwFlags |= win32process.STARTF_USESTDHANDLES

        with open(os.devnull, "rb") as devnull:
            stdin_handle = msvcrt.get_osfhandle(devnull.fileno())
            win32api.SetHandleInformation(
                stdin_handle,
                win32con.HANDLE_FLAG_INHERIT,
                win32con.HANDLE_FLAG_INHERIT,
            )
            startup.hStdInput = stdin_handle
            startup.hStdOutput = int(stdout_write)
            startup.hStdError = int(stderr_write)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | win32con.CREATE_SUSPENDED
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
                if getattr(error, "winerror", None) in {2, 3}:
                    raise FileNotFoundError(str(argv[0])) from error
                return CompletedRuntimeProcess(
                    exit_code=125,
                    stdout="",
                    stderr=str(error),
                    duration_seconds=time.monotonic() - start,
                    process_stop_evidence=_evidence(
                        lifecycle,
                        confirmed=False,
                        reason="launch-failed",
                    ),
                    outcome="launch-failed",
                )

        identity = read_process_identity(pid)
        if identity is not None:
            observed_identities[(identity.pid, identity.created_at)] = identity
        try:
            win32job.AssignProcessToJobObject(job_handle, process_handle)
        except pywintypes.error as error:
            with contextlib.suppress(Exception):
                win32process.TerminateProcess(process_handle, 125)
            with contextlib.suppress(Exception):
                win32event.WaitForSingleObject(process_handle, 5000)
            return CompletedRuntimeProcess(
                exit_code=125,
                stdout="",
                stderr=str(error),
                duration_seconds=time.monotonic() - start,
                process_stop_evidence=_evidence(
                    lifecycle,
                    confirmed=False,
                    reason="containment-setup-failed",
                ),
                outcome="containment-setup-failed",
                root_identity=identity,
            )

        stdout_thread = threading.Thread(target=_reader, args=(stdout_reader, stdout_chunks))
        stderr_thread = threading.Thread(target=_reader, args=(stderr_reader, stderr_chunks))
        stdout_thread.start()
        stderr_thread.start()

        if identity is not None:
            watchdog_process = _start_windows_owner_watchdog(
                owner_pid=os.getpid(),
                root_identity=identity,
                stop_file=watchdog_stop,
            )
            tracker_thread = threading.Thread(
                target=_track_windows_tree,
                args=(pid, observed_identities, tracker_stop),
                daemon=True,
            )
            tracker_thread.start()

        win32process.ResumeThread(thread_handle)
        win32api.CloseHandle(stdout_write)
        win32api.CloseHandle(stderr_write)
        stdout_write = None
        stderr_write = None

        if win32event.WaitForSingleObject(process_handle, _remaining_millis(deadline)) == win32con.WAIT_TIMEOUT:
            for tracked in _windows_tree_identities(pid):
                observed_identities.setdefault((tracked.pid, tracked.created_at), tracked)
            win32job.TerminateJobObject(job_handle, 124)
            if identity is not None:
                _taskkill_windows_tree(identity, *tuple(observed_identities.values()))
            for tracked in _terminate_windows_tree(pid, exit_code=124):
                observed_identities.setdefault((tracked.pid, tracked.created_at), tracked)
            win32event.WaitForSingleObject(process_handle, 5000)
            _wait_for_job_exit(job_handle, time.monotonic() + 5)
            if stdout_thread is not None:
                stdout_thread.join(timeout=5)
            if stderr_thread is not None:
                stderr_thread.join(timeout=5)
            confirmed = _wait_for_windows_identities_exit(
                tuple(observed_identities.values()),
                time.monotonic() + 5,
            )
            return CompletedRuntimeProcess(
                exit_code=124,
                stdout=_decode(stdout_chunks),
                stderr=_decode(stderr_chunks),
                duration_seconds=time.monotonic() - start,
                process_stop_evidence=_evidence(
                    lifecycle,
                    confirmed=confirmed,
                    reason="timeout" if confirmed else "timeout-unconfirmed",
                ),
                outcome="timeout",
                root_identity=identity,
            )

        exit_code = win32process.GetExitCodeProcess(process_handle)
        while _remaining_seconds(deadline) > 0:
            for tracked in _windows_tree_identities(pid):
                observed_identities.setdefault((tracked.pid, tracked.created_at), tracked)
            readers_done = (
                stdout_thread is not None
                and stderr_thread is not None
                and not stdout_thread.is_alive()
                and not stderr_thread.is_alive()
            )
            if readers_done and _wait_for_windows_identities_exit(tuple(observed_identities.values()), time.monotonic() + 0.1):
                return CompletedRuntimeProcess(
                    exit_code=exit_code,
                    stdout=_decode(stdout_chunks),
                    stderr=_decode(stderr_chunks),
                    duration_seconds=time.monotonic() - start,
                    process_stop_evidence=_evidence(
                        lifecycle,
                        confirmed=True,
                        reason="exited",
                    ),
                    outcome="exited",
                    root_identity=identity,
                )
            time.sleep(0.02)

        for tracked in _windows_tree_identities(pid):
            observed_identities.setdefault((tracked.pid, tracked.created_at), tracked)
        win32job.TerminateJobObject(job_handle, 124)
        if identity is not None:
            _taskkill_windows_tree(identity, *tuple(observed_identities.values()))
        for tracked in _terminate_windows_tree(pid, exit_code=124):
            observed_identities.setdefault((tracked.pid, tracked.created_at), tracked)
        win32event.WaitForSingleObject(process_handle, 5000)
        _wait_for_job_exit(job_handle, time.monotonic() + 5)
        if stdout_thread is not None:
            stdout_thread.join(timeout=5)
        if stderr_thread is not None:
            stderr_thread.join(timeout=5)
        confirmed = _wait_for_windows_identities_exit(
            tuple(observed_identities.values()),
            time.monotonic() + 5,
        )
        return CompletedRuntimeProcess(
            exit_code=124,
            stdout=_decode(stdout_chunks),
            stderr=_decode(stderr_chunks),
            duration_seconds=time.monotonic() - start,
            process_stop_evidence=_evidence(
                lifecycle,
                confirmed=confirmed,
                reason="timeout" if confirmed else "timeout-unconfirmed",
            ),
            outcome="timeout",
            root_identity=identity,
        )
    finally:
        with contextlib.suppress(OSError):
            watchdog_stop.write_text("stop", encoding="utf-8")
        tracker_stop.set()
        if tracker_thread is not None:
            tracker_thread.join(timeout=2)
        if watchdog_process is not None:
            with contextlib.suppress(Exception):
                watchdog_process.wait(timeout=5)
        with contextlib.suppress(OSError):
            watchdog_stop.unlink()
        if stdout_write is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(stdout_write)
        if stderr_write is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(stderr_write)
        if thread_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(thread_handle)
        if process_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(process_handle)
        if job_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(job_handle)


if __name__ == "__main__" and len(sys.argv) == 5:
    raise SystemExit(
        _watch_owner_and_kill_tree(
            int(sys.argv[1]),
            int(sys.argv[2]),
            sys.argv[3],
            sys.argv[4],
        )
    )
