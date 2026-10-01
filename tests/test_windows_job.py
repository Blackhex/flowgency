"""Proves WindowsJobOwner launch ordering, cleanup, and containment semantics."""

from __future__ import annotations

import contextlib
import json
import msvcrt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

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


def _open_process_query_handle(pid: int):
    return win32api.OpenProcess(
        win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.SYNCHRONIZE,
        False,
        pid,
    )


def _wait_for_known_exit(process_handle, *, timeout: float) -> int:
    wait_status = win32event.WaitForSingleObject(process_handle, max(0, int(timeout * 1000)))
    assert wait_status == win32event.WAIT_OBJECT_0
    exit_code = win32process.GetExitCodeProcess(process_handle)
    assert exit_code != win32con.STILL_ACTIVE
    return int(exit_code)


def test_child_is_assigned_before_resume(monkeypatch, tmp_path):
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


def test_assign_failure_never_resumes_and_terminates_suspended_child(monkeypatch, tmp_path):
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    resume_calls = []
    child_pid = None

    def fail_assign(job, process):
        nonlocal child_pid
        child_pid = win32process.GetProcessId(process)
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
        assert child_pid is not None
        assert not _process_exists(child_pid)
    finally:
        _close_all(child_stdin, parent_stdin, parent_stdout, child_stdout)


@pytest.mark.parametrize("mode", ["terminate-fails", "wait-times-out"])
def test_unknown_cleanup_keeps_handles_until_child_death_is_proved(monkeypatch, tmp_path, mode):
    assert _uncertain == [], "a previous test leaked an uncertain job registration"
    first_pipes = _make_std_pipes()
    second_pipes = _make_std_pipes()
    child_pid = None

    real_terminate = win32process.TerminateProcess
    real_wait = win32event.WaitForSingleObject

    def fail_assign(job, process):
        nonlocal child_pid
        child_pid = win32process.GetProcessId(process)
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

        assert child_pid is not None
        if mode == "terminate-fails":
            assert _process_exists(child_pid)
            real_terminate(_uncertain[0].process_handle, 1)

        from flowgency.jobs.windows_job import _retry_uncertain

        _retry_uncertain(time.monotonic() + 5)
        assert _uncertain == []
        assert not _process_exists(child_pid)
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


def test_resume_thread_failure_cleans_up_assigned_suspended_child(monkeypatch, tmp_path):
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    child_pid = None
    real_create_process = win32process.CreateProcess

    def recording_create_process(*args, **kwargs):
        nonlocal child_pid
        process_handle, thread_handle, pid, thread_id = real_create_process(*args, **kwargs)
        child_pid = pid
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
        assert child_pid is not None
        assert not _process_exists(child_pid)
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


