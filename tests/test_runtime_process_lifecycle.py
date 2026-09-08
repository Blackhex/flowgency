from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from flowgency.jobs.processes import (
    RuntimeProcessLifecycle,
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


def test_run_supervised_timeout_kills_descendants(tmp_path: Path):
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
        "import subprocess\n"
        "import sys\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]], stdout=sys.stdout, stderr=sys.stderr, close_fds=False)\n"
        "child.wait()\n",
    )
    root_script = _write_script(
        tmp_path / "root_wait.py",
        "import subprocess\n"
        "import sys\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]], stdout=sys.stdout, stderr=sys.stderr, close_fds=False)\n"
        "child.wait()\n",
    )
    pid_file = tmp_path / "grandchild.pid"
    ready = tmp_path / "ready.txt"
    release = tmp_path / "release.txt"
    in_job_file = tmp_path / "grandchild.in_job"

    result = run_supervised(
        [
            sys.executable,
            str(root_script),
            str(child_script),
            str(grandchild_script),
            str(pid_file),
            str(ready),
            str(release),
            str(in_job_file),
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-timeout", generation="gen-timeout"),
    )

    _wait_for_path(ready, timeout=5)
    grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
    grandchild_in_job = in_job_file.read_text(encoding="utf-8") == "1"
    grandchild_identity = read_process_identity(grandchild_pid)

    if grandchild_in_job and grandchild_identity is not None and process_identity_state(grandchild_identity) != "alive":
        assert result.process_stop_evidence.confirmed is True
        assert result.process_stop_evidence.reason == "timeout"
    else:
        release.write_text("release", encoding="utf-8")
        if grandchild_identity is not None:
            _wait_for_process_exit(grandchild_identity, timeout=5)
        assert result.process_stop_evidence.confirmed is False
        assert result.process_stop_evidence.reason == "io-drain-incomplete"
    assert result.exit_code == 124


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
        "flowgency.jobs.processes._job_active_processes",
        lambda _job_handle: 1,
    )

    result = run_supervised(
        [sys.executable, str(child_script)],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-nonzero", generation="gen-nonzero"),
    )

    assert result.exit_code == 0
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

    monkeypatch.setattr("flowgency.jobs.processes.os.name", "posix")
    monkeypatch.setattr("flowgency.jobs.processes._read_posix_process_created_at", lambda pid: ("98765", "ok") if pid == 4321 else (None, "exited"))

    def deny_signal(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("flowgency.jobs.processes.os.kill", deny_signal)

    assert process_identity_state(identity) == "unknown"


def test_posix_read_process_identity_parses_proc_stat_with_spaces(monkeypatch):
    original_read_text = Path.read_text
    sample = (
        "4321 (python worker (child)) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 98765 21 22\n"
    )

    def fake_read_text(self, *args, **kwargs):
        if str(self).replace("\\", "/") == "/proc/4321/stat":
            return sample
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr("flowgency.jobs.processes.os.name", "posix")
    monkeypatch.setattr(Path, "read_text", fake_read_text)

    assert read_process_identity(4321) == RuntimeProcessIdentity(pid=4321, created_at="98765")


@pytest.mark.skipif(os.name != "nt", reason="Kill-on-close ownership is Windows-specific")
def test_windows_owner_death_kills_job_tree(tmp_path: Path):
    root_script = _write_script(
        tmp_path / "owner_root.py",
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
    worker_script = _write_script(
        tmp_path / "owner_worker.py",
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "import win32con\n"
        "import win32job\n"
        "import win32process\n"
        "root_script = pathlib.Path(sys.argv[1])\n"
        "ready = pathlib.Path(sys.argv[2])\n"
        "pid_file = pathlib.Path(sys.argv[3])\n"
        "job = win32job.CreateJobObject(None, '')\n"
        "limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)\n"
        "limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE\n"
        "win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)\n"
        "startup = win32process.STARTUPINFO()\n"
        "process_handle, thread_handle, pid, _ = win32process.CreateProcess(None, subprocess.list2cmdline([sys.executable, str(root_script), str(pid_file), str(ready)]), None, None, False, getattr(subprocess, 'CREATE_NO_WINDOW', 0) | win32con.CREATE_SUSPENDED, os.environ.copy(), str(root_script.parent), startup)\n"
        "win32job.AssignProcessToJobObject(job, process_handle)\n"
        "win32process.ResumeThread(thread_handle)\n"
        "deadline = time.monotonic() + 10\n"
        "while not ready.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n"
        "time.sleep(0.2)\n"
        "os._exit(0)\n",
    )
    ready = tmp_path / "owner-ready.txt"
    pid_file = tmp_path / "owner-root.pid"

    worker = __import__("subprocess").Popen(
        [sys.executable, str(worker_script), str(root_script), str(ready), str(pid_file)],
        cwd=tmp_path,
    )
    assert worker.wait(timeout=10) == 0
    _wait_for_path(ready, timeout=5)
    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    descendant_identity = read_process_identity(descendant_pid)

    if descendant_identity is not None:
        _wait_for_process_exit(descendant_identity, timeout=5)

