"""Proves a WindowsJobOwner assigns its helper to a kill-on-close Job before ever resuming it,
and that Job membership genuinely contains an immediate grandchild until Stop is called."""

from __future__ import annotations

import contextlib
import msvcrt
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-specific")

if os.name == "nt":
    import pywintypes
    import win32api
    import win32con
    import win32job
    import win32pipe
    import win32process
    import win32security

    from flowgency.jobs.windows_job import WindowsJobLaunchError, WindowsJobOwner, _uncertain


def _make_std_pipes():
    security = win32security.SECURITY_ATTRIBUTES()
    security.bInheritHandle = 1
    child_stdin, parent_stdin = win32pipe.CreatePipe(security, 0)
    parent_stdout, child_stdout = win32pipe.CreatePipe(security, 0)
    win32api.SetHandleInformation(parent_stdin, win32con.HANDLE_FLAG_INHERIT, 0)
    win32api.SetHandleInformation(parent_stdout, win32con.HANDLE_FLAG_INHERIT, 0)
    return child_stdin, parent_stdin, parent_stdout, child_stdout


def _close_all(*handles) -> None:
    for handle in handles:
        with contextlib.suppress(Exception):
            win32api.CloseHandle(handle)


def _process_exists(pid: int) -> bool:
    # A held handle (ours or the OS's own bookkeeping) keeps a terminated process's kernel
    # object queryable by pid; check the exit code rather than treating OpenProcess success
    # alone as proof of "still running".
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except pywintypes.error:
        return False
    try:
        return win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE
    finally:
        win32api.CloseHandle(handle)


def test_helper_is_assigned_before_resume(monkeypatch, tmp_path):
    events = []
    security = win32security.SECURITY_ATTRIBUTES()
    security.bInheritHandle = 1
    child_stdin, parent_stdin = win32pipe.CreatePipe(security, 0)
    parent_stdout, child_stdout = win32pipe.CreatePipe(security, 0)
    win32api.SetHandleInformation(parent_stdin, win32con.HANDLE_FLAG_INHERIT, 0)
    win32api.SetHandleInformation(parent_stdout, win32con.HANDLE_FLAG_INHERIT, 0)

    assign = win32job.AssignProcessToJobObject
    resume = win32process.ResumeThread

    def recording_assign(job, process):
        events.append("assign")
        return assign(job, process)

    def recording_resume(thread):
        events.append("resume")
        return resume(thread)

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", recording_assign)
    monkeypatch.setattr(win32process, "ResumeThread", recording_resume)
    owner = None
    try:
        owner = WindowsJobOwner.launch(
            (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
            int(child_stdin), int(child_stdout),
        )
        assert events == ["assign", "resume"]
    finally:
        if owner is not None:
            confirmed, _reason = owner.stop(time.monotonic() + 5)
            assert confirmed
            owner.close_confirmed()
        for handle in (child_stdin, parent_stdin, parent_stdout, child_stdout):
            win32api.CloseHandle(handle)


def test_assign_failure_never_resumes_and_terminates_suspended_helper(monkeypatch, tmp_path):
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    resume_calls = []

    def fail_assign(job, process):
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def recording_resume(thread):
        resume_calls.append(thread)
        return win32process.ResumeThread(thread)

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", fail_assign)
    monkeypatch.setattr(win32process, "ResumeThread", recording_resume)

    try:
        with pytest.raises(WindowsJobLaunchError):
            WindowsJobOwner.launch(
                (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                int(child_stdin), int(child_stdout),
            )
        assert resume_calls == []
        assert _uncertain == []
    finally:
        _close_all(child_stdin, parent_stdin, parent_stdout, child_stdout)


def test_unknown_job_accounting_retains_handle(monkeypatch, tmp_path):
    assert _uncertain == [], "a previous test leaked an uncertain job registration"
    first_pipes = _make_std_pipes()
    second_pipes = _make_std_pipes()

    real_query = win32job.QueryInformationJobObject

    def fail_assign(job, process):
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def fail_accounting_query(job, info_class):
        # Only the accounting read must fail; the job-creation limit query must keep working
        # or launch() would never reach AssignProcessToJobObject at all.
        if info_class == win32job.JobObjectBasicAccountingInformation:
            raise pywintypes.error(6, "QueryInformationJobObject", "The handle is invalid.")
        return real_query(job, info_class)

    try:
        with monkeypatch.context() as m:
            m.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            m.setattr(win32job, "QueryInformationJobObject", fail_accounting_query)

            with pytest.raises(WindowsJobLaunchError):
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(first_pipes[0]), int(first_pipes[3]),
                )
            assert len(_uncertain) == 1

            # A second launch attempt must be refused while the job's emptiness is unproven.
            with pytest.raises(WindowsJobLaunchError):
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(second_pipes[0]), int(second_pipes[3]),
                )
            assert len(_uncertain) == 1

        # Real accounting is restored outside the monkeypatch context; the retained job was
        # never assigned any process, so it resolves to empty and the registry clears.
        from flowgency.jobs.windows_job import _retry_uncertain

        _retry_uncertain(time.monotonic() + 5)
        assert _uncertain == []
    finally:
        _close_all(*first_pipes, *second_pipes)


def _write_script(path, body: str):
    path.write_text(body, encoding="utf-8")
    return path


def _native_python_launch() -> tuple[str, dict[str, str]]:
    # The venv's python.exe is a launcher stub that re-execs the real interpreter as a distinct
    # process; that would put the whole tree we mean to prove contained outside our Job. Resolve
    # the actually-loaded native image so the launched process IS the real interpreter, and set
    # __PYVENV_LAUNCHER__ so it still behaves like it was started through the venv.
    env = os.environ.copy()
    env["__PYVENV_LAUNCHER__"] = sys.executable
    return win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0), env


