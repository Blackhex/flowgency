from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.processes import RuntimeProcessLifecycle, process_identity_state, read_process_identity
from flowgency.jobs.connected_process import (
    ConnectedLaunchError,
    connected_process_available,
    start_connected_process,
)


_POSIX_PROC = os.name != "nt" and Path("/proc/self/stat").is_file()
_SUPPORTED_HOST = _POSIX_PROC
_DEVICE_ATTRIBUTES_QUERY = b"\x1b[c"


def _interpreter() -> tuple[str, dict[str, str]]:
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX pre-spawn size validation only; Windows rejects before validating")
def test_connected_process_rejects_invalid_terminal_size(tmp_path):
    launch = RuntimeLaunch((sys.executable,), tmp_path, os.environ.copy(), "connected")
    with pytest.raises(ValueError, match="size"):
        start_connected_process(launch, rows=0, cols=80)


@pytest.mark.skipif(os.name == "nt", reason="POSIX pre-spawn env validation only; Windows rejects before validating")
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
    if os.name == "nt":
        pytest.skip("Windows dependency gating is covered by Windows-specific tests")
    monkeypatch.setitem(sys.modules, "ptyprocess", None)
    assert connected_process_available() is False


@pytest.mark.skipif(os.name != "nt", reason="Windows connected-setup gate")
def test_windows_connected_process_unavailable_without_required_modules(tmp_path, monkeypatch):
    import builtins
    import flowgency.jobs.connected_process as connected_process

    blocked = {"win32job", "win32api", "win32process", "win32con", "win32event", "pywintypes"}
    attempted: list[str] = []
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in blocked:
            attempted.append(name)
            raise ImportError(f"blocked for this test: {name}")
        return real_import(name, *args, **kwargs)

    def must_not_spawn(*args, **kwargs):
        raise AssertionError("Windows connected spawn should not run when dependencies are missing")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    fake_windows_process = type("_FakeWindowsConnectedProcess", (), {"spawn": staticmethod(must_not_spawn)})
    monkeypatch.setattr(connected_process, "WindowsConnectedProcess", fake_windows_process, raising=False)

    assert connected_process_available() is False
    launch = RuntimeLaunch((sys.executable, "-c", "print('must not run')"), tmp_path, os.environ.copy(), "connected")
    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is True
    assert attempted


@pytest.mark.skipif(os.name != "nt", reason="Windows connected-setup gate")
def test_windows_connected_process_unavailable_without_native_conpty(tmp_path, monkeypatch):
    import flowgency.jobs.connected_process as connected_process
    import flowgency.jobs.windows_connected_process as windows_connected_process

    def must_not_spawn(*args, **kwargs):
        raise AssertionError("Windows connected spawn should not run when native ConPTY is unavailable")

    monkeypatch.setattr(windows_connected_process, "native_conpty_available", lambda: False)
    fake_windows_process = type("_FakeWindowsConnectedProcess", (), {"spawn": staticmethod(must_not_spawn)})
    monkeypatch.setattr(connected_process, "WindowsConnectedProcess", fake_windows_process, raising=False)

    assert connected_process_available() is False
    launch = RuntimeLaunch((sys.executable, "-c", "print('must not run')"), tmp_path, os.environ.copy(), "connected")
    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows connected-setup gate")
def test_windows_connected_process_available_with_real_dependencies():
    assert connected_process_available() is True


