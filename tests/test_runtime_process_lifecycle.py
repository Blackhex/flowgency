from __future__ import annotations

import os
import io
import signal
import sys
import time
import threading
from pathlib import Path

import pytest

from flowgency.jobs.processes import (
    OwnedPosixProcessGroup,
    RuntimeProcessLifecycle,
    _OutputBudget,
    _OutputCapture,
    _run_supervised_posix,
    _owned_posix_group_state,
    _posix_process_identity_state,
    _read_posix_process_snapshot,
    process_identity_matches,
    process_identity_state,
    RuntimeProcessIdentity,
    read_process_identity,
    run_supervised,
)


def _write_script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _wait_for_path(path: Path, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {path}")


def _wait_for_process_exit(identity: RuntimeProcessIdentity, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process_identity_state(identity) != "alive":
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for process {identity.pid} to exit")


def _native_python_launch() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    if os.name != "nt":
        return sys.executable, env
    import win32api
    import win32process

    env["__PYVENV_LAUNCHER__"] = sys.executable
    return win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0), env


def _capture_windows_identity_when_ready(
    ready: Path,
    pid_file: Path,
    *,
    timeout: float,
):
    captured: dict[str, object] = {}

    def worker() -> None:
        _wait_for_path(ready, timeout=timeout)
        pid = int(pid_file.read_text(encoding="utf-8"))
        identity = read_process_identity(pid)
        if identity is None:
            raise AssertionError(f"Could not capture identity for pid {pid}")
        captured["pid"] = pid
        captured["identity"] = identity
        if os.name == "nt":
            import win32api
            import win32con

            captured["handle"] = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )

    thread = threading.Thread(target=worker)
    thread.start()
    return captured, thread


class _FakePipe:
    def __init__(self, payload: bytes = b""):
        self._buffer = io.BytesIO(payload)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        self.closed = True


class _FakePosixProcess:
    def __init__(self, pid: int, *, exit_code: int = 0):
        self.pid = pid
        self.stdout = _FakePipe()
        self.stderr = _FakePipe()
        self._exit_code = exit_code
        self.wait_calls: list[float | int | None] = []
        self.allow_reap = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if not self.allow_reap:
            raise AssertionError("wait() reaped the leader before group completion")
        return self._exit_code