def _grandchild_helper_script(native_python: str) -> str:
    return (
        "import os, subprocess, sys\n"
        f"python = {native_python!r}\n"
        "gc = subprocess.Popen(\n"
        "    [python, '-c', 'import time; time.sleep(60)'],\n"
        "    creationflags=subprocess.CREATE_NO_WINDOW,\n"
        "    env=os.environ.copy(),\n"
        ")\n"
        "sys.stdout.write(str(os.getpid()) + '\\n' + str(gc.pid) + '\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
    )


def test_launch_contains_helper_child_and_grandchild_until_stop(tmp_path):
    native_python, native_env = _native_python_launch()
    helper_script = _write_script(
        tmp_path / "grandchild_helper.py", _grandchild_helper_script(native_python)
    )
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    owner = None
    reader = None
    try:
        owner = WindowsJobOwner.launch(
            (native_python, str(helper_script)), tmp_path, native_env,
            int(child_stdin), int(child_stdout),
        )
        fd = msvcrt.open_osfhandle(int(parent_stdout.Detach()), os.O_RDONLY)
        reader = os.fdopen(fd, "rb", closefd=True)
        helper_pid = int(reader.readline().strip())
        grandchild_pid = int(reader.readline().strip())

        assert owner.contains(helper_pid)
        assert owner.contains(grandchild_pid)
        assert owner.alive()

        confirmed, reason = owner.stop(time.monotonic() + 5)
        assert confirmed, reason
        assert not owner.alive()
        # Our own still-open process_handle keeps the terminated process's kernel object (and
        # its Job association) alive until close_confirmed(); prove real death via a fresh,
        # independent OpenProcess instead of contains(), which would still see the zombie.
        assert not _process_exists(helper_pid)
        assert not _process_exists(grandchild_pid)
        owner.close_confirmed()
    finally:
        if reader is not None:
            with contextlib.suppress(Exception):
                reader.close()
        _close_all(child_stdin, parent_stdin, child_stdout)


def _owner_process_script(native_python: str, helper_script_path: str) -> str:
    return (
        "import os, sys, time\n"
        "import win32api, win32con, win32pipe, win32security\n"
        "from flowgency.jobs.windows_job import WindowsJobOwner\n"
        "security = win32security.SECURITY_ATTRIBUTES()\n"
        "security.bInheritHandle = 1\n"
        "child_stdin, parent_stdin = win32pipe.CreatePipe(security, 0)\n"
        "parent_stdout, child_stdout = win32pipe.CreatePipe(security, 0)\n"
        "win32api.SetHandleInformation(parent_stdin, win32con.HANDLE_FLAG_INHERIT, 0)\n"
        "win32api.SetHandleInformation(parent_stdout, win32con.HANDLE_FLAG_INHERIT, 0)\n"
        "owner = WindowsJobOwner.launch(\n"
        f"    ({native_python!r}, {helper_script_path!r}), os.getcwd(), os.environ.copy(),\n"
        "    int(child_stdin), int(child_stdout),\n"
        ")\n"
        "import msvcrt\n"
        "fd = msvcrt.open_osfhandle(int(parent_stdout.Detach()), os.O_RDONLY)\n"
        "reader = os.fdopen(fd, 'rb', closefd=True)\n"
        "helper_pid = int(reader.readline().strip())\n"
        "grandchild_pid = int(reader.readline().strip())\n"
        "sys.stdout.write(str(owner.pid) + ' ' + str(helper_pid) + ' ' + str(grandchild_pid) + chr(10))\n"
        "sys.stdout.flush()\n"
        "time.sleep(300)\n"
    )


def test_job_close_reaps_tree_after_owner_crash(tmp_path):
    native_python, native_env = _native_python_launch()
    helper_script = _write_script(
        tmp_path / "owner_helper.py", _grandchild_helper_script(native_python)
    )
    owner_script = _write_script(
        tmp_path / "owner_process.py",
        _owner_process_script(native_python, str(helper_script)),
    )
    owner_process = subprocess.Popen(
        [native_python, str(owner_script)],
        stdout=subprocess.PIPE,
        cwd=str(tmp_path),
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=native_env,
    )
    try:
        line = owner_process.stdout.readline()
        assert line, "owner process reported nothing before exiting"
        _owner_pid_str, helper_pid_str, grandchild_pid_str = line.split()
        helper_pid, grandchild_pid = int(helper_pid_str), int(grandchild_pid_str)

        assert _process_exists(helper_pid)
        assert _process_exists(grandchild_pid)

        # Simulate an owner crash: no Stop, no close_confirmed. Its non-inherited Job handle
        # is the last handle referencing the Job, so the OS closes it on process exit and the
        # kill-on-close limit reaps the whole contained tree.
        owner_process.terminate()
        owner_process.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (_process_exists(helper_pid) or _process_exists(grandchild_pid)):
            time.sleep(0.05)

        assert not _process_exists(helper_pid), "helper survived its Job owner's crash"
        assert not _process_exists(grandchild_pid), "grandchild survived its Job owner's crash"
    finally:
        if owner_process.poll() is None:
            with contextlib.suppress(Exception):
                owner_process.kill()
            with contextlib.suppress(Exception):
                owner_process.wait(timeout=5)
        with contextlib.suppress(Exception):
            owner_process.stdout.close()