@pytest.mark.skipif(os.name != "nt", reason="Windows connected-setup gate")
def test_windows_connected_process_available_without_winpty(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "winpty":
            raise ImportError("blocked for this test: winpty")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert connected_process_available() is True


@pytest.mark.skipif(os.name != "nt", reason="Windows connected-setup gate")
def test_windows_connected_process_unavailable_without_websocket_protocol(monkeypatch):
    module = types.SimpleNamespace(AutoWebSocketsProtocol=None)
    monkeypatch.setitem(sys.modules, "uvicorn.protocols.websockets.auto", module)

    assert connected_process_available() is False


@pytest.mark.skipif(os.name != "nt", reason="Windows connected-setup gate")
def test_windows_connected_process_unavailable_without_job_primitives(monkeypatch):
    import win32job

    monkeypatch.delattr(win32job, "AssignProcessToJobObject", raising=False)

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


@pytest.mark.skipif(os.name == "nt" or not Path("/proc/self/stat").is_file(), reason="POSIX /proc PTY check")
def test_posix_group_capture_error_cleans_up_spawned_child(tmp_path, monkeypatch):
    """A capture that raises (e.g. undecodable /proc bytes) instead of returning None must
    still go through the confirmed-stop fallback rather than leaking the spawned child."""
    from ptyprocess import PtyProcess

    real_spawn = PtyProcess.spawn.__func__
    spawned = []

    def recording_spawn(cls, *args, **kwargs):
        process = real_spawn(cls, *args, **kwargs)
        spawned.append(process.pid)
        return process

    def raise_capture_error(pid):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

    monkeypatch.setattr(PtyProcess, "spawn", classmethod(recording_spawn))
    monkeypatch.setattr("flowgency.jobs.connected_process._capture_posix_group_identity", raise_capture_error)
    launch = RuntimeLaunch((sys.executable, "-u", "-c", "import time; time.sleep(120)"), tmp_path, os.environ.copy(), "connected")

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is True
    assert raised.value.__cause__ is not None and isinstance(raised.value.__cause__, UnicodeDecodeError)
    assert spawned
    with pytest.raises(ProcessLookupError):
        os.kill(spawned[0], 0)


@pytest.mark.skipif(os.name == "nt" or not Path("/proc/self/stat").is_file(), reason="POSIX /proc PTY check")
def test_posix_group_capture_error_reports_unconfirmed_without_leaking(tmp_path, monkeypatch):
    """When the group status can't be confirmed empty after the real kill, the fallback must
    report cleanup_confirmed=False honestly -- it must not claim confirmation it never proved.
    The kill signal here is real (unmocked); only the status *read* is forced to look inconclusive,
    so this never leaves an actual child running."""
    from ptyprocess import PtyProcess

    real_spawn = PtyProcess.spawn.__func__
    spawned = []

    def recording_spawn(cls, *args, **kwargs):
        process = real_spawn(cls, *args, **kwargs)
        spawned.append(process.pid)
        return process

    def raise_capture_error(pid):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

    monkeypatch.setattr(PtyProcess, "spawn", classmethod(recording_spawn))
    monkeypatch.setattr("flowgency.jobs.connected_process._capture_posix_group_identity", raise_capture_error)
    monkeypatch.setattr("flowgency.jobs.connected_process._group_exit_status", lambda group_id, deadline: "active")
    launch = RuntimeLaunch((sys.executable, "-u", "-c", "import time; time.sleep(120)"), tmp_path, os.environ.copy(), "connected")

    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)

    assert raised.value.cleanup_confirmed is False
    assert spawned
    # Prove the real SIGKILL (sent by the unmocked termination path) actually reached the
    # child, despite the forced-inconclusive status read reporting it as unconfirmed.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(spawned[0], 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("child was not actually killed despite the forced-unconfirmed status")
    with contextlib.suppress(ChildProcessError):
        os.waitpid(spawned[0], 0)


