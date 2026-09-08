from __future__ import annotations

import contextlib
import os
import signal
import subprocess
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


_POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


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
class PosixProcessSnapshot:
    pid: int
    created_at: str
    process_group_id: int
    session_id: int
    state: str


@dataclass(frozen=True)
class OwnedPosixProcessGroup:
    leader: RuntimeProcessIdentity
    process_group_id: int
    session_id: int


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

    created_at, status = _read_posix_process_created_at(pid)
    if status != "ok" or created_at is None:
        return None
    return RuntimeProcessIdentity(pid=pid, created_at=created_at)


def _parse_proc_stat_created_at(text: str) -> str | None:
    snapshot = _parse_proc_stat_snapshot(text)
    if snapshot is None:
        return None
    return snapshot.created_at


def _parse_proc_stat_snapshot(text: str) -> PosixProcessSnapshot | None:
    line = text.strip()
    closing = line.rfind(")")
    if closing <= 0:
        return None
    prefix = line[:closing]
    pid_text, _, _ = prefix.partition(" ")
    try:
        pid = int(pid_text)
    except ValueError:
        return None
    fields = line[closing + 1 :].strip().split()
    if len(fields) < 20:
        return None
    try:
        process_group_id = int(fields[2])
        session_id = int(fields[3])
    except ValueError:
        return None
    created_at = fields[19]
    return PosixProcessSnapshot(
        pid=pid,
        created_at=created_at,
        process_group_id=process_group_id,
        session_id=session_id,
        state=fields[0],
    )


def _read_posix_process_created_at(pid: int) -> tuple[str | None, Literal["ok", "exited", "unknown"]]:
    snapshot, status = _read_posix_process_snapshot(pid)
    if status != "ok" or snapshot is None:
        return None, status
    return snapshot.created_at, "ok"


def _read_posix_process_snapshot(
    pid: int,
) -> tuple[PosixProcessSnapshot | None, Literal["ok", "exited", "unknown"]]:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "exited"
    except PermissionError:
        return None, "unknown"
    except OSError:
        return None, "unknown"
    snapshot = _parse_proc_stat_snapshot(text)
    if snapshot is None:
        return None, "unknown"
    return snapshot, "ok"


def _capture_posix_group_identity(pid: int) -> OwnedPosixProcessGroup | None:
    snapshot, status = _read_posix_process_snapshot(pid)
    if status != "ok" or snapshot is None:
        return None
    if snapshot.process_group_id != pid or snapshot.session_id != pid:
        return None
    return OwnedPosixProcessGroup(
        leader=RuntimeProcessIdentity(pid=pid, created_at=snapshot.created_at),
        process_group_id=snapshot.process_group_id,
        session_id=snapshot.session_id,
    )


def _supports_waitid_wnowait() -> bool:
    return all(
        hasattr(os, name)
        for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    )


