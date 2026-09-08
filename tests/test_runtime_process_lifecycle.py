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
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "pid_file = pathlib.Path(sys.argv[1])\n"
        "ready = pathlib.Path(sys.argv[2])\n"
        "release = pathlib.Path(sys.argv[3])\n"
        "pid_file.write_text(str(os.getpid()), encoding='utf-8')\n"
        "ready.write_text('ready', encoding='utf-8')\n"
        "while not release.exists():\n"
        "    time.sleep(0.05)\n",
    )
    child_script = _write_script(
        tmp_path / "child_wait.py",
        "import subprocess\n"
        "import sys\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]])\n"
        "child.wait()\n",
    )
    root_script = _write_script(
        tmp_path / "root_wait.py",
        "import subprocess\n"
        "import sys\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]])\n"
        "child.wait()\n",
    )
    pid_file = tmp_path / "grandchild.pid"
    ready = tmp_path / "ready.txt"
    release = tmp_path / "release.txt"

    result = run_supervised(
        [
            sys.executable,
            str(root_script),
            str(child_script),
            str(grandchild_script),
            str(pid_file),
            str(ready),
            str(release),
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=1,
        lifecycle=RuntimeProcessLifecycle(job_id="job-timeout", generation="gen-timeout"),
    )

    _wait_for_path(ready, timeout=5)
    grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
    grandchild_identity = read_process_identity(grandchild_pid)

    if grandchild_identity is not None:
        _wait_for_process_exit(grandchild_identity, timeout=5)
    assert result.exit_code == 124
    assert result.process_stop_evidence.confirmed is True
    assert result.process_stop_evidence.reason == "timeout"


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


def test_pid_identity_mismatch_is_not_treated_as_the_same_process():
    identity = read_process_identity(os.getpid())

    assert identity is not None
    assert process_identity_matches(identity) is True
    assert process_identity_matches(type(identity)(pid=identity.pid, created_at="not-the-same")) is False


@pytest.mark.skipif(os.name != "nt", reason="Kill-on-close ownership is Windows-specific")
def test_windows_owner_death_kills_job_tree(tmp_path: Path):
    descendant_script = _write_script(
        tmp_path / "owner_descendant.py",
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "pid_file = pathlib.Path(sys.argv[1])\n"
        "ready = pathlib.Path(sys.argv[2])\n"
        "release = pathlib.Path(sys.argv[3])\n"
        "pid_file.write_text(str(os.getpid()), encoding='utf-8')\n"
        "ready.write_text('ready', encoding='utf-8')\n"
        "while not release.exists():\n"
        "    time.sleep(0.05)\n",
    )
    root_script = _write_script(
        tmp_path / "owner_root.py",
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "descendant_script = pathlib.Path(sys.argv[1])\n"
        "ready = pathlib.Path(sys.argv[2])\n"
        "pid_file = pathlib.Path(sys.argv[3])\n"
        "release = pathlib.Path(sys.argv[4])\n"
        "subprocess.Popen([sys.executable, str(descendant_script), str(pid_file), str(ready), str(release)])\n"
        "while not release.exists():\n"
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
        "from flowgency.jobs.processes import RuntimeProcessIdentity, _start_windows_owner_watchdog\n"
        "root_script = pathlib.Path(sys.argv[1])\n"
        "descendant_script = pathlib.Path(sys.argv[2])\n"
        "ready = pathlib.Path(sys.argv[3])\n"
        "pid_file = pathlib.Path(sys.argv[4])\n"
        "release = pathlib.Path(sys.argv[5])\n"
        "job = win32job.CreateJobObject(None, '')\n"
        "limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)\n"
        "limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE\n"
        "win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)\n"
        "startup = win32process.STARTUPINFO()\n"
        "process_handle, thread_handle, pid, _ = win32process.CreateProcess(None, subprocess.list2cmdline([sys.executable, str(root_script), str(descendant_script), str(ready), str(pid_file), str(release)]), None, None, False, getattr(subprocess, 'CREATE_NO_WINDOW', 0) | win32con.CREATE_SUSPENDED, os.environ.copy(), str(root_script.parent), startup)\n"
        "win32job.AssignProcessToJobObject(job, process_handle)\n"
        "watchdog = _start_windows_owner_watchdog(owner_pid=os.getpid(), root_identity=RuntimeProcessIdentity(pid=pid, created_at=None), stop_file=root_script.parent / '.watchdog-stop')\n"
        "win32process.ResumeThread(thread_handle)\n"
        "deadline = time.monotonic() + 10\n"
        "while not ready.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n"
        "time.sleep(0.2)\n"
        "os._exit(0)\n",
    )
    ready = tmp_path / "owner-ready.txt"
    pid_file = tmp_path / "owner-descendant.pid"
    release = tmp_path / "owner-release.txt"

    worker = __import__("subprocess").Popen(
        [sys.executable, str(worker_script), str(root_script), str(descendant_script), str(ready), str(pid_file), str(release)],
        cwd=tmp_path,
    )
    assert worker.wait(timeout=10) == 0
    _wait_for_path(ready, timeout=5)
    descendant_pid = int(pid_file.read_text(encoding="utf-8"))
    descendant_identity = read_process_identity(descendant_pid)

    assert descendant_identity is not None
    _wait_for_process_exit(descendant_identity, timeout=5)