def test_run_supervised_reports_confirmed_exit_for_completed_process_tree(tmp_path: Path):
    child_script = _write_script(
        tmp_path / "child.py",
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "grandchild = pathlib.Path(sys.argv[1])\n"
        "subprocess.run([sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(\"done\", encoding=\"utf-8\"); print(\"grandchild-finished\", flush=True)', str(grandchild)], check=True)\n"
        "print('root-finished', flush=True)\n",
    )
    grandchild_marker = tmp_path / "grandchild.txt"

    result = run_supervised(
        [sys.executable, str(child_script), str(grandchild_marker)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        lifecycle=RuntimeProcessLifecycle(job_id="job-1", generation="gen-1"),
    )

    assert result.exit_code == 0
    assert "root-finished" in result.stdout
    assert "grandchild-finished" in result.stdout
    assert grandchild_marker.read_text(encoding="utf-8") == "done"
    assert result.process_stop_evidence.job_id == "job-1"
    assert result.process_stop_evidence.generation == "gen-1"
    assert result.process_stop_evidence.confirmed is True
    assert result.process_stop_evidence.reason == "exited"


def test_run_supervised_waits_for_root_exit_with_live_descendant(tmp_path: Path):
    root_script = _write_script(
        tmp_path / "root_exit_first.py",
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "done = pathlib.Path(sys.argv[1])\n"
        "subprocess.Popen([sys.executable, '-c', 'import pathlib,sys,time; time.sleep(0.2); pathlib.Path(sys.argv[1]).write_text(\"done\", encoding=\"utf-8\"); print(\"grandchild-finished\", flush=True)', str(done)])\n"
        "print('root-finished', flush=True)\n",
    )
    grandchild_marker = tmp_path / "late-grandchild.txt"

    result = run_supervised(
        [sys.executable, str(root_script), str(grandchild_marker)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        lifecycle=RuntimeProcessLifecycle(job_id="job-root", generation="gen-root"),
    )

    assert result.exit_code == 0
    assert result.process_stop_evidence.confirmed is True
    assert result.process_stop_evidence.reason == "exited"
    assert "root-finished" in result.stdout
    assert "grandchild-finished" in result.stdout
    assert grandchild_marker.read_text(encoding="utf-8") == "done"


@pytest.mark.skipif(os.name != "nt", reason="Strict native containment proof is Windows-specific")
def test_run_supervised_timeout_kills_native_descendants_after_root_exit(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    grandchild_script = _write_script(
        tmp_path / "grandchild_wait.py",
        "import os\n"
        "import sys\n"
        "import pathlib\n"
        "import time\n"
        "import win32api\n"
        "import win32con\n"
        "import win32job\n"
        "pid_file = pathlib.Path(sys.argv[1])\n"
        "ready = pathlib.Path(sys.argv[2])\n"
        "release = pathlib.Path(sys.argv[3])\n"
        "in_job_file = pathlib.Path(sys.argv[4])\n"
        "pid_file.write_text(str(os.getpid()), encoding='utf-8')\n"
        "handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, os.getpid())\n"
        "in_job_file.write_text('1' if win32job.IsProcessInJob(handle, None) else '0', encoding='utf-8')\n"
        "handle.Close()\n"
        "ready.write_text('ready', encoding='utf-8')\n"
        "sys.stdout.write('grandchild-ready\\n')\n"
        "sys.stdout.flush()\n"
        "while not release.exists():\n"
        "    time.sleep(0.05)\n",
    )
    child_script = _write_script(
        tmp_path / "child_wait.py",
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "python = sys.argv[1]\n"
        "argv = [python, sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]]\n"
        "_env = os.environ.copy()\n"
        "if sys.platform == 'win32': _env['__PYVENV_LAUNCHER__'] = sys.executable\n"
        "child = subprocess.Popen(argv, stdout=sys.stdout, stderr=sys.stderr, close_fds=False, env=_env)\n"
        "child.wait()\n",
    )
    root_script = _write_script(
        tmp_path / "root_wait.py",
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "python = sys.argv[1]\n"
        "_env = os.environ.copy()\n"
        "if sys.platform == 'win32': _env['__PYVENV_LAUNCHER__'] = sys.executable\n"
        "child = subprocess.Popen([python, sys.argv[2], python, sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7]], stdout=sys.stdout, stderr=sys.stderr, close_fds=False, env=_env)\n"
        "pathlib.Path(sys.argv[8]).write_text('root-exited', encoding='utf-8')\n",
    )
    pid_file = tmp_path / "grandchild.pid"
    ready = tmp_path / "ready.txt"
    release = tmp_path / "release.txt"
    in_job_file = tmp_path / "grandchild.in_job"
    root_exited = tmp_path / "root-exited.txt"
    captured, capture_thread = _capture_windows_identity_when_ready(
        ready,
        pid_file,
        timeout=5,
    )

    try:
        started = time.monotonic()
        result = run_supervised(
            [
                native_python,
                str(root_script),
                native_python,
                str(child_script),
                str(grandchild_script),
                str(pid_file),
                str(ready),
                str(release),
                str(in_job_file),
                str(root_exited),
            ],
            cwd=tmp_path,
            env=native_env,
            timeout=1,
            lifecycle=RuntimeProcessLifecycle(job_id="job-timeout", generation="gen-timeout"),
        )
        elapsed = time.monotonic() - started
        capture_thread.join(timeout=1)
        assert not capture_thread.is_alive()
        grandchild_identity = captured.get("identity")
        assert isinstance(grandchild_identity, RuntimeProcessIdentity)
        assert root_exited.read_text(encoding="utf-8") == "root-exited"
        assert in_job_file.read_text(encoding="utf-8") == "1"
        assert elapsed < 3
        assert result.exit_code == 124
        assert result.process_stop_evidence.confirmed is True
        assert result.process_stop_evidence.reason == "timeout"
        _wait_for_process_exit(grandchild_identity, timeout=5)
        import win32con
        import win32process

        handle = captured.get("handle")
        assert handle is not None
        assert win32process.GetExitCodeProcess(handle) != win32con.STILL_ACTIVE
    finally:
        handle = captured.get("handle")
        if handle is not None:
            handle.Close()
        if capture_thread.is_alive():
            capture_thread.join(timeout=0.1)


@pytest.mark.skipif(os.name != "nt", reason="Windows containment setup is Windows-specific")
def test_windows_setup_failure_never_resumes_uncontained_process(tmp_path: Path, monkeypatch):
    import pywintypes
    import win32job

    marker = tmp_path / "started.txt"
    child_script = _write_script(
        tmp_path / "never_resume.py",
        "import pathlib\n"
        "import sys\n"
        "pathlib.Path(sys.argv[1]).write_text('started', encoding='utf-8')\n",
    )

    def fail_assign(*args, **kwargs):
        raise pywintypes.error(5, "AssignProcessToJobObject", "denied")

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", fail_assign)

    result = run_supervised(
        [sys.executable, str(child_script), str(marker)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        lifecycle=RuntimeProcessLifecycle(job_id="job-fail", generation="gen-fail"),
    )

    assert result.exit_code == 125
    assert result.process_stop_evidence.confirmed is False
    assert result.process_stop_evidence.reason == "containment-setup-failed"
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job accounting is Windows-specific")
def test_windows_completed_tree_is_unconfirmed_when_job_accounting_stays_nonzero(
    tmp_path: Path,
    monkeypatch,
):
    child_script = _write_script(
        tmp_path / "root_done.py",
        "print('root-finished', flush=True)\n",
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._job_exit_status",
        lambda _job_handle, _deadline: "active",
    )

    result = run_supervised(
        [sys.executable, str(child_script)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-nonzero", generation="gen-nonzero"),
    )

    assert result.exit_code == 124
    assert result.process_stop_evidence.confirmed is False
    assert result.process_stop_evidence.reason == "job-active-processes"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job accounting is Windows-specific")
def test_windows_timeout_is_unconfirmed_when_job_accounting_cannot_be_read(
    tmp_path: Path,
    monkeypatch,
):
    child_script = _write_script(
        tmp_path / "wait_forever.py",
        "import time\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
    )

    def fail_job_query(_job_handle):
        raise OSError("job accounting unavailable")

    monkeypatch.setattr("flowgency.jobs.processes._job_active_processes", fail_job_query)

    result = run_supervised(
        [sys.executable, str(child_script)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-unknown", generation="gen-unknown"),
    )

    assert result.exit_code == 124
    assert result.process_stop_evidence.confirmed is False
    assert result.process_stop_evidence.reason == "job-accounting-unavailable"


def test_pid_identity_mismatch_is_not_treated_as_the_same_process():
    identity = read_process_identity(os.getpid())

    assert identity is not None
    assert process_identity_matches(identity) is True
    assert process_identity_matches(type(identity)(pid=identity.pid, created_at="not-the-same")) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows process access handling is Windows-specific")
def test_windows_open_process_access_error_is_unknown(monkeypatch):
    import pywintypes
    import win32api

    def deny_open_process(*args, **kwargs):
        raise pywintypes.error(5, "OpenProcess", "access denied")

    monkeypatch.setattr(win32api, "OpenProcess", deny_open_process)

    assert (
        process_identity_state(RuntimeProcessIdentity(pid=1234, created_at="2026-01-01T00:00:00+00:00"))
        == "unknown"
    )


def test_posix_permission_error_reports_unknown_identity_state(monkeypatch):
    identity = RuntimeProcessIdentity(pid=4321, created_at="98765")

    monkeypatch.setattr("flowgency.jobs.processes._read_posix_process_created_at", lambda pid: ("98765", "ok") if pid == 4321 else (None, "exited"))

    def deny_signal(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("flowgency.jobs.processes.os.kill", deny_signal)

    assert _posix_process_identity_state(identity) == "unknown"


def test_posix_read_process_identity_parses_proc_stat_with_spaces(monkeypatch):
    original_read_text = Path.read_text
    sample = (
        "4321 (python worker (child)) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 98765 21 22\n"
    )

    def fake_read_text(self, *args, **kwargs):
        if str(self).replace("\\", "/") == "/proc/4321/stat":
            return sample
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    snapshot, status = _read_posix_process_snapshot(4321)

    assert status == "ok"
    assert snapshot is not None
    assert snapshot.created_at == "98765"
    assert snapshot.process_group_id == 2
    assert snapshot.session_id == 3


def test_owned_posix_group_state_is_unknown_when_membership_scan_fails(monkeypatch):
    group_identity = OwnedPosixProcessGroup(
        leader=RuntimeProcessIdentity(pid=4321, created_at="leader-created"),
        process_group_id=4321,
        session_id=4321,
    )

    def fake_snapshot(pid: int):
        if pid == 4321:
            return (
                type("Snapshot", (), {
                    "pid": 4321,
                    "created_at": "leader-created",
                    "process_group_id": 4321,
                    "session_id": 4321,
                    "state": "Z",
                })(),
                "ok",
            )
        return None, "unknown"

    monkeypatch.setattr("flowgency.jobs.processes._read_posix_process_snapshot", fake_snapshot)
    monkeypatch.setattr(
        "flowgency.jobs.processes.Path.iterdir",
        lambda self: [Path("/proc/1234")],
    )

    assert _owned_posix_group_state(group_identity) == "unknown"


def test_owned_posix_group_state_rejects_reused_leader_identity(monkeypatch):
    group_identity = OwnedPosixProcessGroup(
        leader=RuntimeProcessIdentity(pid=4321, created_at="leader-created"),
        process_group_id=4321,
        session_id=4321,
    )

    monkeypatch.setattr(
        "flowgency.jobs.processes._read_posix_process_snapshot",
        lambda pid: (
            type("Snapshot", (), {
                "pid": 4321,
                "created_at": "different-created",
                "process_group_id": 4321,
                "session_id": 4321,
                "state": "Z",
            })(),
            "ok",
        ),
    )

    assert _owned_posix_group_state(group_identity) == "reused"


def test_owned_posix_group_state_rejects_member_with_wrong_session(monkeypatch):
    group_identity = OwnedPosixProcessGroup(
        leader=RuntimeProcessIdentity(pid=4321, created_at="leader-created"),
        process_group_id=4321,
        session_id=4321,
    )

    def fake_snapshot(pid: int):
        if pid == 4321:
            return (
                type("Snapshot", (), {
                    "pid": 4321,
                    "created_at": "leader-created",
                    "process_group_id": 4321,
                    "session_id": 4321,
                    "state": "Z",
                })(),
                "ok",
            )
        return (
            type("Snapshot", (), {
                "pid": 5000,
                "created_at": "member-created",
                "process_group_id": 4321,
                "session_id": 9999,
                "state": "S",
            })(),
            "ok",
        )

    monkeypatch.setattr("flowgency.jobs.processes._read_posix_process_snapshot", fake_snapshot)
    monkeypatch.setattr(
        "flowgency.jobs.processes.Path.iterdir",
        lambda self: [Path("/proc/4321"), Path("/proc/5000")],
    )

    assert _owned_posix_group_state(group_identity) == "reused"


def test_posix_timeout_reaps_only_after_owned_group_finishes(monkeypatch, tmp_path: Path):
    fake_process = _FakePosixProcess(4321)
    signal_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "flowgency.jobs.processes.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes.read_process_identity",
        lambda pid: RuntimeProcessIdentity(pid=pid, created_at="leader-created"),
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._capture_posix_group_identity",
        lambda pid: object(),
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._observe_posix_root_exit",
        lambda process, deadline: 0,
        raising=False,
    )

    def fake_group_status(group_identity, deadline):
        if signal_calls:
            fake_process.allow_reap = True
            return "empty"
        return "active"

    monkeypatch.setattr(
        "flowgency.jobs.processes._owned_posix_group_status",
        fake_group_status,
        raising=False,
    )

    def fake_signal(group_identity, sig):
        signal_calls.append((fake_process.pid, sig))
        return "signaled"

    monkeypatch.setattr(
        "flowgency.jobs.processes._signal_owned_posix_group",
        fake_signal,
        raising=False,
    )

    result = _run_supervised_posix(
        [sys.executable, "ignored.py"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-posix-timeout", generation="gen-posix-timeout"),
        start=time.monotonic(),
    )

    assert result.exit_code == 124
    assert result.process_stop_evidence.confirmed is True
    assert result.process_stop_evidence.reason == "timeout"
    assert signal_calls == [(4321, getattr(signal, "SIGKILL", signal.SIGTERM))]
    assert fake_process.wait_calls == [0]


def test_posix_reused_group_fails_closed_without_signaling(monkeypatch, tmp_path: Path):
    fake_process = _FakePosixProcess(4321)
    fake_process.allow_reap = True
    signal_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "flowgency.jobs.processes.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes.read_process_identity",
        lambda pid: RuntimeProcessIdentity(pid=pid, created_at="leader-created"),
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._capture_posix_group_identity",
        lambda pid: object(),
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._observe_posix_root_exit",
        lambda process, deadline: 0,
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._owned_posix_group_status",
        lambda group_identity, deadline: "reused",
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes.os.killpg",
        lambda pgid, sig: signal_calls.append((pgid, sig)),
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._signal_owned_posix_group",
        lambda group_identity, sig: signal_calls.append((9999, sig)),
        raising=False,
    )

    result = _run_supervised_posix(
        [sys.executable, "ignored.py"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-posix-reused", generation="gen-posix-reused"),
        start=time.monotonic(),
    )

    assert result.exit_code == 0
    assert result.process_stop_evidence.confirmed is False
    assert result.process_stop_evidence.reason == "group-identity-unavailable"
    assert signal_calls == []


def test_posix_unreadable_leader_snapshot_reported_as_unknown_not_reused(monkeypatch, tmp_path: Path):
    # Leader /proc/<pid>/stat is unreadable (permission/parse/OS error, not exited);
    # runner must report group-state-unavailable, not group-identity-unavailable,
    # and must not signal the process group.
    fake_process = _FakePosixProcess(4321)
    fake_process.allow_reap = True
    signal_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "flowgency.jobs.processes.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes.read_process_identity",
        lambda pid: RuntimeProcessIdentity(pid=pid, created_at="leader-created"),
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._capture_posix_group_identity",
        lambda pid: OwnedPosixProcessGroup(
            leader=RuntimeProcessIdentity(pid=pid, created_at="leader-created"),
            process_group_id=pid,
            session_id=pid,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._observe_posix_root_exit",
        lambda process, deadline: 0,
        raising=False,
    )
    # Simulate leader whose /proc stat is unreadable (permission or parse failure),
    # NOT exited — status "unknown" rather than "exited".
    monkeypatch.setattr(
        "flowgency.jobs.processes._read_posix_process_snapshot",
        lambda pid: (None, "unknown"),
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes.os.killpg",
        lambda pgid, sig: signal_calls.append((pgid, sig)),
        raising=False,
    )

    result = _run_supervised_posix(
        [sys.executable, "ignored.py"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-leader-unknown", generation="gen-leader-unknown"),
        start=time.monotonic(),
    )

    assert result.process_stop_evidence.confirmed is False
    assert result.process_stop_evidence.reason == "group-state-unavailable"
    assert signal_calls == []


@pytest.mark.skipif(os.name == "nt", reason="requires a real POSIX host")
def test_run_supervised_posix_confirms_completed_process_tree(tmp_path: Path):
    child_script = _write_script(
        tmp_path / "posix_child.py",
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "grandchild = pathlib.Path(sys.argv[1])\n"
        "subprocess.run([sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(\"done\", encoding=\"utf-8\"); print(\"posix-grandchild-finished\", flush=True)', str(grandchild)], check=True)\n"
        "print('posix-root-finished', flush=True)\n",
    )
    grandchild_marker = tmp_path / "posix-grandchild.txt"

    result = run_supervised(
        [sys.executable, str(child_script), str(grandchild_marker)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        lifecycle=RuntimeProcessLifecycle(job_id="job-posix-real", generation="gen-posix-real"),
    )

    assert result.exit_code == 0
    assert result.process_stop_evidence.confirmed is True
    assert result.process_stop_evidence.reason == "exited"
    assert "posix-root-finished" in result.stdout
    assert "posix-grandchild-finished" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="requires a real POSIX host")
def test_run_supervised_posix_timeout_kills_group_after_root_exit(tmp_path: Path):
    root_script = _write_script(
        tmp_path / "posix_root_exit_first.py",
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "subprocess.Popen([sys.executable, '-c', 'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(\"started\", encoding=\"utf-8\"); time.sleep(30)', str(marker)])\n"
        "print('posix-root-finished', flush=True)\n",
    )
    started = tmp_path / "posix-started.txt"

    result = run_supervised(
        [sys.executable, str(root_script), str(started)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-posix-real-timeout", generation="gen-posix-real-timeout"),
    )

    assert result.exit_code == 124
    assert result.process_stop_evidence.reason == "timeout"


def test_run_supervised_returns_exact_raw_bytes_when_retention_requested(tmp_path: Path):
    binary_child = [sys.executable, "-c", "import os; os.write(1, bytes([0, 255, 10]))"]

    result = run_supervised(
        binary_child,
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        lifecycle=RuntimeProcessLifecycle(job_id="job-raw", generation="gen-raw"),
        retain_output_bytes=True,
    )

    assert result.exit_code == 0
    assert result.outcome == "exited"
    assert result.stdout_bytes == bytes([0, 255, 10])
    assert result.stderr_bytes == b""
    assert result.output_limit_exceeded is False
    assert result.process_stop_evidence.confirmed is True


def test_run_supervised_output_limit_truncates_and_kills_process_tree(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    root_script = _write_script(
        tmp_path / "oversized_root.py",
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "python = sys.argv[1]\n"
        "pid_file, ready = sys.argv[2], sys.argv[3]\n"
        "env = os.environ.copy()\n"
        "if sys.platform == 'win32': env['__PYVENV_LAUNCHER__'] = sys.executable\n"
        "subprocess.Popen([python, '-c', 'import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding=\"utf-8\"); pathlib.Path(sys.argv[2]).write_text(\"ready\", encoding=\"utf-8\"); time.sleep(120)', pid_file, ready], close_fds=False, env=env)\n"
        "while not os.path.exists(ready):\n"
        "    time.sleep(0.02)\n"
        "time.sleep(0.5)\n"
        "os.write(1, b'x' * (2 * 1024 * 1024))\n"
        "time.sleep(120)\n",
    )
    pid_file = tmp_path / "oversized-descendant.pid"
    ready = tmp_path / "oversized-ready.txt"
    captured, capture_thread = _capture_windows_identity_when_ready(ready, pid_file, timeout=15)

    try:
        started = time.monotonic()
        result = run_supervised(
            [native_python, str(root_script), native_python, str(pid_file), str(ready)],
            cwd=tmp_path,
            env=native_env,
            timeout=20,
            lifecycle=RuntimeProcessLifecycle(job_id="job-limit", generation="gen-limit"),
            output_limit_bytes=1024,
            retain_output_bytes=True,
        )
        elapsed = time.monotonic() - started
        capture_thread.join(timeout=5)
        assert not capture_thread.is_alive()
        descendant_identity = captured.get("identity")
        assert isinstance(descendant_identity, RuntimeProcessIdentity)

        assert result.output_limit_exceeded is True
        assert result.outcome == "output-limit"
        assert elapsed < 20
        assert result.stdout_bytes is not None and result.stderr_bytes is not None
        assert len(result.stdout_bytes) + len(result.stderr_bytes) <= 1024
        assert set(result.stdout_bytes) <= {ord("x")}
        assert result.process_stop_evidence.confirmed is True
        assert result.process_stop_evidence.reason == "output-limit"
        _wait_for_process_exit(descendant_identity, timeout=10)
    finally:
        handle = captured.get("handle")
        if handle is not None:
            handle.Close()
        if capture_thread.is_alive():
            capture_thread.join(timeout=0.1)


def test_posix_output_limit_reports_missing_root_identity_not_live_descendants(
    monkeypatch, tmp_path: Path
):
    # The owned group is provably empty; only the root's identity is missing, so
    # the stop reason must not claim descendants are still running.
    fake_process = _FakePosixProcess(4321)
    fake_process.allow_reap = True

    monkeypatch.setattr(
        "flowgency.jobs.processes.subprocess.Popen",
        lambda *args, **kwargs: fake_process,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes.read_process_identity",
        lambda pid: None,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._capture_posix_group_identity",
        lambda pid: OwnedPosixProcessGroup(
            leader=RuntimeProcessIdentity(pid=pid, created_at="leader-created"),
            process_group_id=pid,
            session_id=pid,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._signal_owned_posix_group",
        lambda group_identity, sig: "signaled",
        raising=False,
    )
    monkeypatch.setattr(
        "flowgency.jobs.processes._owned_posix_group_status",
        lambda group_identity, deadline: "empty",
        raising=False,
    )
    capture = _OutputCapture(budget=_OutputBudget(1))
    assert capture.budget is not None
    capture.budget.take(b"oversized")

    result = _run_supervised_posix(
        [sys.executable, "ignored.py"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=5,
        lifecycle=RuntimeProcessLifecycle(
            job_id="job-limit-identity", generation="gen-limit-identity"
        ),
        start=time.monotonic(),
        capture=capture,
    )

    assert result.outcome == "output-limit"
    assert result.process_stop_evidence.confirmed is False
    assert result.process_stop_evidence.reason == "root-identity-unavailable"


def test_run_supervised_without_output_options_keeps_existing_behavior(tmp_path: Path):
    child = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.write('plain-out'); sys.stderr.write('plain-err')",
    ]

    result = run_supervised(
        child,
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
        lifecycle=RuntimeProcessLifecycle(job_id="job-plain", generation="gen-plain"),
    )

    assert result.exit_code == 0
    assert result.outcome == "exited"
    assert result.stdout == "plain-out"
    assert result.stderr == "plain-err"
    assert result.stdout_bytes is None
    assert result.stderr_bytes is None
    assert result.output_limit_exceeded is False
    assert result.process_stop_evidence.confirmed is True


@pytest.mark.skipif(os.name != "nt", reason="Kill-on-close ownership is Windows-specific")
def test_windows_owner_death_kills_job_tree(tmp_path: Path):
    native_python, native_env = _native_python_launch()
    grandchild_script = _write_script(
        tmp_path / "owner_grandchild.py",
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "pid_file = pathlib.Path(sys.argv[1])\n"
        "ready = pathlib.Path(sys.argv[2])\n"
        "pid_file.write_text(str(os.getpid()), encoding='utf-8')\n"
        "ready.write_text('ready', encoding='utf-8')\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
    )
    root_script = _write_script(
        tmp_path / "owner_root.py",
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "python = sys.argv[1]\n"
        "child = subprocess.Popen([python, sys.argv[2], sys.argv[3], sys.argv[4]], stdout=sys.stdout, stderr=sys.stderr, close_fds=False, env=os.environ.copy())\n"
        "child.wait()\n",
    )
    worker_script = _write_script(
        tmp_path / "owner_worker.py",
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "from flowgency.jobs.processes import RuntimeProcessLifecycle, run_supervised\n"
        "root_script = pathlib.Path(sys.argv[1])\n"
        "grandchild_script = pathlib.Path(sys.argv[2])\n"
        "ready = pathlib.Path(sys.argv[3])\n"
        "pid_file = pathlib.Path(sys.argv[4])\n"
        "python = sys.argv[5]\n"
        "env = os.environ.copy()\n"
        "run_supervised([python, str(root_script), python, str(grandchild_script), str(pid_file), str(ready)], cwd=root_script.parent, env=env, timeout=30, lifecycle=RuntimeProcessLifecycle(job_id='job-owner', generation='gen-owner'))\n",
    )
    ready = tmp_path / "owner-ready.txt"
    pid_file = tmp_path / "owner-grandchild.pid"
    captured, capture_thread = _capture_windows_identity_when_ready(
        ready,
        pid_file,
        timeout=10,
    )

    worker = __import__("subprocess").Popen(
        [native_python, str(worker_script), str(root_script), str(grandchild_script), str(ready), str(pid_file), native_python],
        cwd=tmp_path,
        env=native_env,
    )
    try:
        _wait_for_path(ready, timeout=5)
        capture_thread.join(timeout=1)
        assert not capture_thread.is_alive()
        descendant_identity = captured.get("identity")
        assert isinstance(descendant_identity, RuntimeProcessIdentity)
        worker.kill()
        assert worker.wait(timeout=10) != 0
        _wait_for_process_exit(descendant_identity, timeout=5)
        import win32con
        import win32process

        handle = captured.get("handle")
        assert handle is not None
        assert win32process.GetExitCodeProcess(handle) != win32con.STILL_ACTIVE
    finally:
        handle = captured.get("handle")
        if handle is not None:
            handle.Close()
        if capture_thread.is_alive():
            capture_thread.join(timeout=0.1)
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