@pytest.mark.skipif(os.name != "nt", reason="Atomic ConPTY launch is Windows-specific")
def test_launch_conpty_root_exit_keeps_job_alive_until_stop(tmp_path):
    from flowgency.jobs.windows_conpty import WindowsConPTY

    native_python, native_env = _native_python_launch()
    started = tmp_path / "root-exit-started.txt"
    grandchild_pid_path = tmp_path / "root-exit-grandchild.pid"
    child_script = _write_script(
        tmp_path / "conpty_root_exit.py",
        "import os, pathlib, subprocess, sys\n"
        f"python = {native_python!r}\n"
        "started = pathlib.Path(sys.argv[1])\n"
        "grandchild_pid_path = pathlib.Path(sys.argv[2])\n"
        "grandchild = subprocess.Popen([python, '-c', 'import time; time.sleep(60)'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=os.environ.copy())\n"
        "grandchild_pid_path.write_text(str(grandchild.pid), encoding='utf-8')\n"
        "started.write_text('ready', encoding='utf-8')\n"
        "sys.stdout.write('root-exited-marker\\n')\n"
        "sys.stdout.flush()\n",
    )
    terminal = WindowsConPTY(24, 80)
    owner = None
    stopped = False

    try:
        terminal.open()
        owner = WindowsJobOwner.launch_conpty(
            (native_python, "-u", str(child_script), str(started), str(grandchild_pid_path)),
            tmp_path,
            native_env,
            terminal.pseudoconsole,
        )
        terminal.close_child_ends()
        _wait_for_path(started, timeout=10)
        _wait_for_path(grandchild_pid_path, timeout=5)
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and owner.exit_code() is None:
            time.sleep(0.02)

        assert owner.exit_code() == 0
        assert owner.alive()
        assert owner.contains(grandchild_pid)

        confirmed, reason = owner.stop(time.monotonic() + 5)
        assert confirmed, reason
        stopped = True
        assert not owner.alive()
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
    mock_called = False
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
        nonlocal mock_called
        del argv, cwd, env, pseudoconsole
        mock_called = True
        assert int(passed_job_handle) == int(job_handle)
        assert int(process_handle) != 0
        assert int(thread_handle) != 0
        process_raw = process_handle.Detach()
        thread_raw = thread_handle.Detach()
        assert process_raw != 0
        assert thread_raw != 0
        assert int(process_handle) == 0
        assert int(thread_handle) == 0
        raise WindowsConPTYCreateProcessError(
            "attribute cleanup failed",
            process_handle=process_raw,
            thread_handle=thread_raw,
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
        assert mock_called is True
        assert raised.value.cleanup_confirmed is True
        assert raised.value.process_terminated is True
        assert raised.value.job_empty_confirmed is True
        assert resume_calls == []
        assert _uncertain == []
        assert not _process_exists(pid)
        assert int(process_handle) == 0
        assert int(thread_handle) == 0
    finally:
        if not mock_called:
            with contextlib.suppress(Exception):
                win32process.TerminateProcess(process_handle, 1)
            _close_all(process_handle, thread_handle, job_handle)
        _close_all(child_stdin, parent_stdin, parent_stdout, child_stdout)


def _owner_process_launch_conpty_script(
    native_python: str,
    child_script_path: str,
    ready_path: str,
    child_pid_path: str,
    grandchild_pid_path: str,
) -> str:
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
        f"ready = pathlib.Path({ready_path!r})\n"
        f"child_pid_path = pathlib.Path({child_pid_path!r})\n"
        f"grandchild_pid_path = pathlib.Path({grandchild_pid_path!r})\n"
        "deadline = time.monotonic() + 10\n"
        "while time.monotonic() < deadline and not (ready.exists() and child_pid_path.exists() and grandchild_pid_path.exists()):\n"
        "    time.sleep(0.05)\n"
        "child_pid = int(child_pid_path.read_text(encoding='utf-8'))\n"
        "grandchild_pid = int(grandchild_pid_path.read_text(encoding='utf-8'))\n"
        "sys.stdout.write(\n"
        "    str(owner.pid) + ' ' + str(child_pid) + ' ' + str(grandchild_pid) + ' ' +\n"
        "    str(owner.contains(child_pid)) + ' ' + str(owner.contains(grandchild_pid)) + chr(10)\n"
        ")\n"
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
        _owner_process_launch_conpty_script(
            native_python,
            str(child_script),
            str(ready),
            str(child_pid_path),
            str(grandchild_pid_path),
        ),
    )
    owner_process = subprocess.Popen(
        [native_python, str(owner_script)],
        stdout=subprocess.PIPE,
        cwd=str(tmp_path),
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=native_env,
    )
    root_handle = None
    grandchild_handle = None
    try:
        deadline = time.monotonic() + 5
        line = ""
        while time.monotonic() < deadline:
            candidate = owner_process.stdout.readline()
            assert candidate, "owner process reported nothing before exiting"
            if len(candidate.split()) == 5:
                line = candidate
                break
        assert line, "owner process never reported structured membership output"
        _wait_for_path(ready, timeout=5)
        root_pid_str, child_pid_str, grandchild_pid_str, child_in_job_str, grandchild_in_job_str = line.split()
        child_pid = int(child_pid_str)
        grandchild_pid = int(grandchild_pid_str)
        assert int(root_pid_str) == child_pid
        assert child_in_job_str == "True"
        assert grandchild_in_job_str == "True"

        root_handle = _open_process_query_handle(child_pid)
        grandchild_handle = _open_process_query_handle(grandchild_pid)
        assert win32process.GetExitCodeProcess(root_handle) == win32con.STILL_ACTIVE
        assert win32process.GetExitCodeProcess(grandchild_handle) == win32con.STILL_ACTIVE

        owner_process.terminate()
        owner_process.wait(timeout=5)

        assert _wait_for_known_exit(root_handle, timeout=5) is not None
        assert _wait_for_known_exit(grandchild_handle, timeout=5) is not None
        assert not _process_exists(child_pid)
        assert not _process_exists(grandchild_pid)
    finally:
        if grandchild_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(grandchild_handle)
        if root_handle is not None:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(root_handle)
        if owner_process.poll() is None:
            with contextlib.suppress(Exception):
                owner_process.kill()
            with contextlib.suppress(Exception):
                owner_process.wait(timeout=5)
        with contextlib.suppress(Exception):
            owner_process.stdout.close()


