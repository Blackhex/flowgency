from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import (
    ConnectedLaunchError,
    ConnectedLaunchError as _ConnectedLaunchError,
    start_connected_process,
)
from flowgency.jobs.processes import RuntimeProcessLifecycle
from flowgency.jobs.windows_connected_process import WindowsConnectedProcess
from flowgency.jobs.windows_job import WindowsJobLaunchError, WindowsJobOwner

from tests.test_connected_process import _read_until


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows connected PTY adapter is Windows-specific")


if os.name == "nt":
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32job
    import win32process


def _native_python_launch() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    env["__PYVENV_LAUNCHER__"] = sys.executable
    return win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0), env


def _stop(process, name: str):
    return process.stop(RuntimeProcessLifecycle("setup", name))


def _write_script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _wait_for_text(path: Path, *, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {path}")


def _wait_for_process_exit(pid: int, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except pywintypes.error:
            return True
        try:
            if win32process.GetExitCodeProcess(handle) != win32con.STILL_ACTIVE:
                return True
        finally:
            win32api.CloseHandle(handle)
        time.sleep(0.05)
    return False


def _wait_for_handle_exit(process_handle, *, timeout: float = 5.0) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        wait_status = win32event.WaitForSingleObject(process_handle, remaining_ms)
        if wait_status == win32event.WAIT_OBJECT_0:
            exit_code = win32process.GetExitCodeProcess(process_handle)
            if exit_code != win32con.STILL_ACTIVE:
                return int(exit_code)
        if wait_status != win32event.WAIT_TIMEOUT:
            break
        time.sleep(0.05)
    return None


def _read_visible_until(process, expected: bytes, *, timeout: float = 20.0) -> bytes:
    ansi = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = process.read()
        if not chunk:
            break
        buffer.extend(chunk)
        visible = ansi.sub(b"", bytes(buffer))
        if expected in visible:
            return visible
    raise AssertionError(f"Timed out waiting for visible output {expected!r}; output was {bytes(buffer)!r}")


def test_windows_connected_process_streams_output_and_confirms_nested_job_membership(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    script = _write_script(
        tmp_path / "nested_connected.py",
        (
            "import os, subprocess, sys, time\n"
            "print('helper-child-ready', flush=True)\n"
            f"python = {native_python!r}\n"
            "env = os.environ.copy()\n"
            "env['__PYVENV_LAUNCHER__'] = sys.executable\n"
            "grandchild = subprocess.Popen([python, '-c', 'import time; time.sleep(60)'], env=env)\n"
            "print(f'grandchild-pid:{grandchild.pid}', flush=True)\n"
            "data = sys.stdin.buffer.readline()\n"
            "sys.stdout.buffer.write(data)\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(60)\n"
        ),
    )
    launch = RuntimeLaunch((native_python, "-u", str(script)), tmp_path, native_env, "connected")
    process = start_connected_process(launch)
    try:
        assert process.alive() is True
        assert getattr(process, "_owner").contains(process.pid)
        visible = _read_visible_until(process, b"grandchild-pid:")
        assert b"helper-child-ready" in visible
        grandchild_pid = int(re.search(rb"grandchild-pid:(\d+)\r?\n", visible).group(1))
        assert getattr(process, "_owner").contains(grandchild_pid)
        payload = "zażółć\n".encode("utf-8")
        process.write(payload[:3])
        process.write(payload[3:])
        assert payload.rstrip() in _read_visible_until(process, payload.rstrip())
    finally:
        evidence = _stop(process, "windows-stream")
        assert evidence.confirmed


def test_windows_connected_process_reports_known_exit_code(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    process = start_connected_process(launch)
    try:
        assert b"connected" in _read_until(process, rb"connected").group(0)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and process.exit_code() is None:
            time.sleep(0.05)
        assert process.exit_code() == 0
    finally:
        assert process.stop(RuntimeProcessLifecycle("setup", "windows-test")).confirmed


def test_windows_failed_launch_cleanup_combines_original_job_and_native_teardown(tmp_path: Path, monkeypatch):
    import flowgency.jobs.connected_process as connected_process
    import flowgency.jobs.windows_job as windows_job
    from flowgency.jobs.windows_conpty import WindowsConPTY

    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch((native_python, "-u", "-c", "pass"), tmp_path, native_env, "connected")
    original_query = windows_job._job_exit_status
    original_wait = WindowsConPTY.wait_closed
    blocked_console = True

    def wait_closed(terminal, deadline):
        if blocked_console:
            return False
        return original_wait(terminal, deadline)

    monkeypatch.setattr(connected_process, "_STOP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(WindowsJobOwner, "contains", lambda self, pid: False)
    monkeypatch.setattr(windows_job, "_job_exit_status", lambda job, deadline: "unknown")
    monkeypatch.setattr(WindowsConPTY, "wait_closed", wait_closed)
    try:
        with pytest.raises(ConnectedLaunchError) as raised:
            WindowsConnectedProcess.spawn(launch, rows=24, cols=80)

        assert raised.value.cleanup_confirmed is False
        cleanup = raised.value._cleanup
        assert cleanup is not None
        original_owner = raised.value.__cause__._cleanup_owners[0]
        assert original_owner in windows_job._uncertain
        assert cleanup.stop(RuntimeProcessLifecycle("setup", "job-unknown")).confirmed is False

        monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
        console_pending = cleanup.stop(RuntimeProcessLifecycle("setup", "console-pending"))

        assert console_pending.confirmed is False
        assert original_owner in windows_job._uncertain
        assert cleanup._terminal._input_write != 0
        assert cleanup._terminal._output_read != 0

        blocked_console = False
        complete = cleanup.stop(RuntimeProcessLifecycle("setup", "cleanup-complete"))

        assert complete.confirmed is True
        assert windows_job._uncertain == []
        assert cleanup._terminal.pseudoconsole == 0
        assert not cleanup._reader_thread.is_alive()
        assert cleanup._reader_active is False
        assert cleanup._ops_in_flight == 0
        assert (
            cleanup._terminal._input_read,
            cleanup._terminal._input_write,
            cleanup._terminal._output_read,
            cleanup._terminal._output_write,
        ) == (0, 0, 0, 0)
        connected_process._retry_unconfirmed()
    finally:
        blocked_console = False
        monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
        monkeypatch.setattr(WindowsConPTY, "wait_closed", original_wait)
        windows_job._retry_uncertain(time.monotonic() + 5.0)
        connected_process._retry_unconfirmed()


@pytest.mark.parametrize("before_resume", [False, True])
def test_windows_native_thread_close_failures_retain_exact_cleanup(tmp_path: Path, monkeypatch, before_resume):
    import flowgency.jobs.connected_process as connected_process
    import flowgency.jobs.windows_job as windows_job

    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch((native_python, "-u", "-c", "pass"), tmp_path, native_env, "connected")
    real_create = windows_job.create_suspended_conpty_process
    real_close = win32api.CloseHandle
    thread_value = None
    original_process = None
    closed_thread = False
    blocked = True
    cleanup = None

    def create(*args):
        nonlocal thread_value, original_process
        result = real_create(*args)
        thread_value = result[1]
        original_process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.SYNCHRONIZE, False, result[2])
        return result

    def close(handle):
        nonlocal closed_thread
        if thread_value is not None and int(handle) == thread_value:
            if blocked:
                raise OSError("thread handle close unavailable")
            result = real_close(handle)
            closed_thread = True
            return result
        return real_close(handle)

    monkeypatch.setattr(windows_job, "create_suspended_conpty_process", create)
    monkeypatch.setattr(win32api, "CloseHandle", close)
    if before_resume:
        monkeypatch.setattr(WindowsJobOwner, "contains", lambda self, pid: False)
    try:
        if before_resume:
            with pytest.raises(ConnectedLaunchError) as raised:
                WindowsConnectedProcess.spawn(launch, rows=24, cols=80)
            assert raised.value.cleanup_confirmed is False
            cleanup = raised.value._cleanup
            assert cleanup is not None
        else:
            cleanup = WindowsConnectedProcess.spawn(launch, rows=24, cols=80)
            assert cleanup.stop(RuntimeProcessLifecycle("setup", "thread-close-blocked")).confirmed is False

        assert _wait_for_handle_exit(original_process, timeout=1.0) is not None
        assert closed_thread is False
        blocked = False
        assert cleanup.stop(RuntimeProcessLifecycle("setup", "thread-close-retry")).confirmed is True
        assert closed_thread is True
        assert windows_job._uncertain == []
        connected_process._retry_unconfirmed()
    finally:
        blocked = False
        if cleanup is not None:
            cleanup.stop(RuntimeProcessLifecycle("setup", "thread-close-finalize"))
        windows_job._retry_uncertain(time.monotonic() + 5.0)
        connected_process._retry_unconfirmed()
        if thread_value is not None and not closed_thread:
            real_close(thread_value)
        if original_process is not None:
            real_close(original_process)


def test_windows_connected_process_launch_failure_propagates_confirmed_cleanup(tmp_path: Path, monkeypatch):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_launch(argv, cwd, env, pseudoconsole):
        del argv, cwd, env, pseudoconsole
        raise WindowsJobLaunchError("native launch failed", cleanup_confirmed=True)

    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(fail_launch))

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is True


def test_windows_connected_process_launch_failure_propagates_unconfirmed_cleanup(
    tmp_path: Path, monkeypatch
):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_launch(argv, cwd, env, pseudoconsole):
        del argv, cwd, env, pseudoconsole
        raise WindowsJobLaunchError("native launch failed", cleanup_confirmed=False)

    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(fail_launch))

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is False
    assert isinstance(raised.value.__cause__, WindowsJobLaunchError)
    assert raised.value.__cause__.cleanup_confirmed is False


def test_windows_connected_process_launch_failure_from_open_cleans_up(
    tmp_path: Path, monkeypatch
):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_open(self):
        del self
        raise OSError("native terminal open failed")

    monkeypatch.setattr("flowgency.jobs.windows_connected_process.WindowsConPTY.open", fail_open)

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is True


def test_windows_generic_job_launch_failure_does_not_infer_unknown_ownership_clean(tmp_path: Path, monkeypatch):
    import flowgency.jobs.connected_process as connected_process

    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch((native_python, "-u", "-c", "pass"), tmp_path, native_env, "connected")

    def fail_launch(*args):
        raise RuntimeError("unknown native ownership")

    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(fail_launch))
    try:
        with pytest.raises(ConnectedLaunchError) as raised:
            WindowsConnectedProcess.spawn(launch, rows=24, cols=80)

        assert raised.value.cleanup_confirmed is False
        assert raised.value._cleanup is None
        assert isinstance(raised.value.__cause__, RuntimeError)
    finally:
        connected_process._retry_unconfirmed()


def test_windows_connected_process_launch_failure_passthrough_stays_unconfirmed(
    tmp_path: Path, monkeypatch
):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_launch(argv, cwd, env, pseudoconsole):
        del argv, cwd, env, pseudoconsole
        raise WindowsJobLaunchError("native launch failed", cleanup_confirmed=False)

    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(fail_launch))

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is False


