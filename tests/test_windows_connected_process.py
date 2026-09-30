from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import threading
import time
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
from flowgency.jobs.windows_pty_protocol import FrameType

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
        _read_until(process, rb"helper-child-ready")
        grandchild_pid = int(_read_until(process, rb"grandchild-pid:(\d+)\r?\n").group(1))
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


def test_windows_connected_process_partial_ready_cleans_up(tmp_path: Path, monkeypatch):
    native_python, native_env = _native_python_launch()
    helper_script = _write_script(
        tmp_path / "bogus_helper.py",
        "import sys, time\n"
        "sys.stdout.write('not-framed')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.2)\n",
    )
    monkeypatch.setattr(
        "flowgency.jobs.windows_connected_process._helper_launch",
        lambda: ((native_python, "-u", str(helper_script)), native_env),
    )
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is True


def test_windows_connected_process_assign_failure_propagates_confirmed_cleanup(
    tmp_path: Path, monkeypatch
):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_assign(job, process):
        del job, process
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", fail_assign)

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is True
    assert isinstance(raised.value.__cause__, Exception)
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert getattr(raised.value.__cause__, "cleanup_confirmed", None) is True


def test_windows_connected_process_assign_failure_with_unproven_cleanup_blocks_replacement(
    tmp_path: Path, monkeypatch
):
    from flowgency.jobs.windows_job import _retry_uncertain, _uncertain

    assert _uncertain == []
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_assign(job, process):
        del job, process
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def timeout_wait(process, timeout_ms):
        del process, timeout_ms
        return win32event.WAIT_TIMEOUT

    replacement = None
    try:
        with monkeypatch.context() as m:
            m.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            m.setattr(win32event, "WaitForSingleObject", timeout_wait)

            with pytest.raises(ConnectedLaunchError) as raised:
                start_connected_process(launch)
            assert raised.value.cleanup_confirmed is False

            with pytest.raises(ConnectedLaunchError) as blocked:
                start_connected_process(launch)
            assert blocked.value.cleanup_confirmed is False

        assert len(_uncertain) == 1
        _retry_uncertain(time.monotonic() + 5)
        assert _uncertain == []

        replacement = start_connected_process(launch)
        assert replacement.alive() is True
    finally:
        while _uncertain:
            entry = _uncertain[0]
            with contextlib.suppress(Exception):
                win32process.TerminateProcess(entry.process_handle, 1)
            with contextlib.suppress(Exception):
                win32event.WaitForSingleObject(entry.process_handle, 5000)
            with contextlib.suppress(Exception):
                _retry_uncertain(time.monotonic() + 5)
        if replacement is not None:
            assert _stop(replacement, "windows-unproven-launch").confirmed


def test_windows_connected_process_retry_probe_failure_stays_unconfirmed(
    tmp_path: Path, monkeypatch
):
    from flowgency.jobs.windows_job import _retry_uncertain, _uncertain

    assert _uncertain == []
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('connected', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )

    def fail_assign(job, process):
        del job, process
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def signaled_wait(process, timeout_ms):
        del process, timeout_ms
        return win32event.WAIT_OBJECT_0

    def fail_exit_code(process):
        del process
        raise pywintypes.error(6, "GetExitCodeProcess", "The handle is invalid.")

    replacement = None
    cleanup_error = None
    completed_assertions = False
    try:
        with monkeypatch.context() as m:
            m.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            m.setattr(win32event, "WaitForSingleObject", signaled_wait)
            m.setattr(win32process, "GetExitCodeProcess", fail_exit_code)

            with pytest.raises(ConnectedLaunchError) as raised:
                start_connected_process(launch)
            assert raised.value.cleanup_confirmed is False

        assert len(_uncertain) == 1

        with monkeypatch.context() as m:
            m.setattr(win32event, "WaitForSingleObject", signaled_wait)
            m.setattr(win32process, "GetExitCodeProcess", fail_exit_code)

            with pytest.raises(ConnectedLaunchError) as blocked:
                start_connected_process(launch)
            assert blocked.value.cleanup_confirmed is False

        entry = _uncertain[0]
        exit_code = _wait_for_handle_exit(entry.process_handle, timeout=5)
        assert exit_code is not None
        assert exit_code != win32con.STILL_ACTIVE

        _retry_uncertain(time.monotonic() + 5)
        assert _uncertain == []

        replacement = start_connected_process(launch)
        assert replacement.alive() is True
        completed_assertions = True
    finally:
        if replacement is not None:
            assert _stop(replacement, "windows-retry-probe-failure").confirmed
        if _uncertain:
            entry = _uncertain[0]
            try:
                if win32event.WaitForSingleObject(entry.process_handle, 0) == win32event.WAIT_TIMEOUT:
                    win32process.TerminateProcess(entry.process_handle, 1)
                win32event.WaitForSingleObject(entry.process_handle, 5000)
                _retry_uncertain(time.monotonic() + 5)
            except Exception as error:
                cleanup_error = error
    if cleanup_error is not None and completed_assertions:
        raise cleanup_error


