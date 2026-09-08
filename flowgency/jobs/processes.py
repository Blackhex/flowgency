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

    created_at, status = _read_posix_process_created_at(pid)
    if status != "ok" or created_at is None:
        return None
    return RuntimeProcessIdentity(pid=pid, created_at=created_at)


def _parse_proc_stat_created_at(text: str) -> str | None:
    line = text.strip()
    closing = line.rfind(")")
    if closing <= 0:
        return None
    fields = line[closing + 1 :].strip().split()
    if len(fields) < 20:
        return None
    return fields[19]


def _read_posix_process_created_at(pid: int) -> tuple[str | None, Literal["ok", "exited", "unknown"]]:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "exited"
    except PermissionError:
        return None, "unknown"
    except OSError:
        return None, "unknown"
    created_at = _parse_proc_stat_created_at(text)
    if created_at is None:
        return None, "unknown"
    return created_at, "ok"


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
    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, stdout_chunks))
    stderr_thread = threading.Thread(target=_reader, args=(process.stderr, stderr_chunks))
    stdout_thread.start()
    stderr_thread.start()
    deadline = start + timeout
    try:
        exit_code = process.wait(timeout=timeout)
        group_status = _group_exit_status(process.pid, deadline)
        readers_done = _join_reader_threads(stdout_thread, stderr_thread, deadline=deadline)
        if group_status == "empty" and readers_done and identity is not None:
            confirmed = True
            reason = "exited"
        elif group_status == "unknown" or identity is None:
            confirmed = False
            reason = "group-state-unavailable"
        elif group_status == "active":
            confirmed = False
            reason = "descendants-still-running"
        else:
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
            outcome="exited",
            root_identity=identity,
        )
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        wait_deadline = time.monotonic() + 5
        group_status = _group_exit_status(process.pid, wait_deadline)
        readers_done = _join_reader_threads(stdout_thread, stderr_thread, deadline=wait_deadline)
        if group_status == "empty" and readers_done and identity is not None:
            confirmed = True
            reason = "timeout"
        elif group_status == "unknown" or identity is None:
            confirmed = False
            reason = "group-state-unavailable"
        elif group_status == "active":
            confirmed = False
            reason = "descendants-still-running"
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

        stdout_thread = threading.Thread(target=_reader, args=(stdout_reader, stdout_chunks))
        stderr_thread = threading.Thread(target=_reader, args=(stderr_reader, stderr_chunks))
        stdout_thread.start()
        stderr_thread.start()

        win32process.ResumeThread(thread_handle)
        win32api.CloseHandle(stdout_write)
        win32api.CloseHandle(stderr_write)
        stdout_write = None
        stderr_write = None

        if win32event.WaitForSingleObject(process_handle, _remaining_millis(deadline)) == win32con.WAIT_TIMEOUT:
            win32job.TerminateJobObject(job_handle, 124)
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

        exit_code = win32process.GetExitCodeProcess(process_handle)
        job_status = _job_exit_status(job_handle, deadline)
        readers_done = _join_reader_threads(stdout_thread, stderr_thread, deadline=deadline)
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