@pytest.mark.parametrize(
    ("launch", "rows", "cols", "error_type", "match"),
    [
        (RuntimeLaunch((), Path("C:/"), {}, "connected"), 24, 80, ValueError, "command"),
        (RuntimeLaunch(("",), Path("C:/"), {}, "connected"), 24, 80, ConnectedLaunchError, "executable"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {}, "headless"), 24, 80, ValueError, "connected"),
        (RuntimeLaunch(("copilot",), Path("relative"), {}, "connected"), 24, 80, ConnectedLaunchError, "cwd"),
        (
            RuntimeLaunch(("copilot",), Path("C:/definitely-missing-flowgency-connected"), {}, "connected"),
            24,
            80,
            ConnectedLaunchError,
            "cwd",
        ),
        (RuntimeLaunch(("co\x00pilot",), Path("C:/"), {}, "connected"), 24, 80, ConnectedLaunchError, "NUL"),
        (RuntimeLaunch(("copilot", 3), Path("C:/"), {}, "connected"), 24, 80, ConnectedLaunchError, "string"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {"": "value"}, "connected"), 24, 80, ConnectedLaunchError, "invalid entry"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {"BAD=KEY": "value"}, "connected"), 24, 80, ConnectedLaunchError, "invalid entry"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {"BAD\x00KEY": "value"}, "connected"), 24, 80, ConnectedLaunchError, "invalid entry"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {"SAFE": "value\x00suffix"}, "connected"), 24, 80, ConnectedLaunchError, "invalid entry"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {"SAFE": 7}, "connected"), 24, 80, ConnectedLaunchError, "string"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {}, "connected"), 0, 80, ValueError, "size"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {}, "connected"), 24, 0, ValueError, "size"),
        (RuntimeLaunch(("copilot",), Path("C:/"), {}, "connected"), True, 80, ValueError, "size"),
    ],
    ids=[
        "empty-argv",
        "empty-executable",
        "wrong-mode",
        "relative-cwd",
        "missing-cwd",
        "nul-argv",
        "nonstring-argv",
        "empty-env-key",
        "equals-env-key",
        "nul-env-key",
        "nul-env-value",
        "nonstring-env-value",
        "zero-rows",
        "zero-cols",
        "bool-rows",
    ],
)
def test_windows_invalid_launch_is_rejected_before_native_allocation(
    tmp_path: Path,
    monkeypatch,
    launch: RuntimeLaunch,
    rows: int,
    cols: int,
    error_type: type[BaseException],
    match: str,
):
    opened: list[tuple[int, int]] = []
    launched: list[tuple[tuple[object, ...], Path, dict[str, object], object]] = []

    def record_open(self):
        opened.append((self._rows, self._cols))

    def record_launch(argv, cwd, env, pseudoconsole):
        launched.append((argv, cwd, env, pseudoconsole))
        raise AssertionError("launch_conpty must not run for invalid input")

    monkeypatch.setattr("flowgency.jobs.windows_connected_process.WindowsConPTY.open", record_open)
    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(record_launch))

    with pytest.raises(error_type, match=match):
        start_connected_process(launch, rows=rows, cols=cols)

    assert opened == []
    assert launched == []


