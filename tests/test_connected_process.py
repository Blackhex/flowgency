from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

from flowgency.integrations.errors import IntegrationError
from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.processes import RuntimeProcessLifecycle, process_identity_state, read_process_identity
from flowgency.jobs.connected_process import (
    ConnectedLaunchError,
    connected_process_available,
    start_connected_process,
)


_POSIX_PROC = os.name != "nt" and Path("/proc/self/stat").is_file()
_SUPPORTED_HOST = os.name == "nt" or _POSIX_PROC
_DEVICE_ATTRIBUTES_QUERY = b"\x1b[c"


def _interpreter() -> tuple[str, dict[str, str]]:
    if os.name == "nt":
        from tests.test_runtime_process_lifecycle import _native_python_launch

        return _native_python_launch()
    return sys.executable, os.environ.copy()


def _read_until(process, pattern: bytes, *, timeout: float = 20.0) -> re.Match[bytes]:
    """Drain terminal output until `pattern` appears, answering ConPTY's DA1 query like a terminal."""
    buffer = bytearray()
    found: list[re.Match[bytes]] = []

    def pump() -> None:
        answered = False
        while True:
            chunk = process.read()
            if not chunk:
                return
            buffer.extend(chunk)
            if not answered and _DEVICE_ATTRIBUTES_QUERY in buffer:
                process.write(b"\x1b[?1;0c")
                answered = True
            match = re.search(pattern, bytes(buffer))
            if match:
                found.append(match)
                return

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    reader.join(timeout)
    if not found:
        raise AssertionError(f"Timed out waiting for {pattern!r}; output was {bytes(buffer)!r}")
    return found[0]


def _stop(process, name: str):
    return process.stop(RuntimeProcessLifecycle("setup", name))


def test_connected_process_rejects_headless_launch(tmp_path):
    launch = RuntimeLaunch((sys.executable,), tmp_path, os.environ.copy(), "headless")
    with pytest.raises(ValueError, match="connected"):
        start_connected_process(launch)


def test_connected_process_rejects_invalid_terminal_size(tmp_path):
    launch = RuntimeLaunch((sys.executable,), tmp_path, os.environ.copy(), "connected")
    with pytest.raises(ValueError, match="size"):
        start_connected_process(launch, rows=0, cols=80)


def test_connected_process_rejects_nul_in_environment_before_spawning(tmp_path):
    env = {"SAFE": "1", "INJECTED": "x\0EXTRA=1"}
    launch = RuntimeLaunch((sys.executable, "-c", "pass"), tmp_path, env, "connected")
    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is True


@pytest.mark.skipif(not _SUPPORTED_HOST, reason="connected PTY needs ConPTY or POSIX /proc")
def test_connected_process_available_on_supported_host():
    assert connected_process_available() is True


def test_connected_process_unavailable_without_pty_library(monkeypatch):
    monkeypatch.setitem(sys.modules, "winpty" if os.name == "nt" else "ptyprocess", None)
    assert connected_process_available() is False


@pytest.mark.skipif(os.name == "nt" or not Path("/proc/self/stat").is_file(), reason="POSIX /proc PTY check")
def test_posix_connected_process_has_tty_and_accepts_input(tmp_path):
    command = (sys.executable, "-u", "-c", "import sys; print(sys.stdin.isatty(), flush=True); print(input(), flush=True)")
    launch = RuntimeLaunch(command, tmp_path, os.environ.copy(), "connected")
    process = start_connected_process(launch)
    try:
        _read_until(process, rb"True")
        process.resize(30, 100)
        process.write(b"approved\n")
        _read_until(process, rb"approved")
    finally:
        evidence = _stop(process, "posix-test")
        assert evidence.confirmed


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY-only check")
def test_windows_connected_process_has_tty_and_stops(tmp_path):
    executable, env = _interpreter()
    command = (executable, "-u", "-c", "import sys; print(sys.stdin.isatty(), flush=True); print(input(), flush=True)")
    launch = RuntimeLaunch(command, tmp_path, env, "connected")
    process = start_connected_process(launch)
    try:
        _read_until(process, rb"True")
        process.resize(30, 100)
        process.write(b"approved\r")
        _read_until(process, rb"approved")
    finally:
        evidence = _stop(process, "windows-test")
        assert evidence.confirmed
    assert process.alive() is False
    assert process.read() == b""


@pytest.mark.skipif(not _SUPPORTED_HOST, reason="connected PTY needs ConPTY or POSIX /proc")
def test_connected_process_stop_reaps_child_tree(tmp_path):
    executable, env = _interpreter()
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); "
        "print('child-pid:%d' % child.pid,flush=True); time.sleep(120)"
    )
    launch = RuntimeLaunch((executable, "-u", "-c", script), tmp_path, env, "connected")
    process = start_connected_process(launch)
    try:
        child_pid = int(_read_until(process, rb"child-pid:(\d+)\r?\n").group(1))
        identity = read_process_identity(child_pid)
        assert identity is not None
        assert process.alive() is True
        assert process.exit_code() is None
    finally:
        evidence = _stop(process, "tree-test")
    assert evidence.confirmed
    assert evidence.job_id == "setup" and evidence.generation == "tree-test"
    assert process_identity_state(identity) != "alive"
    assert process.alive() is False
    assert process.exit_code() is not None
    assert process.read() == b""
    with pytest.raises(BrokenPipeError):
        process.write(b"late\n")
    with pytest.raises(BrokenPipeError):
        process.resize(30, 100)
    assert _stop(process, "tree-test-again").confirmed is True