def test_windows_connected_process_helper_exit_after_ready_keeps_job_alive_until_stop(
    tmp_path: Path, monkeypatch
):
    native_python, native_env = _native_python_launch()
    child_pid_path = tmp_path / "contained-child.pid"
    child_script = _write_script(
        tmp_path / "contained_child.py",
        (
            "import os, pathlib, sys, time\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(120)\n"
        ),
    )
    helper_script = _write_script(
        tmp_path / "ready_then_exit_helper.py",
        (
            "import json, subprocess, sys\n"
            "from flowgency.jobs.windows_pty_protocol import FrameType, decode_start, read_frame, write_frame\n"
            "frame = read_frame(sys.stdin.buffer)\n"
            "if frame is None or frame[0] is not FrameType.START:\n"
            "    raise SystemExit(2)\n"
            "launch, _rows, _cols = decode_start(frame[1])\n"
            "child = subprocess.Popen(\n"
            "    list(launch.argv),\n"
            "    cwd=str(launch.cwd),\n"
            "    env=dict(launch.env),\n"
            "    stdin=subprocess.DEVNULL,\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            "    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),\n"
            ")\n"
            "payload = json.dumps({'pid': child.pid}, separators=(',', ':')).encode('utf-8')\n"
            "write_frame(sys.stdout.buffer, FrameType.READY, payload)\n"
            "sys.stdout.flush()\n"
        ),
    )
    monkeypatch.setattr(
        "flowgency.jobs.windows_connected_process._helper_launch",
        lambda: ((native_python, "-u", str(helper_script)), native_env),
    )
    launch = RuntimeLaunch(
        (native_python, "-u", str(child_script), str(child_pid_path)),
        tmp_path,
        native_env,
        "connected",
    )
    process = start_connected_process(launch)
    child_pid: int | None = None
    try:
        child_pid = int(_wait_for_text(child_pid_path))
        assert process.read() == b""
        assert process.alive() is True
        assert process.exit_code() is None
        assert getattr(process, "_owner").contains(child_pid) is True
        reader = getattr(process, "_reader_thread")
        assert reader is not None
        reader.join(timeout=2.0)
        assert reader.is_alive() is False
    finally:
        evidence = _stop(process, "windows-ready-eof")
        assert evidence.confirmed
        assert process.alive() is False
        assert child_pid is not None
        assert _wait_for_process_exit(child_pid)


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


class _BlockingFrameReader:
    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()

    def read(self, *, deadline: float | None = None):
        del deadline
        self.entered.set()
        self.released.wait(30.0)
        return None


def test_windows_connected_process_resize_writes_resize_frame(tmp_path: Path, monkeypatch):
    owner = _BlockingOwner()
    input_path = tmp_path / "input.bin"
    output_path = tmp_path / "output.bin"
    input_path.write_bytes(b"")
    output_path.write_bytes(b"")
    recorded: list[tuple[FrameType, bytes]] = []

    def capture_frame(stream, kind, payload):
        del stream
        recorded.append((kind, bytes(payload)))

    monkeypatch.setattr("flowgency.jobs.windows_connected_process.write_frame", capture_frame)

    with input_path.open("wb", buffering=0) as input_stream, output_path.open("rb", buffering=0) as output_stream:
        process = WindowsConnectedProcess(owner, input_stream, output_stream)
        process.resize(33, 91)

    assert recorded == [(FrameType.RESIZE, b'{"rows":33,"cols":91}')]


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
    input_path = tmp_path / "input.bin"
    output_path = tmp_path / "output.bin"
    input_path.write_bytes(b"")
    output_path.write_bytes(b"")

    with input_path.open("wb", buffering=0) as input_stream, output_path.open("rb", buffering=0) as output_stream:
        process = WindowsConnectedProcess(owner, input_stream, output_stream)
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

    replacement = start_connected_process(launch)
    try:
        assert b"replacement" in _read_until(replacement, rb"replacement").group(0)
    finally:
        assert _stop(replacement, "job-accounting-replacement").confirmed


@pytest.mark.skipif(os.name != "nt", reason="Windows connected PTY adapter is Windows-specific")
def test_windows_stop_requires_reader_drain_before_confirmation(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch(
        (native_python, "-u", "-c", "print('replacement', flush=True)"),
        tmp_path,
        native_env,
        "connected",
    )
    owner = _BlockingOwner()
    input_path = tmp_path / "input.bin"
    output_path = tmp_path / "output.bin"
    input_path.write_bytes(b"")
    output_path.write_bytes(b"")
    with input_path.open("wb", buffering=0) as input_stream, output_path.open("rb", buffering=0) as output_stream:
        process = WindowsConnectedProcess(owner, input_stream, output_stream)
        blocking_reader = _BlockingFrameReader()
        process._frame_reader = blocking_reader
        process._reader_thread = threading.Thread(target=process._read_frames, name="blocked-frame-reader", daemon=False)
        process._reader_thread.start()
        assert blocking_reader.entered.wait(1.0)

        evidence = process.stop(RuntimeProcessLifecycle("setup", "blocked-reader"))
        assert evidence.confirmed is False
        assert evidence.reason == "reader-undrained"
        assert owner.closed is False
        assert process._reader_thread is not None and process._reader_thread.is_alive()

        with pytest.raises(_ConnectedLaunchError) as raised:
            start_connected_process(launch)
        assert raised.value.cleanup_confirmed is False

        blocking_reader.released.set()
        deadline = time.monotonic() + 2.0
        while process._reader_thread is not None and process._reader_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process._reader_thread is not None and not process._reader_thread.is_alive()

        retry = process.stop(RuntimeProcessLifecycle("setup", "blocked-reader-retry"))
        assert retry.confirmed is True
        assert owner.closed is True