def test_windows_connected_process_root_exit_keeps_job_alive_until_stop(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    child_pid_path = tmp_path / "contained-child.pid"
    script = _write_script(
        tmp_path / "contained_child.py",
        (
            "import os, pathlib, subprocess, sys, time\n"
            f"python = {native_python!r}\n"
            "env = os.environ.copy()\n"
            "env['__PYVENV_LAUNCHER__'] = sys.executable\n"
            "grandchild = subprocess.Popen([python, '-c', 'import time; time.sleep(120)'], env=env)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(grandchild.pid), encoding='utf-8')\n"
        ),
    )
    launch = RuntimeLaunch(
        (native_python, "-u", str(script), str(child_pid_path)),
        tmp_path,
        native_env,
        "connected",
    )
    process = start_connected_process(launch)
    child_pid: int | None = None
    try:
        child_pid = int(_wait_for_text(child_pid_path))
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and getattr(process, "_owner").exit_code() is None:
            time.sleep(0.05)
        assert getattr(process, "_owner").exit_code() == 0
        assert process.alive() is True
        assert process.exit_code() is None
        assert getattr(process, "_owner").contains(child_pid) is True
    finally:
        evidence = _stop(process, "windows-root-exit")
        assert evidence.confirmed
        assert process.alive() is False
        assert child_pid is not None
        assert _wait_for_process_exit(child_pid)


def test_windows_connected_process_natural_eof_returns_empty_bytes(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    process = start_connected_process(launch)
    try:
        assert b"connected" in _read_until(process, rb"connected").group(0)
        deadline = time.monotonic() + 10.0
        while process.alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert process.alive() is False
        assert process.exit_code() == 0
        drained = bytearray()
        while time.monotonic() < deadline:
            chunk = process.read()
            if not chunk:
                break
            drained.extend(chunk)
        assert process.read() == b""
        assert b"connected" not in drained
    finally:
        evidence = _stop(process, "windows-ready-eof")
        assert evidence.confirmed


class _BlockingOwner:
    def __init__(self):
        self.pid = 4321
        self.stop_calls = 0
        self.closed = False
        self.results = [(True, "stopped")]

    def contains(self, pid: int) -> bool:
        return pid == self.pid

    def stop(self, deadline: float) -> tuple[bool, str]:
        del deadline
        self.stop_calls += 1
        index = min(self.stop_calls - 1, len(self.results) - 1)
        return self.results[index]

    def alive(self) -> bool:
        return self.stop_calls == 0

    def close_confirmed(self) -> None:
        self.closed = True


class _StubTerminal:
    def __init__(self, *, read_results: list[bytes | None] | None = None, wait_closed: bool = True):
        self.read_results = deque(read_results or [])
        self.wait_closed_result = wait_closed
        self.writes: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.begin_close_calls = 0
        self.close_streams_calls = 0

    def read(self, size: int = 65536):
        del size
        if self.read_results:
            return self.read_results.popleft()
        return None

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def resize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def close_child_ends(self) -> None:
        return None

    def begin_close(self) -> None:
        self.begin_close_calls += 1

    def wait_closed(self, deadline: float) -> bool:
        del deadline
        return self.wait_closed_result

    def close_streams(self) -> None:
        self.close_streams_calls += 1


class _BlockingTerminal(_StubTerminal):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.released = threading.Event()

    def read(self, size: int = 65536):
        del size
        self.entered.set()
        self.released.wait(30.0)
        return b""


class _ErrorTerminal(_StubTerminal):
    def __init__(self, message: str, *, wait_closed: bool = True):
        super().__init__(wait_closed=wait_closed)
        self.message = message

    def read(self, size: int = 65536):
        del size
        raise OSError(self.message)


class _BlockingWriteTerminal(_StubTerminal):
    def __init__(self, *, wait_closed: bool = True):
        super().__init__(wait_closed=wait_closed)
        self.entered = threading.Event()
        self.released = threading.Event()

    def write(self, data: bytes) -> None:
        self.entered.set()
        self.released.wait(30.0)
        super().write(data)


class _NaturalExitOwner(_BlockingOwner):
    def __init__(self):
        super().__init__()
        self._alive = False

    def alive(self) -> bool:
        return self._alive

    def exit_code(self) -> int | None:
        return 0


class _BlockingResizeTerminal(_StubTerminal):
    def __init__(self, *, wait_closed: bool = True):
        super().__init__(wait_closed=wait_closed)
        self.allow_close_checks = threading.Event()
        self.resize_entered = threading.Event()
        self.resize_released = threading.Event()
        self.close_started = threading.Event()

    def read(self, size: int = 65536):
        del size
        self.allow_close_checks.wait(30.0)
        if self.close_started.is_set():
            return b""
        return None

    def resize(self, rows: int, cols: int) -> None:
        self.resize_entered.set()
        self.resize_released.wait(30.0)
        super().resize(rows, cols)

    def begin_close(self) -> None:
        if self.close_started.is_set():
            return
        super().begin_close()
        self.close_started.set()


class _CloseStartingTerminal(_StubTerminal):
    def __init__(self, *, wait_closed: bool = True):
        super().__init__(wait_closed=wait_closed)
        self.close_started = threading.Event()

    def read(self, size: int = 65536):
        del size
        if self.close_started.is_set():
            return b""
        return None

    def begin_close(self) -> None:
        if self.close_started.is_set():
            return
        super().begin_close()
        self.close_started.set()


class _LaunchOwner(_BlockingOwner):
    def __init__(self, *, confirmed: bool, reason: str = "stopped"):
        super().__init__()
        self.results = [(confirmed, reason)]

    def exit_code(self) -> int | None:
        return 0


def test_windows_reader_start_failure_does_not_prevent_confirmed_stop(monkeypatch):
    owner = _BlockingOwner()
    terminal = _StubTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    original_start = threading.Thread.start

    def fail_start(thread):
        del thread
        raise RuntimeError("reader thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    monkeypatch.setattr("flowgency.jobs.connected_process._STOP_TIMEOUT_SECONDS", 0.05)
    try:
        with pytest.raises(RuntimeError, match="reader thread unavailable"):
            process._start_reader()

        evidence = process.stop(RuntimeProcessLifecycle("setup", "reader-start-failed"))

        assert evidence.confirmed is True
        assert owner.closed is True
        assert terminal.close_streams_calls == 1
    finally:
        monkeypatch.setattr(threading.Thread, "start", original_start)
        if not process._reader_done.is_set():
            terminal.read_results.append(b"")
            process._start_reader()
        process.stop(RuntimeProcessLifecycle("setup", "reader-start-cleanup"))


@pytest.mark.parametrize("failure_stage", ["begin-close", "wait-close", "streams-close"])
def test_windows_cleanup_errors_stay_tracked_until_stop_retry_confirms(monkeypatch, failure_stage):
    import flowgency.jobs.connected_process as connected_process

    class FailingTerminal(_StubTerminal):
        blocked = True

        def begin_close(self):
            if self.blocked and failure_stage == "begin-close":
                raise RuntimeError("private cleanup detail")
            super().begin_close()

        def wait_closed(self, deadline):
            if self.blocked and failure_stage == "wait-close":
                raise RuntimeError("private cleanup detail")
            return super().wait_closed(deadline)

        def close_streams(self):
            if self.blocked and failure_stage == "streams-close":
                raise RuntimeError("private cleanup detail")
            super().close_streams()

    owner = _BlockingOwner()
    terminal = FailingTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    monkeypatch.setattr(connected_process, "_STOP_TIMEOUT_SECONDS", 0.05)
    try:
        first = process.stop(RuntimeProcessLifecycle("setup", "cleanup-error"))

        assert first.confirmed is False
        assert "private cleanup detail" not in first.reason
        assert owner.closed is False
        assert terminal.close_streams_calls == 0
        with pytest.raises(ConnectedLaunchError) as raised:
            connected_process._retry_unconfirmed()
        assert raised.value.cleanup_confirmed is False

        terminal.blocked = False
        retry = process.stop(RuntimeProcessLifecycle("setup", "cleanup-error-retry"))

        assert retry.confirmed is True
        assert owner.closed is True
        assert terminal.close_streams_calls == 1
        connected_process._retry_unconfirmed()
    finally:
        terminal.blocked = False
        process.stop(RuntimeProcessLifecycle("setup", "cleanup-error-finalize"))


def test_windows_connected_process_resize_forwards_to_terminal():
    owner = _BlockingOwner()
    terminal = _StubTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    process.resize(33, 91)

    assert terminal.sizes == [(33, 91)]


def test_windows_stop_retry_recovers_after_reader_error_once_close_is_proven():
    owner = _BlockingOwner()
    terminal = _ErrorTerminal("secret pty/auth detail", wait_closed=False)
    process = WindowsConnectedProcess(terminal, owner)
    process._start_reader()

    deadline = time.monotonic() + 2.0
    while not process._reader_done.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert process._reader_done.is_set()

    first = process.stop(RuntimeProcessLifecycle("setup", "reader-error-close-pending"))
    assert first.confirmed is False
    assert first.reason == "reader-unreleased"
    assert owner.closed is False

    terminal.wait_closed_result = True
    retry = process.stop(RuntimeProcessLifecycle("setup", "reader-error-close-proven"))
    assert retry.confirmed is True
    assert retry.reason == "stopped"
    assert owner.closed is True


def test_windows_read_surfaces_sanitized_reader_failure_without_raw_terminal_detail():
    owner = _BlockingOwner()
    terminal = _ErrorTerminal("secret pty/auth detail")
    process = WindowsConnectedProcess(terminal, owner)
    process._start_reader()
    try:
        deadline = time.monotonic() + 2.0
        while not process._reader_done.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process._reader_done.is_set()

        with pytest.raises(OSError, match="PTY output failed") as raised:
            process.read()
        assert "secret pty/auth detail" not in str(raised.value)
    finally:
        assert process.stop(RuntimeProcessLifecycle("setup", "reader-error-surface")).confirmed is True


def test_windows_stop_retry_recovers_after_console_close_is_later_proven():
    owner = _BlockingOwner()
    terminal = _StubTerminal(wait_closed=False)
    process = WindowsConnectedProcess(terminal, owner)

    first = process.stop(RuntimeProcessLifecycle("setup", "close-pending"))
    assert first.confirmed is False
    assert first.reason == "reader-unreleased"
    assert owner.closed is False
    assert terminal.close_streams_calls == 0

    terminal.wait_closed_result = True
    retry = process.stop(RuntimeProcessLifecycle("setup", "close-proven"))
    assert retry.confirmed is True
    assert retry.reason == "stopped"
    assert owner.closed is True
    assert terminal.close_streams_calls == 1


def test_windows_stop_preserves_handles_while_public_write_is_still_in_flight(monkeypatch):
    owner = _BlockingOwner()
    terminal = _BlockingWriteTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    writer = threading.Thread(target=process.write, args=(b"payload",), daemon=False)
    writer.start()
    assert terminal.entered.wait(1.0)
    monkeypatch.setattr("flowgency.jobs.connected_process._STOP_TIMEOUT_SECONDS", 0.1)

    first = process.stop(RuntimeProcessLifecycle("setup", "write-blocked"))
    assert first.confirmed is False
    assert first.reason == "reader-undrained"
    assert owner.closed is False
    assert terminal.close_streams_calls == 0

    terminal.released.set()
    writer.join(timeout=2.0)
    assert not writer.is_alive()

    retry = process.stop(RuntimeProcessLifecycle("setup", "write-blocked-retry"))
    assert retry.confirmed is True
    assert retry.reason == "stopped"
    assert owner.closed is True
    assert terminal.close_streams_calls == 1


def test_windows_natural_close_defers_begin_close_until_inflight_resize_finishes(monkeypatch):
    owner = _NaturalExitOwner()
    terminal = _BlockingResizeTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    process._start_reader()
    resize_thread = threading.Thread(target=process.resize, args=(33, 91), daemon=False)
    resize_thread.start()

    try:
        assert terminal.resize_entered.wait(1.0)
        terminal.allow_close_checks.set()
        time.sleep(0.05)
        assert terminal.begin_close_calls == 0

        monkeypatch.setattr("flowgency.jobs.connected_process._STOP_TIMEOUT_SECONDS", 0.1)
        first = process.stop(RuntimeProcessLifecycle("setup", "resize-close-race"))
        assert first.confirmed is False
        assert first.reason == "reader-undrained"
        assert owner.closed is False
        assert terminal.close_streams_calls == 0

        terminal.resize_released.set()
        resize_thread.join(timeout=2.0)
        assert not resize_thread.is_alive()
        assert terminal.close_started.wait(1.0)

        deadline = time.monotonic() + 2.0
        while not process._reader_done.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process._reader_done.is_set()

        retry = process.stop(RuntimeProcessLifecycle("setup", "resize-close-race-retry"))
        assert retry.confirmed is True
        assert retry.reason == "stopped"
        assert owner.closed is True
        assert terminal.begin_close_calls == 1
        assert terminal.sizes == [(33, 91)]
    finally:
        # release the stubbed reader gate first so cleanup can't stall on an early assertion failure
        terminal.allow_close_checks.set()
        terminal.resize_released.set()
        resize_thread.join(timeout=2.0)
        with contextlib.suppress(Exception):
            process.stop(RuntimeProcessLifecycle("setup", "resize-close-race-cleanup"))


def test_windows_resize_after_close_start_raises_broken_pipe_without_native_resize():
    owner = _NaturalExitOwner()
    terminal = _CloseStartingTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    process._start_reader()

    try:
        assert terminal.close_started.wait(1.0)
        with pytest.raises(BrokenPipeError):
            process.resize(44, 100)
        assert terminal.sizes == []
        assert terminal.begin_close_calls == 1
    finally:
        with contextlib.suppress(Exception):
            process.stop(RuntimeProcessLifecycle("setup", "close-start-resize-cleanup"))


def test_windows_stop_retry_releases_retained_child_pipe_ends_before_reader_drain(tmp_path: Path, monkeypatch):
    import flowgency.jobs.connected_process as connected_process
    from flowgency.jobs.windows_conpty import WindowsConPTY

    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch((native_python, "-u", "-c", "pass"), tmp_path, native_env, "connected")
    real_close = WindowsConPTY._close_handle
    blocked = True
    cleanup = None
    original_process = None

    def close(terminal, value):
        if value == terminal._output_write and blocked:
            raise OSError("child output close unavailable")
        return real_close(terminal, value)

    monkeypatch.setattr(WindowsConPTY, "_close_handle", close)
    monkeypatch.setattr(connected_process, "_STOP_TIMEOUT_SECONDS", 0.05)
    try:
        with pytest.raises(ConnectedLaunchError) as raised:
            WindowsConnectedProcess.spawn(launch, rows=24, cols=80)
        assert raised.value.cleanup_confirmed is False
        cleanup = raised.value._cleanup
        assert cleanup is not None
        original_process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.SYNCHRONIZE, False, cleanup.pid
        )
        assert cleanup._terminal._output_write != 0
        assert cleanup._owner._closed is False

        blocked = False
        evidence = cleanup.stop(RuntimeProcessLifecycle("setup", "child-output-close-retry"))

        assert evidence.confirmed is True
        assert _wait_for_handle_exit(original_process, timeout=1.0) is not None
        assert cleanup._terminal._output_write == 0
        assert cleanup._terminal._output_read == 0
        assert not cleanup._reader_thread.is_alive()
        assert cleanup._owner._closed is True
        connected_process._retry_unconfirmed()
    finally:
        blocked = False
        if cleanup is not None:
            cleanup._terminal.close_child_ends()
            cleanup.stop(RuntimeProcessLifecycle("setup", "child-output-close-finalize"))
        connected_process._retry_unconfirmed()
        if original_process is not None:
            win32api.CloseHandle(original_process)


def test_windows_connected_process_post_launch_child_end_close_failure_reaps_started_tree(
    tmp_path: Path, monkeypatch
):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    owner = _LaunchOwner(confirmed=True)

    monkeypatch.setattr("flowgency.jobs.windows_connected_process.WindowsConPTY.open", lambda self: None)
    monkeypatch.setattr(
        "flowgency.jobs.windows_connected_process.WindowsConPTY.close_child_ends",
        lambda self: (_ for _ in ()).throw(OSError("close child ends failed")),
    )
    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(lambda *args: owner))

    with pytest.raises(ConnectedLaunchError) as raised:
        WindowsConnectedProcess.spawn(launch, rows=24, cols=80)

    assert raised.value.cleanup_confirmed is True
    assert owner.closed is True


def test_windows_connected_process_post_launch_child_end_close_failure_stays_unconfirmed(
    tmp_path: Path, monkeypatch
):
    import flowgency.jobs.connected_process as connected_process

    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    owner = _LaunchOwner(confirmed=False, reason="job-accounting-unavailable")

    monkeypatch.setattr("flowgency.jobs.windows_connected_process.WindowsConPTY.open", lambda self: None)
    monkeypatch.setattr(
        "flowgency.jobs.windows_connected_process.WindowsConPTY.close_child_ends",
        lambda self: (_ for _ in ()).throw(OSError("close child ends failed")),
    )
    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(lambda *args: owner))

    try:
        with pytest.raises(ConnectedLaunchError) as raised:
            WindowsConnectedProcess.spawn(launch, rows=24, cols=80)

        assert raised.value.cleanup_confirmed is False
        assert owner.closed is False
    finally:
        owner.results = [(True, "stopped")]
        with connected_process._unconfirmed_lock:
            pending = list(connected_process._unconfirmed)
        for process in pending:
            process._stop_tree()