def _observe_posix_root_exit(process: subprocess.Popen[bytes], deadline: float) -> int | None:
    del deadline
    if _supports_waitid_wnowait():
        try:
            result = os.waitid(  # type: ignore[attr-defined]
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except ChildProcessError:
            return 0
        except OSError:
            return None
        if result is not None and getattr(result, "si_pid", 0):
            status = int(getattr(result, "si_status", 0))
            code = getattr(result, "si_code", None)
            if code in {
                getattr(os, "CLD_KILLED", object()),
                getattr(os, "CLD_DUMPED", object()),
            }:
                return -status
            return status
    snapshot, status = _read_posix_process_snapshot(process.pid)
    if status == "exited":
        return 0
    if status == "ok" and snapshot is not None and snapshot.state == "Z":
        return 0
    return None


def _owned_posix_group_state(
    group_identity: OwnedPosixProcessGroup,
) -> Literal["empty", "active", "unknown", "reused"]:
    leader_snapshot, leader_status = _read_posix_process_snapshot(group_identity.leader.pid)
    if leader_status != "ok" or leader_snapshot is None:
        return "reused"
    if (
        leader_snapshot.created_at != group_identity.leader.created_at
        or leader_snapshot.process_group_id != group_identity.process_group_id
        or leader_snapshot.session_id != group_identity.session_id
    ):
        return "reused"
    if leader_snapshot.state != "Z":
        return "active"

    proc_root = Path("/proc")
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return "unknown"

    for entry in entries:
        if not entry.name.isdigit():
            continue
        snapshot, status = _read_posix_process_snapshot(int(entry.name))
        if status == "unknown":
            return "unknown"
        if status != "ok" or snapshot is None:
            continue
        if snapshot.process_group_id != group_identity.process_group_id:
            continue
        if snapshot.session_id != group_identity.session_id:
            return "reused"
        if snapshot.pid == group_identity.leader.pid:
            if snapshot.created_at != group_identity.leader.created_at:
                return "reused"
            continue
        if snapshot.state != "Z":
            return "active"
    return "empty"


def _owned_posix_group_status(
    group_identity: OwnedPosixProcessGroup,
    deadline: float,
) -> Literal["empty", "active", "unknown", "reused"]:
    while True:
        state = _owned_posix_group_state(group_identity)
        if state != "active":
            return state
        if _remaining_seconds(deadline) <= 0:
            return "active"
        time.sleep(0.02)


def _signal_owned_posix_group(
    group_identity: OwnedPosixProcessGroup,
    sig: int,
) -> Literal["signaled", "empty", "unavailable"]:
    leader_snapshot, leader_status = _read_posix_process_snapshot(group_identity.leader.pid)
    if leader_status != "ok" or leader_snapshot is None:
        return "unavailable"
    if (
        leader_snapshot.created_at != group_identity.leader.created_at
        or leader_snapshot.process_group_id != group_identity.process_group_id
        or leader_snapshot.session_id != group_identity.session_id
    ):
        return "unavailable"
    try:
        os.killpg(group_identity.process_group_id, sig)
    except ProcessLookupError:
        return "empty"
    except PermissionError:
        return "unavailable"
    except OSError:
        return "unavailable"
    return "signaled"


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
            return "unknown"
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

    return _posix_process_identity_state(identity)


def _posix_process_identity_state(identity: RuntimeProcessIdentity) -> ProcessIdentityState:
    if identity.pid <= 0:
        return "unknown"

    created_at, status = _read_posix_process_created_at(identity.pid)
    if status == "exited":
        return "exited"
    if status != "ok" or created_at is None:
        return "unknown"
    if identity.created_at is not None and created_at != identity.created_at:
        return "reused"
    try:
        os.kill(identity.pid, 0)
    except ProcessLookupError:
        return "exited"
    except PermissionError:
        return "unknown"
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


def _group_exit_status(group_id: int, deadline: float) -> Literal["empty", "active", "unknown"]:
    while True:
        exited = _group_exited(group_id)
        if exited is True:
            return "empty"
        if exited is None:
            return "unknown"
        if _remaining_seconds(deadline) <= 0:
            return "active"
        time.sleep(0.02)


def _drain_deadline(seconds: float = 1.0) -> float:
    return time.monotonic() + seconds


def _reap_posix_root(process: subprocess.Popen[bytes], *, timeout: float) -> int | None:
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _posix_completed_result(
    *,
    process: subprocess.Popen[bytes],
    lifecycle: RuntimeProcessLifecycle,
    start: float,
    stdout_chunks: list[bytes],
    stderr_chunks: list[bytes],
    stdout_thread,
    stderr_thread,
    outcome: RuntimeProcessOutcome,
    exit_code: int,
    confirmed: bool,
    reason: str,
    root_identity: RuntimeProcessIdentity | None,
    drain_deadline: float,
) -> CompletedRuntimeProcess:
    readers_done = _join_reader_threads(stdout_thread, stderr_thread, deadline=drain_deadline)
    if confirmed and not readers_done:
        confirmed = False
        reason = "io-drain-incomplete"
    return CompletedRuntimeProcess(
        exit_code=exit_code,
        stdout=_decode(stdout_chunks),
        stderr=_decode(stderr_chunks),
        duration_seconds=time.monotonic() - start,
        process_stop_evidence=_evidence(
            lifecycle,
            confirmed=confirmed,
            reason=reason,
        ),
        outcome=outcome,
        root_identity=root_identity,
    )


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
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, stdout_chunks), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(process.stderr, stderr_chunks), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    group_identity = _capture_posix_group_identity(process.pid)
    if group_identity is None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        return _posix_completed_result(
            process=process,
            lifecycle=lifecycle,
            start=start,
            stdout_chunks=stdout_chunks,
            stderr_chunks=stderr_chunks,
            stdout_thread=stdout_thread,
            stderr_thread=stderr_thread,
            outcome="containment-setup-failed",
            exit_code=125,
            confirmed=False,
            reason="containment-setup-failed",
            root_identity=identity,
            drain_deadline=time.monotonic() + 5,
        )
    deadline = start + timeout
    root_exited = False
    while _remaining_seconds(deadline) > 0:
        if _observe_posix_root_exit(process, deadline) is not None:
            root_exited = True
        group_status = _owned_posix_group_status(group_identity, time.monotonic())
        if root_exited and group_status == "empty":
            exit_code = _reap_posix_root(process, timeout=0)
            if exit_code is None:
                return _posix_completed_result(
                    process=process,
                    lifecycle=lifecycle,
                    start=start,
                    stdout_chunks=stdout_chunks,
                    stderr_chunks=stderr_chunks,
                    stdout_thread=stdout_thread,
                    stderr_thread=stderr_thread,
                    outcome="exited",
                    exit_code=125,
                    confirmed=False,
                    reason="group-state-unavailable",
                    root_identity=identity,
                    drain_deadline=_drain_deadline(),
                )
            return _posix_completed_result(
                process=process,
                lifecycle=lifecycle,
                start=start,
                stdout_chunks=stdout_chunks,
                stderr_chunks=stderr_chunks,
                stdout_thread=stdout_thread,
                stderr_thread=stderr_thread,
                outcome="exited",
                exit_code=exit_code,
                confirmed=identity is not None,
                reason="exited" if identity is not None else "group-state-unavailable",
                root_identity=identity,
                drain_deadline=_drain_deadline(),
            )
        if root_exited and group_status == "reused":
            exit_code = _reap_posix_root(process, timeout=0) or 0
            return _posix_completed_result(
                process=process,
                lifecycle=lifecycle,
                start=start,
                stdout_chunks=stdout_chunks,
                stderr_chunks=stderr_chunks,
                stdout_thread=stdout_thread,
                stderr_thread=stderr_thread,
                outcome="exited",
                exit_code=exit_code,
                confirmed=False,
                reason="group-identity-unavailable",
                root_identity=identity,
                drain_deadline=_drain_deadline(),
            )
        if root_exited and group_status == "unknown":
            exit_code = _reap_posix_root(process, timeout=0) or 0
            return _posix_completed_result(
                process=process,
                lifecycle=lifecycle,
                start=start,
                stdout_chunks=stdout_chunks,
                stderr_chunks=stderr_chunks,
                stdout_thread=stdout_thread,
                stderr_thread=stderr_thread,
                outcome="exited",
                exit_code=exit_code,
                confirmed=False,
                reason="group-state-unavailable",
                root_identity=identity,
                drain_deadline=_drain_deadline(),
            )
        time.sleep(0.02)

    group_status = _owned_posix_group_status(group_identity, time.monotonic())
    if group_status == "active":
        signal_status = _signal_owned_posix_group(group_identity, _POSIX_KILL_SIGNAL)
        if signal_status == "signaled":
            wait_deadline = time.monotonic() + 5
            group_status = _owned_posix_group_status(group_identity, wait_deadline)
            if group_status == "empty":
                exit_code = _reap_posix_root(process, timeout=0)
                if exit_code is None:
                    exit_code = _reap_posix_root(process, timeout=5)
                if exit_code is not None:
                    return _posix_completed_result(
                        process=process,
                        lifecycle=lifecycle,
                        start=start,
                        stdout_chunks=stdout_chunks,
                        stderr_chunks=stderr_chunks,
                        stdout_thread=stdout_thread,
                        stderr_thread=stderr_thread,
                        outcome="timeout",
                        exit_code=124,
                        confirmed=identity is not None,
                        reason="timeout" if identity is not None else "group-state-unavailable",
                        root_identity=identity,
                        drain_deadline=wait_deadline,
                    )
        elif signal_status == "empty":
            exit_code = _reap_posix_root(process, timeout=0)
            if exit_code is not None:
                return _posix_completed_result(
                    process=process,
                    lifecycle=lifecycle,
                    start=start,
                    stdout_chunks=stdout_chunks,
                    stderr_chunks=stderr_chunks,
                    stdout_thread=stdout_thread,
                    stderr_thread=stderr_thread,
                    outcome="timeout",
                    exit_code=124,
                    confirmed=identity is not None,
                    reason="timeout" if identity is not None else "group-state-unavailable",
                    root_identity=identity,
                    drain_deadline=_drain_deadline(),
                )

    reason = "descendants-still-running"
    if group_status == "unknown":
        reason = "group-state-unavailable"
    elif group_status == "reused":
        reason = "group-identity-unavailable"
    return _posix_completed_result(
        process=process,
        lifecycle=lifecycle,
        start=start,
        stdout_chunks=stdout_chunks,
        stderr_chunks=stderr_chunks,
        stdout_thread=stdout_thread,
        stderr_thread=stderr_thread,
        outcome="timeout",
        exit_code=124,
        confirmed=False,
        reason=reason,
        root_identity=identity,
        drain_deadline=_drain_deadline(),
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


def _job_exit_status(job_handle, deadline: float) -> Literal["empty", "active", "unknown"]:
    last_active = False
    while True:
        try:
            active = _job_active_processes(job_handle)
        except Exception:
            return "unknown"
        if active == 0:
            return "empty"
        last_active = True
        if _remaining_seconds(deadline) <= 0:
            return "active"
        time.sleep(0.02)


def _join_reader_threads(*threads, deadline: float) -> bool:
    all_done = True
    for thread in threads:
        if thread is None:
            continue
        thread.join(timeout=_remaining_seconds(deadline))
        if thread.is_alive():
            all_done = False
    return all_done


def _windows_timeout_result(
    *,
    job_handle,
    process_handle,
    stdout_thread,
    stderr_thread,
    stdout_chunks: list[bytes],
    stderr_chunks: list[bytes],
    lifecycle: RuntimeProcessLifecycle,
    start: float,
    identity: RuntimeProcessIdentity | None,
) -> CompletedRuntimeProcess:
    import contextlib as _contextlib
    import win32con
    import win32event
    import win32job

    with _contextlib.suppress(Exception):
        win32job.TerminateJobObject(job_handle, 124)
    with _contextlib.suppress(Exception):
        win32event.WaitForSingleObject(process_handle, 5000)
    wait_deadline = time.monotonic() + 5
    job_status = _job_exit_status(job_handle, wait_deadline)
    readers_done = _join_reader_threads(stdout_thread, stderr_thread, deadline=wait_deadline)
    if job_status == "empty" and readers_done:
        confirmed = True
        reason = "timeout"
    elif job_status == "unknown":
        confirmed = False
        reason = "job-accounting-unavailable"
    elif job_status == "active":
        confirmed = False
        reason = "job-active-processes"
    else:
        confirmed = False
        reason = "io-drain-incomplete"
    return CompletedRuntimeProcess(
        exit_code=124,
        stdout=_decode(stdout_chunks),
        stderr=_decode(stderr_chunks),
        duration_seconds=time.monotonic() - start,
        process_stop_evidence=_evidence(
            lifecycle,
            confirmed=confirmed,
            reason=reason,
        ),
        outcome="timeout",
        root_identity=identity,
    )


def _open_pipe_reader(read_handle):
    import msvcrt

    fd = msvcrt.open_osfhandle(int(read_handle.Detach()), os.O_RDONLY)
    return os.fdopen(fd, "rb", closefd=True)


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
    stdout_reader = None
    stderr_reader = None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_thread = None
    stderr_thread = None
    identity = None

    try:
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

        stdout_thread = threading.Thread(target=_reader, args=(stdout_reader, stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=_reader, args=(stderr_reader, stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        win32process.ResumeThread(thread_handle)
        win32api.CloseHandle(stdout_write)
        win32api.CloseHandle(stderr_write)
        stdout_write = None
        stderr_write = None

        if win32event.WaitForSingleObject(process_handle, _remaining_millis(deadline)) == win32con.WAIT_TIMEOUT:
            return _windows_timeout_result(
                job_handle=job_handle,
                process_handle=process_handle,
                stdout_thread=stdout_thread,
                stderr_thread=stderr_thread,
                stdout_chunks=stdout_chunks,
                stderr_chunks=stderr_chunks,
                lifecycle=lifecycle,
                start=start,
                identity=identity,
            )

        exit_code = win32process.GetExitCodeProcess(process_handle)
        job_status = _job_exit_status(job_handle, deadline)
        if job_status == "active":
            return _windows_timeout_result(
                job_handle=job_handle,
                process_handle=process_handle,
                stdout_thread=stdout_thread,
                stderr_thread=stderr_thread,
                stdout_chunks=stdout_chunks,
                stderr_chunks=stderr_chunks,
                lifecycle=lifecycle,
                start=start,
                identity=identity,
            )
        readers_done = _join_reader_threads(stdout_thread, stderr_thread, deadline=_drain_deadline())
        if job_status == "empty" and readers_done:
            confirmed = True
            reason = "exited"
            outcome = "exited"
            result_exit_code = exit_code
        else:
            confirmed = False
            if job_status == "unknown":
                reason = "job-accounting-unavailable"
                outcome = "exited"
                result_exit_code = exit_code
            elif job_status == "active":
                reason = "job-active-processes"
                outcome = "exited"
                result_exit_code = exit_code
            else:
                reason = "io-drain-incomplete"
                outcome = "exited"
                result_exit_code = exit_code
        return CompletedRuntimeProcess(
            exit_code=result_exit_code,
            stdout=_decode(stdout_chunks),
            stderr=_decode(stderr_chunks),
            duration_seconds=time.monotonic() - start,
            process_stop_evidence=_evidence(
                lifecycle,
                confirmed=confirmed,
                reason=reason,
            ),
            outcome=outcome,
            root_identity=identity,
        )
    finally:
        if stdout_write is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(stdout_write)
        if stderr_write is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(stderr_write)
        if stdout_reader is not None and stdout_thread is None:
            with contextlib.suppress(Exception):
                stdout_reader.close()
        if stderr_reader is not None and stderr_thread is None:
            with contextlib.suppress(Exception):
                stderr_reader.close()
        if thread_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(thread_handle)
        if process_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(process_handle)
        if job_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(job_handle)