@pytest.mark.skipif(not _SUPPORTED_HOST, reason="connected PTY needs ConPTY or POSIX /proc")
def test_connected_process_tree_stays_alive_after_root_exit_until_stopped(tmp_path):
    executable, env = _interpreter()
    # A survivor must ignore the SIGHUP a POSIX session leader's exit sends to its group.
    survivor = (
        "import signal,sys,time; signal.signal(getattr(signal, 'SIGHUP', signal.SIGINT), signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(120)"
    )
    script = (
        "import subprocess,sys; "
        f"child=subprocess.Popen([sys.executable,'-c',{survivor!r}],stdout=subprocess.PIPE); "
        "child.stdout.readline(); print('child-pid:%d' % child.pid,flush=True)"
    )
    launch = RuntimeLaunch((executable, "-u", "-c", script), tmp_path, env, "connected")
    process = start_connected_process(launch)
    try:
        child_pid = int(_read_until(process, rb"child-pid:(\d+)\r?\n").group(1))
        identity = read_process_identity(child_pid)
        assert identity is not None
        time.sleep(1.0)
        assert process.alive() is True
        assert process.exit_code() is None
    finally:
        evidence = _stop(process, "orphan-test")
    assert evidence.confirmed
    assert process_identity_state(identity) != "alive"


@pytest.mark.skipif(not _SUPPORTED_HOST, reason="connected PTY needs ConPTY or POSIX /proc")
def test_unconfirmed_stop_refuses_next_launch_until_cleanup_is_confirmed(tmp_path, monkeypatch):
    executable, env = _interpreter()
    launch = RuntimeLaunch((executable, "-u", "-c", "import time; time.sleep(120)"), tmp_path, env, "connected")
    process = start_connected_process(launch)
    if os.name == "nt":
        monkeypatch.setattr("flowgency.jobs.connected_process._job_exit_status", lambda job, deadline: "active")
    else:
        monkeypatch.setattr(
            "flowgency.jobs.connected_process.terminate_owned_posix_group",
            lambda group, *, timeout: "active",
        )
    try:
        evidence = _stop(process, "unconfirmed")
        assert evidence.confirmed is False
        with pytest.raises(ConnectedLaunchError) as raised:
            start_connected_process(launch)
        assert raised.value.cleanup_confirmed is False
    finally:
        monkeypatch.undo()
    replacement = start_connected_process(launch)
    try:
        assert _stop(process, "unconfirmed").confirmed is True
    finally:
        assert _stop(replacement, "replacement").confirmed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows Job assignment is Windows-specific")
def test_windows_assignment_failure_never_returns_uncontained_process(tmp_path, monkeypatch):
    import pywintypes
    import win32job
    import win32process

    identities = []

    def fail_assign(job, handle):
        identities.append(read_process_identity(win32process.GetProcessId(handle)))
        raise pywintypes.error(5, "AssignProcessToJobObject", "access denied")

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", fail_assign)
    executable, env = _interpreter()
    launch = RuntimeLaunch((executable, "-u", "-c", "import time; time.sleep(120)"), tmp_path, env, "connected")

    with pytest.raises(IntegrationError) as raised:
        start_connected_process(launch)

    assert isinstance(raised.value, ConnectedLaunchError)
    assert "contain" in str(raised.value)
    assert raised.value.cleanup_confirmed is True
    assert identities and identities[0] is not None
    assert process_identity_state(identities[0]) != "alive"


@pytest.mark.skipif(os.name != "nt", reason="Windows startup race is Windows-specific")
def test_windows_descendant_started_before_assignment_fails_closed(tmp_path, monkeypatch):
    import winpty

    real_pty = winpty.PTY
    pid_file = tmp_path / "escaped.pid"

    class RacingPTY:
        """Lets the root run and spawn a child before Flowgency assigns it to the job."""

        def __init__(self, *args, **kwargs):
            self._pty = real_pty(*args, **kwargs)

        def spawn(self, *args, **kwargs):
            spawned = self._pty.spawn(*args, **kwargs)
            self._pty.write("\x1b[?1;0c")
            deadline = time.monotonic() + 20
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.2)
            return spawned

        def __getattr__(self, name):
            return getattr(self._pty, name)

    monkeypatch.setattr(winpty, "PTY", RacingPTY)
    executable, env = _interpreter()
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(120)"
    )
    launch = RuntimeLaunch((executable, "-u", "-c", script), tmp_path, env, "connected")

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert "outside" in str(raised.value)
    assert raised.value.cleanup_confirmed is True
    escaped = read_process_identity(int(pid_file.read_text()))
    assert escaped is None or process_identity_state(escaped) != "alive"


@pytest.mark.skipif(os.name == "nt" or not Path("/proc/self/stat").is_file(), reason="POSIX /proc PTY check")
def test_posix_group_capture_failure_cleans_up_spawned_child(tmp_path, monkeypatch):
    from ptyprocess import PtyProcess

    real_spawn = PtyProcess.spawn.__func__
    spawned = []

    def recording_spawn(cls, *args, **kwargs):
        process = real_spawn(cls, *args, **kwargs)
        spawned.append(process.pid)
        return process

    monkeypatch.setattr(PtyProcess, "spawn", classmethod(recording_spawn))
    monkeypatch.setattr("flowgency.jobs.connected_process._capture_posix_group_identity", lambda pid: None)
    launch = RuntimeLaunch((sys.executable, "-c", "import time; time.sleep(120)"), tmp_path, os.environ.copy(), "connected")

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is True
    assert spawned
    with pytest.raises(ProcessLookupError):
        os.kill(spawned[0], 0)