def test_windows_connected_process_bounds_output_to_1mib_with_raw_byte_integrity():
    owner = _BlockingOwner()
    terminal = _StubTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    limit = 65536 * 16
    payload = bytes((index % 251 for index in range(limit + 12345)))

    process._append_output(payload)
    process._stream_ended = True

    drained = bytearray()
    while True:
        chunk = process.read(32768)
        if not chunk:
            break
        drained.extend(chunk)

    assert bytes(drained) == payload[-limit:]


def test_windows_connected_process_preserves_unicode_argv_env_split_input_and_resize(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    native_env["FLOWGENCY_UNICODE_ENV"] = "zażółć-env"
    unicode_arg = "zażółć-argv"
    payload = "gęślą-jaźń\r\n".encode("utf-8")
    argv_path = tmp_path / "argv.txt"
    env_path = tmp_path / "env.txt"
    size0_path = tmp_path / "size0.txt"
    size1_path = tmp_path / "size1.txt"
    input_path = tmp_path / "input.bin"
    script = _write_script(
        tmp_path / "unicode_resize.py",
        (
            "import os, pathlib, sys, time\n"
            "size0 = os.get_terminal_size()\n"
            "pathlib.Path(sys.argv[1]).write_text(sys.argv[6], encoding='utf-8')\n"
            "pathlib.Path(sys.argv[2]).write_text(os.environ['FLOWGENCY_UNICODE_ENV'], encoding='utf-8')\n"
            "pathlib.Path(sys.argv[3]).write_text(f'{size0.lines}x{size0.columns}', encoding='utf-8')\n"
            "data = sys.stdin.buffer.readline()\n"
            "deadline = time.time() + 2\n"
            "size1 = os.get_terminal_size()\n"
            "while f'{size1.lines}x{size1.columns}' != '33x91' and time.time() < deadline:\n"
            "    time.sleep(0.02)\n"
            "    size1 = os.get_terminal_size()\n"
            "pathlib.Path(sys.argv[4]).write_text(f'{size1.lines}x{size1.columns}', encoding='utf-8')\n"
            "pathlib.Path(sys.argv[5]).write_bytes(data)\n"
            "time.sleep(60)\n"
        ),
    )
    launch = RuntimeLaunch(
        (
            native_python,
            "-u",
            str(script),
            str(argv_path),
            str(env_path),
            str(size0_path),
            str(size1_path),
            str(input_path),
            unicode_arg,
        ),
        tmp_path,
        native_env,
        "connected",
    )
    process = start_connected_process(launch)
    try:
        assert _wait_for_text(argv_path) == unicode_arg
        assert _wait_for_text(env_path) == native_env["FLOWGENCY_UNICODE_ENV"]
        assert _wait_for_text(size0_path) == "24x80"
        process.resize(33, 91)
        process.write(payload[:4])
        process.write(payload[4:])
        assert _wait_for_text(size1_path) == "33x91"
        deadline = time.monotonic() + 5.0
        while not input_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert input_path.read_bytes() == payload
    finally:
        evidence = _stop(process, "unicode-resize")
        assert evidence.confirmed


def test_windows_connected_process_preserves_empty_argument_token(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    marker_path = tmp_path / "empty-arg.txt"
    script = _write_script(
        tmp_path / "empty_arg.py",
        (
            "import pathlib, sys, time\n"
            "assert sys.argv[2] == ''\n"
            "pathlib.Path(sys.argv[1]).write_text('preserved', encoding='utf-8')\n"
            "time.sleep(60)\n"
        ),
    )
    launch = RuntimeLaunch(
        (native_python, "-u", str(script), str(marker_path), ""),
        tmp_path,
        native_env,
        "connected",
    )
    process = start_connected_process(launch)
    try:
        assert _wait_for_text(marker_path) == "preserved"
    finally:
        evidence = _stop(process, "empty-arg")
        assert evidence.confirmed


def test_windows_stop_retry_confirms_after_job_accounting_recovers(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('replacement', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    owner = _BlockingOwner()
    owner.results = [
        (False, "job-accounting-unavailable"),
        (False, "job-accounting-unavailable"),
        (True, "stopped"),
    ]
    terminal = _StubTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    evidence = process.stop(RuntimeProcessLifecycle("setup", "job-accounting"))
    assert evidence.confirmed is False
    assert evidence.reason == "job-accounting-unavailable"
    assert owner.closed is False

    with pytest.raises(_ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is False

    retry = process.stop(RuntimeProcessLifecycle("setup", "job-accounting-retry"))
    assert retry.confirmed is True
    assert retry.reason == "stopped"
    assert owner.closed is True
    assert terminal.begin_close_calls == 1

    replacement = start_connected_process(launch)
    try:
        assert b"replacement" in _read_until(replacement, rb"replacement").group(0)
    finally:
        assert _stop(replacement, "job-accounting-replacement").confirmed


@pytest.mark.skipif(os.name != "nt", reason="Windows connected PTY adapter is Windows-specific")
def test_windows_stop_requires_reader_drain_before_confirmation(tmp_path: Path, monkeypatch):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('replacement', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    owner = _BlockingOwner()
    terminal = _BlockingTerminal()
    process = WindowsConnectedProcess(terminal, owner)
    process._reader_done.clear()
    process._reader_thread = threading.Thread(
        target=process._read_terminal_output,
        name="blocked-conpty-reader",
        daemon=False,
    )
    process._reader_thread.start()
    assert terminal.entered.wait(1.0)
    monkeypatch.setattr("flowgency.jobs.connected_process._STOP_TIMEOUT_SECONDS", 0.1)

    evidence = process.stop(RuntimeProcessLifecycle("setup", "blocked-reader"))
    assert evidence.confirmed is False
    assert evidence.reason == "reader-undrained"
    assert owner.closed is False
    assert process._reader_thread is not None and process._reader_thread.is_alive()

    with pytest.raises(_ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is False

    terminal.released.set()
    deadline = time.monotonic() + 2.0
    while process._reader_thread is not None and process._reader_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert process._reader_thread is not None and not process._reader_thread.is_alive()

    retry = process.stop(RuntimeProcessLifecycle("setup", "blocked-reader-retry"))
    assert retry.confirmed is True
    assert owner.closed is True