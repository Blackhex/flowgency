"""Proves a WindowsJobOwner assigns its helper to a kill-on-close Job before ever resuming it,
and that Job membership genuinely contains an immediate grandchild until Stop is called."""

from __future__ import annotations

import contextlib
import json
import msvcrt
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.windows_pty_protocol import FrameType, encode_start, read_frame, write_frame

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-specific")

if os.name == "nt":
    import pywintypes
    import win32api
    import win32con
    import win32event
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
    helper_pid = None

    def fail_assign(job, process):
        nonlocal helper_pid
        helper_pid = win32process.GetProcessId(process)
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def recording_resume(thread):
        resume_calls.append(thread)
        return win32process.ResumeThread(thread)

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", fail_assign)
    monkeypatch.setattr(win32process, "ResumeThread", recording_resume)

    try:
        with pytest.raises(WindowsJobLaunchError) as raised:
            WindowsJobOwner.launch(
                (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                int(child_stdin), int(child_stdout),
            )
        assert raised.value.cleanup_confirmed is True
        assert raised.value.process_terminated is True
        assert raised.value.job_empty_confirmed is True
        assert resume_calls == []
        assert _uncertain == []
        assert helper_pid is not None
        assert not _process_exists(helper_pid)
    finally:
        _close_all(child_stdin, parent_stdin, parent_stdout, child_stdout)


@pytest.mark.parametrize("mode", ["terminate-fails", "wait-times-out"])
def test_unknown_cleanup_keeps_handles_until_helper_death_is_proved(monkeypatch, tmp_path, mode):
    assert _uncertain == [], "a previous test leaked an uncertain job registration"
    first_pipes = _make_std_pipes()
    second_pipes = _make_std_pipes()
    helper_pid = None

    real_terminate = win32process.TerminateProcess
    real_wait = win32event.WaitForSingleObject

    def fail_assign(job, process):
        nonlocal helper_pid
        helper_pid = win32process.GetProcessId(process)
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def maybe_fail_terminate(process, exit_code):
        if mode == "terminate-fails":
            raise pywintypes.error(5, "TerminateProcess", "Access is denied.")
        return real_terminate(process, exit_code)

    def maybe_timeout_wait(process, timeout_ms):
        if mode == "terminate-fails" or mode == "wait-times-out":
            return win32event.WAIT_TIMEOUT
        return real_wait(process, timeout_ms)

    try:
        with monkeypatch.context() as m:
            m.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            m.setattr(win32process, "TerminateProcess", maybe_fail_terminate)
            m.setattr(win32event, "WaitForSingleObject", maybe_timeout_wait)

            with pytest.raises(WindowsJobLaunchError) as raised:
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(first_pipes[0]), int(first_pipes[3]),
                )
            assert raised.value.cleanup_confirmed is False
            assert raised.value.process_terminated is False
            assert raised.value.job_empty_confirmed is True
            assert len(_uncertain) == 1

            with pytest.raises(WindowsJobLaunchError) as blocked:
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(second_pipes[0]), int(second_pipes[3]),
                )
            assert blocked.value.cleanup_confirmed is False
            assert len(_uncertain) == 1

        assert helper_pid is not None
        if mode == "terminate-fails":
            assert _process_exists(helper_pid)
            real_terminate(_uncertain[0].process_handle, 1)

        from flowgency.jobs.windows_job import _retry_uncertain

        _retry_uncertain(time.monotonic() + 5)
        assert _uncertain == []
        assert not _process_exists(helper_pid)
    finally:
        while _uncertain:
            entry = _uncertain[0]
            with contextlib.suppress(Exception):
                real_terminate(entry.process_handle, 1)
            with contextlib.suppress(Exception):
                real_wait(entry.process_handle, 5000)
            from flowgency.jobs.windows_job import _retry_uncertain

            with contextlib.suppress(Exception):
                _retry_uncertain(time.monotonic() + 5)
        _close_all(*first_pipes, *second_pipes)


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