@pytest.mark.skipif(os.name != "nt", reason="Atomic ConPTY launch is Windows-specific")
def test_launch_conpty_drains_trailing_output_while_close_completes(tmp_path):
    from flowgency.jobs.windows_conpty import WindowsConPTY

    trailing_marker = b"TRAILING-MARKER-9f1b3f38"
    terminal = WindowsConPTY(24, 80)
    owner = None
    stopped = False
    drained = bytearray()
    reader_done = threading.Event()
    reader = None

    def drain_native_pty() -> None:
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                chunk = terminal.read()
                if chunk is None:
                    time.sleep(0.02)
                    continue
                if chunk == b"":
                    return
                drained.extend(chunk)
        finally:
            reader_done.set()

    try:
        terminal.open()
        owner = WindowsJobOwner.launch_conpty(
            (
                "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"Write-Output {trailing_marker.decode('ascii')}",
            ),
            tmp_path,
            os.environ.copy(),
            terminal.pseudoconsole,
        )
        terminal.close_child_ends()

        reader = threading.Thread(target=drain_native_pty, daemon=True)
        reader.start()

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and trailing_marker not in drained:
            time.sleep(0.02)
        assert trailing_marker in drained

        terminal.begin_close()
        confirmed, reason = owner.stop(time.monotonic() + 5)
        assert confirmed, reason
        stopped = True

        assert reader_done.wait(timeout=5), "native PTY reader did not drain to EOF"
        reader.join(timeout=0)
        assert not reader.is_alive()
        assert terminal.wait_closed(time.monotonic() + 5)
        assert trailing_marker in bytes(drained)
        assert terminal.read() == b""
    finally:
        if owner is not None:
            if not stopped:
                confirmed, _reason = owner.stop(time.monotonic() + 5)
                stopped = confirmed
            if stopped:
                owner.close_confirmed()
        terminal.begin_close()
        assert terminal.wait_closed(time.monotonic() + 5)
        if reader is not None and reader.is_alive():
            reader.join(timeout=0)
        terminal.close_streams()


@pytest.mark.skipif(os.name != "nt", reason="Atomic ConPTY launch is Windows-specific")
def test_launch_conpty_redirected_parent_stdout_stays_off_host_logs(tmp_path):
    script = (
        "import json, os, shutil, subprocess, sys, tempfile, time\n"
        "from pathlib import Path\n"
        "from flowgency.jobs.windows_conpty import WindowsConPTY\n"
        "from flowgency.jobs.windows_job import WindowsJobOwner\n"
        "root = Path.cwd()\n"
        "terminal = WindowsConPTY(24, 80)\n"
        "owner = None\n"
        "pty_output = bytearray()\n"
        "proof = {'marker_in_pty': False, 'job_empty': False, 'console_closed': False}\n"
        "private = tempfile.mkdtemp(prefix='redirected-parent-', dir=root)\n"
        "try:\n"
        "        terminal.open()\n"
        "        owner = WindowsJobOwner.launch_conpty((\n"
        "            'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',\n"
        "            '-NoLogo',\n"
        "            '-NoProfile',\n"
        "            '-Command',\n"
        "            \"Write-Output 'REDIRECTED-PARENT-MARKER'\",\n"
        "        ), Path(private), os.environ.copy(), terminal.pseudoconsole)\n"
        "        terminal.close_child_ends()\n"
        "        deadline = time.monotonic() + 4\n"
        "        while time.monotonic() < deadline:\n"
        "            chunk = terminal.read()\n"
        "            if chunk:\n"
        "                pty_output.extend(chunk)\n"
        "                if b'REDIRECTED-PARENT-MARKER' in pty_output:\n"
        "                    break\n"
        "            elif chunk == b'':\n"
        "                break\n"
        "            time.sleep(0.01)\n"
        "        proof['marker_in_pty'] = b'REDIRECTED-PARENT-MARKER' in pty_output\n"
        "finally:\n"
        "    if owner is not None:\n"
        "        proof['job_empty'], _reason = owner.stop(time.monotonic() + 5)\n"
        "    terminal.begin_close()\n"
        "    deadline = time.monotonic() + 5\n"
        "    while time.monotonic() < deadline:\n"
        "        chunk = terminal.read()\n"
        "        if chunk:\n"
        "            pty_output.extend(chunk)\n"
        "        if chunk == b'':\n"
        "            break\n"
        "        time.sleep(0.01)\n"
        "    proof['console_closed'] = terminal.wait_closed(time.monotonic() + 2)\n"
        "    if proof['job_empty'] and owner is not None:\n"
        "        owner.close_confirmed()\n"
        "    terminal.close_streams()\n"
        "    shutil.rmtree(private)\n"
        "proof['pty_bytes'] = len(pty_output)\n"
        "print(json.dumps(proof))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=25,
    )

    assert result.returncode == 0, result.stderr
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 1, result.stdout
    assert "REDIRECTED-PARENT-MARKER" not in result.stdout
    proof = json.loads(stdout_lines[0])
    assert proof["marker_in_pty"] is True, proof
    assert proof["job_empty"] is True, proof
    assert proof["console_closed"] is True, proof
    assert proof["pty_bytes"] > 0, proof


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
    # Runs as the CHILD (spawned by the root process that WindowsJobOwner.launch starts directly).
    # Spawns a GRANDCHILD immediately and reports both real pids on its own stdout so the root
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


