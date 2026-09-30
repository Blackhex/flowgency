from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import ConnectedLaunchError, start_connected_process
from flowgency.jobs.processes import RuntimeProcessLifecycle

from tests.test_connected_process import _read_until


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows connected PTY adapter is Windows-specific")


if os.name == "nt":
    import win32api
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
        process.write("zażółć\n".encode("utf-8"))
        assert b"za\xc5\xbc\xc3\xb3\xc5\x82\xc4\x87" in _read_until(process, "zażółć".encode("utf-8")).group(0)
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