@pytest.mark.skipif(os.name == "nt" or not Path("/proc/self/stat").is_file(), reason="POSIX /proc PTY check")
def test_terminate_unproven_group_never_resignals_after_leader_reaped(tmp_path, monkeypatch):
    """Once an unproven leader has been reaped, its numeric pid may already be recycled by the
    OS for an unrelated process. A retry that still can't prove the group empty must revalidate
    status only -- it must never signal that pid/pgid a second time."""
    import flowgency.jobs.connected_process as connected_process
    from flowgency.jobs.connected_process import PosixConnectedProcess
    from ptyprocess import PtyProcess

    launch = RuntimeLaunch((sys.executable, "-u", "-c", "import time; time.sleep(120)"), tmp_path, os.environ.copy(), "connected")
    pty = PtyProcess.spawn(list(launch.argv), cwd=str(launch.cwd), env=dict(launch.env), dimensions=(24, 80))
    process = PosixConnectedProcess(pty, None)  # simulate a group whose capture could not be proved

    monkeypatch.setattr(connected_process, "_group_exit_status", lambda group_id, deadline: "active")

    kill_calls: list[tuple[str, int]] = []
    real_killpg, real_kill = os.killpg, os.kill

    def recording_killpg(pgid, sig):
        kill_calls.append(("killpg", pgid))
        return real_killpg(pgid, sig)

    def recording_kill(pid, sig):
        kill_calls.append(("kill", pid))
        return real_kill(pid, sig)

    monkeypatch.setattr(os, "killpg", recording_killpg)
    monkeypatch.setattr(os, "kill", recording_kill)

    try:
        confirmed, reason = process._stop_tree()
        assert (confirmed, reason) == (False, "active")
        assert kill_calls, "expected the first stop attempt to signal the unproven leader"
        assert all(target == pty.pid for _, target in kill_calls)
        with connected_process._unconfirmed_lock:
            assert connected_process._unconfirmed.count(process) == 1

        # Prove the first, real signal actually reached the child before its pid could be
        # recycled, rather than relying on the forced-inconclusive status alone.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                real_kill(pty.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("child was not actually killed by the first stop attempt")

        kill_calls.clear()

        # Simulate `_retry_unconfirmed()` calling `_stop_tree()` again on the already-tracked,
        # still-unconfirmed process while the group status remains inconclusive.
        confirmed_again, reason_again = process._stop_tree()

        assert kill_calls == [], (
            f"a retry must never re-signal the already-reaped pid {pty.pid}: it may by now "
            f"name a recycled, unrelated process; recorded calls: {kill_calls}"
        )
        assert (confirmed_again, reason_again) == (False, "active")
    finally:
        with connected_process._unconfirmed_lock:
            while process in connected_process._unconfirmed:
                connected_process._unconfirmed.remove(process)
        with contextlib.suppress(ChildProcessError, ProcessLookupError):
            os.waitpid(pty.pid, 0)


def test_launch_check_blocks_until_an_in_flight_stop_records_its_failure():
    """A launch must never observe an empty pending-failure list while an earlier stop is
    still deciding it failed; it has to wait for that decision to be recorded first."""
    import flowgency.jobs.connected_process as connected_process

    entered_terminate = threading.Event()
    release_terminate = threading.Event()

    class _BlockingTerminal(connected_process._OwnedTerminal):
        def _terminate_tree(self, deadline):
            entered_terminate.set()
            release_terminate.wait(timeout=5)
            return False, "active"

        def _read_terminal(self, size):
            return b""

        def _write_terminal(self, data):
            pass

        def _resize_terminal(self, rows, cols):
            pass

        def _release(self, drained):
            pass

    terminal = _BlockingTerminal(pid=999999)
    stop_result: dict[str, object] = {}

    def run_stop():
        stop_result["value"] = terminal._stop_tree()

    stop_thread = threading.Thread(target=run_stop)
    stop_thread.start()
    assert entered_terminate.wait(timeout=5), "stop never reached the fake termination point"

    retry_done = threading.Event()
    retry_result: dict[str, object] = {}

    def run_retry():
        try:
            connected_process._retry_unconfirmed()
        except connected_process.ConnectedLaunchError as error:
            retry_result["error"] = error
        finally:
            retry_done.set()

    retry_thread = threading.Thread(target=run_retry)
    retry_thread.start()
    try:
        # The in-flight stop has not recorded an outcome yet; a concurrent launch check
        # must block rather than see an empty pending list and let a launch through.
        assert not retry_done.wait(timeout=0.3), "launch check did not wait for the in-flight stop"
    finally:
        release_terminate.set()
        stop_thread.join(timeout=5)
        retry_thread.join(timeout=5)

    assert stop_result["value"] == (False, "active")
    assert isinstance(retry_result.get("error"), connected_process.ConnectedLaunchError)
    assert retry_result["error"].cleanup_confirmed is False
    with connected_process._unconfirmed_lock:
        if terminal in connected_process._unconfirmed:
            connected_process._unconfirmed.remove(terminal)