def _root_spawns_child_script(native_python: str, child_script_path: str) -> str:
    # Runs as the ROOT process that WindowsJobOwner.launch starts as argv[0]. Spawns the CHILD
    # script above, relays its (child_pid, grandchild_pid) report, and adds its own pid so the
    # test observes root, child, and grandchild identities on the same real, contained tree.
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


def test_launch_contains_root_child_and_grandchild_until_stop(tmp_path):
    native_python, native_env = _native_python_launch()
    child_script = _write_script(
        tmp_path / "child_script.py", _child_spawns_grandchild_script(native_python)
    )
    root_script = _write_script(
        tmp_path / "root_script.py",
        _root_spawns_child_script(native_python, str(child_script)),
    )
    child_stdin, parent_stdin, parent_stdout, child_stdout = _make_std_pipes()
    owner = None
    reader = None
    try:
        owner = WindowsJobOwner.launch(
            (native_python, str(root_script)), tmp_path, native_env,
            int(child_stdin), int(child_stdout),
        )
        fd = msvcrt.open_osfhandle(int(parent_stdout.Detach()), os.O_RDONLY)
        reader = os.fdopen(fd, "rb", closefd=True)
        root_pid = int(reader.readline().strip())
        child_pid = int(reader.readline().strip())
        grandchild_pid = int(reader.readline().strip())

        assert owner.contains(root_pid)
        assert owner.contains(child_pid)
        assert owner.contains(grandchild_pid)
        assert owner.alive()

        confirmed, reason = owner.stop(time.monotonic() + 5)
        assert confirmed, reason
        assert not owner.alive()
        # Our own still-open process_handle keeps the terminated process's kernel object (and
        # its Job association) alive until close_confirmed(); prove real death via a fresh,
        # independent OpenProcess instead of contains(), which would still see the zombie.
        assert not _process_exists(root_pid)
        assert not _process_exists(child_pid)
        assert not _process_exists(grandchild_pid)
        owner.close_confirmed()
    finally:
        if reader is not None:
            with contextlib.suppress(Exception):
                reader.close()
        _close_all(child_stdin, parent_stdin, child_stdout)


def _owner_process_script(native_python: str, root_script_path: str) -> str:
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
        f"    ({native_python!r}, {root_script_path!r}), os.getcwd(), os.environ.copy(),\n"
        "    int(child_stdin), int(child_stdout),\n"
        ")\n"
        "import msvcrt\n"
        "fd = msvcrt.open_osfhandle(int(parent_stdout.Detach()), os.O_RDONLY)\n"
        "reader = os.fdopen(fd, 'rb', closefd=True)\n"
        "root_pid = int(reader.readline().strip())\n"
        "child_pid = int(reader.readline().strip())\n"
        "grandchild_pid = int(reader.readline().strip())\n"
        "sys.stdout.write(\n"
        "    str(owner.pid) + ' ' + str(root_pid) + ' ' + str(child_pid) + ' '\n"
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
    root_script = _write_script(
        tmp_path / "owner_root_script.py",
        _root_spawns_child_script(native_python, str(child_script)),
    )
    owner_script = _write_script(
        tmp_path / "owner_process.py",
        _owner_process_script(native_python, str(root_script)),
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
        _owner_pid_str, root_pid_str, child_pid_str, grandchild_pid_str = line.split()
        root_pid = int(root_pid_str)
        child_pid = int(child_pid_str)
        grandchild_pid = int(grandchild_pid_str)

        assert _process_exists(root_pid)
        assert _process_exists(child_pid)
        assert _process_exists(grandchild_pid)

        # Simulate an owner crash: no Stop, no close_confirmed. Its non-inherited Job handle
        # is the last handle referencing the Job, so the OS closes it on process exit and the
        # kill-on-close limit reaps the whole contained tree.
        owner_process.terminate()
        owner_process.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            _process_exists(root_pid)
            or _process_exists(child_pid)
            or _process_exists(grandchild_pid)
        ):
            time.sleep(0.05)

        assert not _process_exists(root_pid), "root process survived its Job owner's crash"
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