def test_exit_code_probe_failure_after_signaled_wait_keeps_uncertain_handle(monkeypatch, tmp_path):
    assert _uncertain == [], "a previous test leaked an uncertain job registration"
    first_pipes = _make_std_pipes()
    second_pipes = _make_std_pipes()

    real_wait = win32event.WaitForSingleObject

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
    try:
        with monkeypatch.context() as m:
            m.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            m.setattr(win32event, "WaitForSingleObject", signaled_wait)
            m.setattr(win32process, "GetExitCodeProcess", fail_exit_code)

            with pytest.raises(WindowsJobLaunchError) as raised:
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(first_pipes[0]), int(first_pipes[3]),
                )
            assert raised.value.cleanup_confirmed is False
            assert raised.value.process_terminated is False
            assert raised.value.job_empty_confirmed is True
            assert len(_uncertain) == 1

            with pytest.raises(WindowsJobLaunchError) as blocked:
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(second_pipes[0]), int(second_pipes[3]),
                )
            assert blocked.value.cleanup_confirmed is False
            assert len(_uncertain) == 1

        from flowgency.jobs.windows_job import _retry_uncertain

        _retry_uncertain(time.monotonic() + 5)
        assert _uncertain == []

        replacement = WindowsJobOwner.launch(
            (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
            int(second_pipes[0]), int(second_pipes[3]),
        )
    finally:
        cleanup_error = None
        retained_entry = _uncertain[0] if _uncertain else None
        if retained_entry is not None:
            with contextlib.suppress(Exception):
                win32process.TerminateProcess(retained_entry.process_handle, 1)
            with contextlib.suppress(Exception):
                real_wait(retained_entry.process_handle, 5000)
            from flowgency.jobs.windows_job import _retry_uncertain

            try:
                _retry_uncertain(time.monotonic() + 5)
            except Exception as error:
                cleanup_error = error
        if replacement is not None:
            confirmed, _reason = replacement.stop(time.monotonic() + 5)
            assert confirmed
            replacement.close_confirmed()
        _close_all(*first_pipes, *second_pipes)
        if cleanup_error is not None:
            raise cleanup_error
        if _uncertain:
            pytest.fail("bounded cleanup did not confirm the retained uncertain handle")


def test_retry_uncertain_exit_code_probe_failure_raises_structured_error(monkeypatch, tmp_path):
    assert _uncertain == [], "a previous test leaked an uncertain job registration"
    pipes = _make_std_pipes()
    real_wait = win32event.WaitForSingleObject

    def fail_assign(job, process):
        del job, process
        raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")

    def signaled_wait(process, timeout_ms):
        del process, timeout_ms
        return win32event.WAIT_OBJECT_0

    def fail_exit_code(process):
        del process
        raise pywintypes.error(6, "GetExitCodeProcess", "The handle is invalid.")

    try:
        with monkeypatch.context() as m:
            m.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            m.setattr(win32event, "WaitForSingleObject", signaled_wait)
            m.setattr(win32process, "GetExitCodeProcess", fail_exit_code)

            with pytest.raises(WindowsJobLaunchError):
                WindowsJobOwner.launch(
                    (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                    int(pipes[0]), int(pipes[3]),
                )

        from flowgency.jobs.windows_job import _retry_uncertain

        with monkeypatch.context() as m:
            m.setattr(win32event, "WaitForSingleObject", signaled_wait)
            m.setattr(win32process, "GetExitCodeProcess", fail_exit_code)

            with pytest.raises(WindowsJobLaunchError) as raised:
                _retry_uncertain(time.monotonic() + 5)
        assert raised.value.cleanup_confirmed is False
        assert len(_uncertain) == 1
    finally:
        cleanup_error = None
        retained_entry = _uncertain[0] if _uncertain else None
        if retained_entry is not None:
            with contextlib.suppress(Exception):
                win32process.TerminateProcess(retained_entry.process_handle, 1)
            with contextlib.suppress(Exception):
                real_wait(retained_entry.process_handle, 5000)
            from flowgency.jobs.windows_job import _retry_uncertain

            try:
                _retry_uncertain(time.monotonic() + 5)
            except Exception as error:
                cleanup_error = error
        _close_all(*pipes)
        if cleanup_error is not None:
            raise cleanup_error
        if _uncertain:
            pytest.fail("bounded cleanup did not confirm the retained uncertain handle")


def test_resume_thread_failure_cleans_up_assigned_suspended_helper(monkeypatch, tmp_path):
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    helper_pid = None
    real_create_process = win32process.CreateProcess

    def recording_create_process(*args, **kwargs):
        nonlocal helper_pid
        process_handle, thread_handle, pid, thread_id = real_create_process(*args, **kwargs)
        helper_pid = pid
        return process_handle, thread_handle, pid, thread_id

    def fail_resume(thread):
        del thread
        raise pywintypes.error(6, "ResumeThread", "The handle is invalid.")

    monkeypatch.setattr(win32process, "CreateProcess", recording_create_process)
    monkeypatch.setattr(win32process, "ResumeThread", fail_resume)

    try:
        with pytest.raises(WindowsJobLaunchError) as raised:
            WindowsJobOwner.launch(
                (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
                int(child_stdin), int(child_stdout),
            )
        assert raised.value.cleanup_confirmed is True
        assert raised.value.process_terminated is True
        assert raised.value.job_empty_confirmed is True
        assert _uncertain == []
        assert helper_pid is not None
        assert not _process_exists(helper_pid)
    finally:
        _close_all(child_stdin, parent_stdin, parent_stdout, child_stdout)


def test_launch_conpty_method_exists() -> None:
    assert hasattr(WindowsJobOwner, "launch_conpty")


def _read_conpty_until(terminal, needle: bytes, *, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    collected = bytearray()
    while time.monotonic() < deadline:
        chunk = terminal.read()
        if chunk is None:
            time.sleep(0.02)
            continue
        if chunk == b"":
            break
        collected.extend(chunk)
        if needle in collected:
            return bytes(collected)
    raise AssertionError(f"Timed out waiting for {needle!r} in {bytes(collected)!r}")


def _wait_for_path(path: Path, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {path}")


@pytest.mark.skipif(os.name != "nt", reason="Atomic ConPTY launch is Windows-specific")
def test_launch_conpty_assigns_before_resume_and_contains_descendants(monkeypatch, tmp_path):
    from flowgency.jobs.windows_conpty import WindowsConPTY

    native_python, native_env = _native_python_launch()
    started = tmp_path / "started.txt"
    child_pid_path = tmp_path / "child.pid"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    child_script = _write_script(
        tmp_path / "conpty_child.py",
        "import os, pathlib, subprocess, sys, time\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "child_pid_path = pathlib.Path(sys.argv[2])\n"
        "grandchild_pid_path = pathlib.Path(sys.argv[3])\n"
        f"python = {native_python!r}\n"
        "grandchild = subprocess.Popen([python, '-c', 'import time; time.sleep(60)'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=os.environ.copy())\n"
        "marker.write_text('started', encoding='utf-8')\n"
        "child_pid_path.write_text(str(os.getpid()), encoding='utf-8')\n"
        "grandchild_pid_path.write_text(str(grandchild.pid), encoding='utf-8')\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
    )
    terminal = WindowsConPTY(24, 80)
    captured: dict[str, object] = {}
    original_create = __import__("flowgency.jobs.windows_job", fromlist=["create_suspended_conpty_process"]).create_suspended_conpty_process
    original_job_factory = __import__("flowgency.jobs.windows_job", fromlist=["create_kill_on_close_windows_job"]).create_kill_on_close_windows_job
    original_resume = win32process.ResumeThread
    owner = None

    def record_job():
        handle = original_job_factory()
        captured["job_handle"] = handle
        return handle

    def record_create(argv, cwd, env, pseudoconsole, job_handle):
        result = original_create(argv, cwd, env, pseudoconsole, job_handle)
        captured["process_handle"] = result[0]
        captured["pid"] = result[2]
        return result

    def record_resume(thread_handle):
        assert not started.exists()
        assert win32job.IsProcessInJob(captured["process_handle"], captured["job_handle"])
        return original_resume(thread_handle)

    monkeypatch.setattr("flowgency.jobs.windows_job.create_kill_on_close_windows_job", record_job)
    monkeypatch.setattr("flowgency.jobs.windows_job.create_suspended_conpty_process", record_create)
    monkeypatch.setattr(win32process, "ResumeThread", record_resume)
    stopped = False

    try:
        terminal.open()
        owner = WindowsJobOwner.launch_conpty(
            (
                native_python,
                "-u",
                str(child_script),
                str(started),
                str(child_pid_path),
                str(grandchild_pid_path),
            ),
            tmp_path,
            native_env,
            terminal.pseudoconsole,
        )
        terminal.close_child_ends()
        _wait_for_path(started, timeout=10)
        _wait_for_path(child_pid_path, timeout=5)
        _wait_for_path(grandchild_pid_path, timeout=5)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))
        assert win32process.GetExitCodeProcess(captured["process_handle"]) == win32con.STILL_ACTIVE
        assert owner.contains(child_pid)
        assert owner.contains(grandchild_pid)
        confirmed, reason = owner.stop(time.monotonic() + 5)
        assert confirmed, reason
        stopped = True
        assert win32process.GetExitCodeProcess(captured["process_handle"]) != win32con.STILL_ACTIVE
        assert not _process_exists(child_pid)
        assert not _process_exists(grandchild_pid)
        terminal.begin_close()
    finally:
        if owner is not None:
            if not stopped:
                confirmed, _reason = owner.stop(time.monotonic() + 5)
                stopped = confirmed
            if stopped:
                owner.close_confirmed()
        terminal.begin_close()
        assert terminal.wait_closed(time.monotonic() + 5)
        terminal.close_streams()


def test_launch_conpty_post_create_failure_keeps_uncertain_handles_and_never_resumes(monkeypatch, tmp_path):
    from flowgency.jobs.windows_conpty import WindowsConPTYCreateProcessError
    from flowgency.jobs.processes import create_kill_on_close_windows_job

    assert _uncertain == [], "a previous test leaked an uncertain job registration"
    resume_calls = []
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()

    startup = win32process.STARTUPINFO()
    startup.dwFlags |= win32process.STARTF_USESTDHANDLES
    startup.hStdInput = int(child_stdin)
    startup.hStdOutput = int(child_stdout)
    startup.hStdError = int(child_stdout)
    process_handle, thread_handle, pid, thread_id = win32process.CreateProcess(
        None,
        subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(60)"]),
        None,
        None,
        True,
        win32process.CREATE_SUSPENDED,
        os.environ.copy(),
        str(tmp_path),
        startup,
    )
    job_handle = create_kill_on_close_windows_job()
    win32job.AssignProcessToJobObject(job_handle, process_handle)

    def fail_after_create(argv, cwd, env, pseudoconsole, passed_job_handle):
        del argv, cwd, env, pseudoconsole
        assert int(passed_job_handle) == int(job_handle)
        raise WindowsConPTYCreateProcessError(
            "attribute cleanup failed",
            process_handle=int(process_handle),
            thread_handle=int(thread_handle),
            pid=pid,
            thread_id=thread_id,
        )

    def record_resume(thread):
        resume_calls.append(thread)
        return win32process.ResumeThread(thread)

    def return_existing_job():
        return job_handle

    monkeypatch.setattr("flowgency.jobs.windows_job.create_kill_on_close_windows_job", return_existing_job)
    monkeypatch.setattr("flowgency.jobs.windows_job.create_suspended_conpty_process", fail_after_create)
    monkeypatch.setattr(win32process, "ResumeThread", record_resume)

    try:
        with pytest.raises(WindowsJobLaunchError) as raised:
            WindowsJobOwner.launch_conpty(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                tmp_path,
                os.environ.copy(),
                0x1234,
            )
        assert raised.value.cleanup_confirmed is True
        assert raised.value.process_terminated is True
        assert raised.value.job_empty_confirmed is True
        assert resume_calls == []
        assert _uncertain == []
        assert not _process_exists(pid)
    finally:
        _close_all(child_stdin, parent_stdin, parent_stdout, child_stdout)


def _owner_process_launch_conpty_script(native_python: str, child_script_path: str, ready_path: str) -> str:
    return (
        "import os, pathlib, sys, time\n"
        "from flowgency.jobs.windows_conpty import WindowsConPTY\n"
        "from flowgency.jobs.windows_job import WindowsJobOwner\n"
        "terminal = WindowsConPTY(24, 80)\n"
        "terminal.open()\n"
        "owner = WindowsJobOwner.launch_conpty(\n"
        f"    ({native_python!r}, '-u', {child_script_path!r}, {ready_path!r}),\n"
        "    pathlib.Path(os.getcwd()),\n"
        "    os.environ.copy(),\n"
        "    terminal.pseudoconsole,\n"
        ")\n"
        "terminal.close_child_ends()\n"
        "sys.stdout.write(str(owner.pid) + chr(10))\n"
        "sys.stdout.flush()\n"
        "time.sleep(300)\n"
    )


@pytest.mark.skipif(os.name != "nt", reason="Atomic ConPTY launch is Windows-specific")
def test_launch_conpty_owner_crash_closes_sole_job_handle_and_reaps_tree(tmp_path):
    native_python, native_env = _native_python_launch()
    ready = tmp_path / "ready.txt"
    child_pid_path = tmp_path / "child.pid"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    child_script = _write_script(
        tmp_path / "owner_conpty_child.py",
        "import os, pathlib, subprocess, sys, time\n"
        f"python = {native_python!r}\n"
        "ready = pathlib.Path(sys.argv[1])\n"
        f"child_pid_path = pathlib.Path({str(child_pid_path)!r})\n"
        f"grandchild_pid_path = pathlib.Path({str(grandchild_pid_path)!r})\n"
        "grandchild = subprocess.Popen([python, '-c', 'import time; time.sleep(60)'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=os.environ.copy())\n"
        "child_pid_path.write_text(str(os.getpid()), encoding='utf-8')\n"
        "grandchild_pid_path.write_text(str(grandchild.pid), encoding='utf-8')\n"
        "ready.write_text('ready', encoding='utf-8')\n"
        "print('ready', flush=True)\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
    )
    owner_script = _write_script(
        tmp_path / "owner_conpty_process.py",
        _owner_process_launch_conpty_script(native_python, str(child_script), str(ready)),
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
        assert line.strip().isdigit()
        _wait_for_path(ready, timeout=5)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))
        assert _process_exists(child_pid)
        assert _process_exists(grandchild_pid)

        owner_process.terminate()
        owner_process.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            _process_exists(child_pid) or _process_exists(grandchild_pid)
        ):
            time.sleep(0.05)

        assert not _process_exists(child_pid)
        assert not _process_exists(grandchild_pid)
    finally:
        if owner_process.poll() is None:
            with contextlib.suppress(Exception):
                owner_process.kill()
            with contextlib.suppress(Exception):
                owner_process.wait(timeout=5)
        with contextlib.suppress(Exception):
            owner_process.stdout.close()


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


def _child_spawns_grandchild_script(native_python: str) -> str:
    # Runs as the CHILD (spawned by the helper that WindowsJobOwner.launch starts directly).
    # Spawns a GRANDCHILD immediately and reports both real pids on its own stdout so the helper
    # can relay them; neither identity is launched by the test process itself.
    return (
        "import os, subprocess, sys\n"
        f"python = {native_python!r}\n"
        "grandchild = subprocess.Popen(\n"
        "    [python, '-c', 'import time; time.sleep(60)'],\n"
        "    creationflags=subprocess.CREATE_NO_WINDOW,\n"
        "    env=os.environ.copy(),\n"
        ")\n"
        "sys.stdout.write(str(os.getpid()) + '\\n' + str(grandchild.pid) + '\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
    )


def _helper_spawns_child_script(native_python: str, child_script_path: str) -> str:
    # Runs as the HELPER (the process WindowsJobOwner.launch starts as argv[0]). Spawns the CHILD
    # script above, relays its (child_pid, grandchild_pid) report, and adds its own pid so the
    # test observes helper, child, and grandchild identities on the same real, contained tree.
    return (
        "import os, subprocess, sys\n"
        f"python = {native_python!r}\n"
        f"child_script = {child_script_path!r}\n"
        "child = subprocess.Popen(\n"
        "    [python, child_script],\n"
        "    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,\n"
        "    creationflags=subprocess.CREATE_NO_WINDOW,\n"
        "    env=os.environ.copy(),\n"
        ")\n"
        "child_pid = int(child.stdout.readline().strip())\n"
        "grandchild_pid = int(child.stdout.readline().strip())\n"
        "sys.stdout.write(\n"
        "    str(os.getpid()) + '\\n' + str(child_pid) + '\\n' + str(grandchild_pid) + '\\n'\n"
        ")\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
    )


def test_launch_contains_helper_child_and_grandchild_until_stop(tmp_path):
    native_python, native_env = _native_python_launch()
    child_script = _write_script(
        tmp_path / "child_script.py", _child_spawns_grandchild_script(native_python)
    )
    helper_script = _write_script(
        tmp_path / "helper_script.py",
        _helper_spawns_child_script(native_python, str(child_script)),
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
        child_pid = int(reader.readline().strip())
        grandchild_pid = int(reader.readline().strip())

        assert owner.contains(helper_pid)
        assert owner.contains(child_pid)
        assert owner.contains(grandchild_pid)
        assert owner.alive()

        confirmed, reason = owner.stop(time.monotonic() + 5)
        assert confirmed, reason
        assert not owner.alive()
        # Our own still-open process_handle keeps the terminated process's kernel object (and
        # its Job association) alive until close_confirmed(); prove real death via a fresh,
        # independent OpenProcess instead of contains(), which would still see the zombie.
        assert not _process_exists(helper_pid)
        assert not _process_exists(child_pid)
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
        "child_pid = int(reader.readline().strip())\n"
        "grandchild_pid = int(reader.readline().strip())\n"
        "sys.stdout.write(\n"
        "    str(owner.pid) + ' ' + str(helper_pid) + ' ' + str(child_pid) + ' '\n"
        "    + str(grandchild_pid) + chr(10)\n"
        ")\n"
        "sys.stdout.flush()\n"
        "time.sleep(300)\n"
    )


def test_job_close_reaps_tree_after_owner_crash(tmp_path):
    native_python, native_env = _native_python_launch()
    child_script = _write_script(
        tmp_path / "owner_child_script.py", _child_spawns_grandchild_script(native_python)
    )
    helper_script = _write_script(
        tmp_path / "owner_helper_script.py",
        _helper_spawns_child_script(native_python, str(child_script)),
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
        _owner_pid_str, helper_pid_str, child_pid_str, grandchild_pid_str = line.split()
        helper_pid = int(helper_pid_str)
        child_pid = int(child_pid_str)
        grandchild_pid = int(grandchild_pid_str)

        assert _process_exists(helper_pid)
        assert _process_exists(child_pid)
        assert _process_exists(grandchild_pid)

        # Simulate an owner crash: no Stop, no close_confirmed. Its non-inherited Job handle
        # is the last handle referencing the Job, so the OS closes it on process exit and the
        # kill-on-close limit reaps the whole contained tree.
        owner_process.terminate()
        owner_process.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            _process_exists(helper_pid)
            or _process_exists(child_pid)
            or _process_exists(grandchild_pid)
        ):
            time.sleep(0.05)

        assert not _process_exists(helper_pid), "helper survived its Job owner's crash"
        assert not _process_exists(child_pid), "child survived its Job owner's crash"
        assert not _process_exists(grandchild_pid), "grandchild survived its Job owner's crash"
    finally:
        if owner_process.poll() is None:
            with contextlib.suppress(Exception):
                owner_process.kill()
            with contextlib.suppress(Exception):
                owner_process.wait(timeout=5)
        with contextlib.suppress(Exception):
            owner_process.stdout.close()


def _write_connected_child(path: Path, native_python: str) -> Path:
    path.write_text(
        "import os, subprocess, sys\n"
        f"python = {native_python!r}\n"
        "grandchild = subprocess.Popen(\n"
        "    [python, '-c', 'import time; time.sleep(60)'],\n"
        "    creationflags=subprocess.CREATE_NO_WINDOW,\n"
        "    env=os.environ.copy(),\n"
        ")\n"
        "sys.stdout.write('argv-ok:' + sys.argv[1] + '\\n')\n"
        "sys.stdout.write('env-ok:' + os.environ['FLOWGENCY_TEST_VALUE'] + '\\n')\n"
        "sys.stdout.write('grandchild-pid:' + str(grandchild.pid) + '\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n",
        encoding="utf-8",
    )
    return path


def _write_connected_child_that_exits(path: Path) -> Path:
    path.write_text(
        "import sys\n"
        "sys.stdout.write('child-finished\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    return path


def test_windows_pty_helper_runs_conpty_inside_owned_job(tmp_path):
    native_python, native_env = _native_python_launch()
    child_script = _write_connected_child(tmp_path / "connected_child.py", native_python)
    launch_env = dict(native_env)
    launch_env["FLOWGENCY_TEST_VALUE"] = "žluťoučký"

    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    owner = None
    parent_input = None
    parent_output = None
    reader = None
    reader_done = threading.Event()
    frames: queue.Queue[tuple[FrameType, bytes] | None | BaseException] = queue.Queue()

    def drain_frames() -> None:
        try:
            while True:
                frame = read_frame(parent_output)
                frames.put(frame)
                if frame is None:
                    break
        except BaseException as error:
            frames.put(error)
        finally:
            reader_done.set()

    def next_frame(timeout: float):
        frame = frames.get(timeout=timeout)
        if isinstance(frame, BaseException):
            raise frame
        return frame

    try:
        owner = WindowsJobOwner.launch(
            (native_python, "-u", "-m", "flowgency.jobs.windows_pty_helper"),
            tmp_path,
            native_env,
            int(child_stdin),
            int(child_stdout),
        )
        win32api.CloseHandle(child_stdin)
        win32api.CloseHandle(child_stdout)
        parent_input = os.fdopen(
            msvcrt.open_osfhandle(parent_stdin.Detach(), os.O_WRONLY | os.O_BINARY),
            "wb",
            buffering=0,
        )
        parent_output = os.fdopen(
            msvcrt.open_osfhandle(parent_stdout.Detach(), os.O_RDONLY | os.O_BINARY),
            "rb",
            buffering=0,
        )
        reader = threading.Thread(target=drain_frames, daemon=True)
        reader.start()

        write_frame(
            parent_input,
            FrameType.START,
            encode_start(
                RuntimeLaunch(
                    (native_python, str(child_script), "Příprava"),
                    tmp_path,
                    launch_env,
                    "connected",
                ),
                24,
                80,
            ),
        )

        kind, payload = next_frame(10)
        assert kind is FrameType.READY
        assert owner.contains(json.loads(payload)["pid"])

        output = bytearray()
        saw_argv = False
        saw_env = False
        saw_grandchild = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                frame = next_frame(min(0.5, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            assert frame is not None
            if frame[0] is not FrameType.OUTPUT:
                continue
            output.extend(frame[1])
            if b"argv-ok:P\xc5\x99\xc3\xadprava" in output:
                saw_argv = True
            if b"env-ok:\xc5\xbelu\xc5\xa5ou\xc4\x8dk\xc3\xbd" in output:
                saw_env = True
            match = re.search(rb"grandchild-pid:(\d+)", output)
            if match:
                saw_grandchild = True
                assert owner.contains(int(match.group(1)))
            if saw_argv and saw_env and saw_grandchild:
                break
        else:
            raise AssertionError(f"ConPTY child output incomplete: {output!r}")
    finally:
        if owner is not None:
            assert owner.stop(time.monotonic() + 5) == (True, "stopped")
        if parent_input is not None:
            parent_input.close()
        if parent_output is not None:
            assert reader_done.wait(timeout=5), "PTY pipe reader did not drain after Job exit"
            if reader is not None:
                reader.join(timeout=0)
                assert not reader.is_alive()
            parent_output.close()
        if owner is not None:
            owner.close_confirmed()


def test_windows_pty_helper_exits_when_child_finishes_with_control_pipe_open(tmp_path):
    native_python, native_env = _native_python_launch()
    child_script = _write_connected_child_that_exits(tmp_path / "connected_child_exit.py")

    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    owner = None
    parent_input = None
    parent_output = None
    reader = None
    reader_done = threading.Event()
    frames: queue.Queue[tuple[FrameType, bytes] | None | BaseException] = queue.Queue()

    def drain_frames() -> None:
        try:
            while True:
                frame = read_frame(parent_output)
                frames.put(frame)
                if frame is None:
                    break
        except BaseException as error:
            frames.put(error)
        finally:
            reader_done.set()

    def next_frame(timeout: float):
        frame = frames.get(timeout=timeout)
        if isinstance(frame, BaseException):
            raise frame
        return frame

    try:
        owner = WindowsJobOwner.launch(
            (native_python, "-u", "-m", "flowgency.jobs.windows_pty_helper"),
            tmp_path,
            native_env,
            int(child_stdin),
            int(child_stdout),
        )
        win32api.CloseHandle(child_stdin)
        win32api.CloseHandle(child_stdout)
        parent_input = os.fdopen(
            msvcrt.open_osfhandle(parent_stdin.Detach(), os.O_WRONLY | os.O_BINARY),
            "wb",
            buffering=0,
        )
        parent_output = os.fdopen(
            msvcrt.open_osfhandle(parent_stdout.Detach(), os.O_RDONLY | os.O_BINARY),
            "rb",
            buffering=0,
        )
        reader = threading.Thread(target=drain_frames, daemon=True)
        reader.start()

        write_frame(
            parent_input,
            FrameType.START,
            encode_start(
                RuntimeLaunch((native_python, str(child_script)), tmp_path, native_env, "connected"),
                24,
                80,
            ),
        )

        kind, payload = next_frame(10)
        assert kind is FrameType.READY
        assert owner.contains(json.loads(payload)["pid"])

        saw_output = False
        saw_exit = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            frame = next_frame(max(0.01, deadline - time.monotonic()))
            assert frame is not None
            if frame[0] is FrameType.OUTPUT:
                saw_output = saw_output or b"child-finished" in frame[1]
                continue
            if frame[0] is FrameType.EXIT:
                saw_exit = True
                assert json.loads(frame[1]) == {"exit_code": 0}
                break
            if frame[0] is FrameType.ERROR:
                raise AssertionError(frame[1].decode("utf-8", errors="strict"))
        assert saw_output
        assert saw_exit
    finally:
        if owner is not None:
            assert owner.stop(time.monotonic() + 5) == (True, "stopped")
        if parent_input is not None:
            parent_input.close()
        if parent_output is not None:
            assert reader_done.wait(timeout=5), "PTY pipe reader did not drain after child exit"
            if reader is not None:
                reader.join(timeout=0)
                assert not reader.is_alive()
            parent_output.close()
        if owner is not None:
            owner.close_confirmed()
