import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from agency.dispatch.install import detect_platform, get_timer_status, install_timer, uninstall_timer

import agency.dispatch.install as dispatch_install


def test_detect_platform_linux():
    with patch("platform.system", return_value="Linux"):
        assert detect_platform() == "linux"


def test_detect_platform_macos():
    with patch("platform.system", return_value="Darwin"):
        assert detect_platform() == "macos"


def test_detect_platform_windows():
    with patch("platform.system", return_value="Windows"):
        assert detect_platform() == "windows"


def test_windows_python_launcher_prefers_pythonw(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    with patch("agency.dispatch.install.sys.executable", str(tmp_path / "python.exe")):
        from agency.dispatch.install import _windows_python_launcher
        assert _windows_python_launcher() == str(tmp_path / "pythonw.exe")


def test_windows_python_launcher_falls_back_to_executable(tmp_path):
    (tmp_path / "python.exe").write_text("")
    with patch("agency.dispatch.install.sys.executable", str(tmp_path / "python.exe")):
        from agency.dispatch.install import _windows_python_launcher
        assert _windows_python_launcher() == str(tmp_path / "python.exe")


def test_install_windows_registers_task():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    task_def = scheduler.NewTask.return_value
    folder = scheduler.GetFolder.return_value
    folder.GetTask.side_effect = Exception("task not found")
    trigger = task_def.Triggers.Create.return_value
    action = task_def.Actions.Create.return_value

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import install_timer
        err = install_timer(r"C:\cfg\config.yaml", 15)

    assert err is None
    # Connect is called twice: once in status check, once in install
    assert scheduler.Connect.call_count == 2
    task_def.Triggers.Create.assert_called_once_with(1)   # TASK_TRIGGER_TIME
    assert trigger.Repetition.Interval == "PT15M"
    task_def.Actions.Create.assert_called_once_with(0)    # TASK_ACTION_EXEC
    # Verify canonical path is used
    assert "--config" in action.Arguments
    assert "agency.dispatch.run" in action.Arguments
    import agency.dispatch.install as _install_mod
    from pathlib import Path as _Path
    assert action.WorkingDirectory == str(_Path(_install_mod.__file__).parent.parent.parent)
    folder.RegisterTaskDefinition.assert_called_once()
    reg_args = folder.RegisterTaskDefinition.call_args.args
    assert reg_args[0] == "AgencyDispatch"
    assert reg_args[2] == 6   # TASK_CREATE_OR_UPDATE
    assert reg_args[5] == 3   # TASK_LOGON_INTERACTIVE_TOKEN


def test_install_windows_without_pywin32_returns_error():
    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": None, "win32com.client": None}):
        from agency.dispatch.install import install_timer
        err = install_timer("cfg", 15)
    assert err is not None
    assert "pywin32" in err


def test_status_windows_installed_and_active():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    task = folder.GetTask.return_value
    task.Enabled = True
    task.State = 3  # TASK_STATE_READY
    action = task.Definition.Actions.Item.return_value
    action.Path = dispatch_install._windows_python_launcher()
    action.Arguments = '-m agency.dispatch.run --config "C:\\config.yaml"'
    trigger = task.Definition.Triggers.Item.return_value
    trigger.Repetition.Interval = "PT15M"

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status("C:\\config.yaml", 15)

    assert status["installed"] is True
    assert status["timer_active"] is True
    assert status["state"] == "active"
    assert status["definition_matches"] is True


def test_status_windows_not_installed():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    folder.GetTask.side_effect = Exception("The system cannot find the file specified")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status("C:\\config.yaml", 15)

    assert status["installed"] is False
    assert status["timer_active"] is False
    assert status["state"] == "inactive"


def test_status_windows_installed_but_disabled():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    task = folder.GetTask.return_value
    task.Enabled = False
    task.State = 3  # TASK_STATE_READY
    action = task.Definition.Actions.Item.return_value
    action.Path = dispatch_install._windows_python_launcher()
    action.Arguments = '-m agency.dispatch.run --config "C:\\config.yaml"'
    trigger = task.Definition.Triggers.Item.return_value
    trigger.Repetition.Interval = "PT15M"

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status("C:\\config.yaml", 15)

    assert status["installed"] is True
    assert status["timer_active"] is False
    assert status["state"] == "inactive"
    assert status["definition_matches"] is True


def test_uninstall_windows_deletes_task():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer("C:\\config.yaml")

    assert err is None
    folder.DeleteTask.assert_called_once_with("AgencyDispatch", 0)


def test_uninstall_windows_missing_task_is_success():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    folder.GetTask.side_effect = Exception("The system cannot find the file specified")
    folder.DeleteTask.side_effect = Exception("The system cannot find the file specified")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer("C:\\config.yaml")

    assert err is None


def test_uninstall_windows_connect_failure_returns_error():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    scheduler.Connect.side_effect = Exception("The RPC server is unavailable")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer("C:\\config.yaml")

    assert err is not None
    assert "RPC server" in err


# ── Step 1: Windows definition and conflict tests ────────────────────────────


def _configure_windows_task(fake_client, config_path, interval=15, enabled=True, state=3):
    scheduler = fake_client.Dispatch.return_value
    task = scheduler.GetFolder.return_value.GetTask.return_value
    task.Enabled = enabled
    task.State = state
    action = task.Definition.Actions.Item.return_value
    action.Path = dispatch_install._windows_python_launcher()
    action.Arguments = f'-m agency.dispatch.run --config "{Path(config_path).resolve()}"'
    trigger = task.Definition.Triggers.Item.return_value
    trigger.Repetition.Interval = f"PT{interval}M"
    return task


def test_status_windows_matching_definition_is_active(tmp_path):
    fake_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    _configure_windows_task(fake_client, config_path)
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(config_path, 15)
    assert status["state"] == "active"
    assert status["installed"] is True
    assert status["enabled"] is True
    assert status["definition_matches"] is True
    assert status["config_conflict"] is False
    assert status["config_path"] == str(config_path.resolve())
    assert status["interval"] == 15
    assert status["mismatches"] == []
    assert status["error"] is None


def test_status_windows_reports_config_conflict(tmp_path):
    fake_client = MagicMock()
    expected = tmp_path / "expected.yaml"
    other = tmp_path / "other.yaml"
    _configure_windows_task(fake_client, other)
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(expected, 15)
    assert status["state"] == "misconfigured"
    assert status["config_conflict"] is True
    assert status["config_path"] == str(other.resolve())
    assert status["mismatches"] == ["config_path"]


def test_status_windows_reports_action_and_interval_mismatches(tmp_path):
    fake_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    task = _configure_windows_task(fake_client, config_path, interval=30)
    task.Definition.Actions.Item.return_value.Path = str(tmp_path / "wrong.exe")
    task.Definition.Actions.Item.return_value.Arguments = (
        f'-m wrong.module --config "{config_path.resolve()}"'
    )
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(config_path, 15)
    assert status["state"] == "misconfigured"
    assert status["config_conflict"] is False
    assert status["mismatches"] == ["executable", "module", "interval"]


def test_status_windows_disabled_matching_task_is_inactive(tmp_path):
    fake_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    _configure_windows_task(fake_client, config_path, enabled=False)
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(config_path, 15)
    assert status["state"] == "inactive"
    assert status["definition_matches"] is True
    assert status["enabled"] is False
    assert status["timer_active"] is False


def test_install_refuses_another_config_without_replace(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    other = tmp_path / "other.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=other,
        interval=15,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    error = install_timer(requested, 15)
    assert "another config" in error
    assert calls == []


def test_install_replace_overwrites_conflicting_global_task(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    other = tmp_path / "other.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=other,
        interval=15,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert install_timer(requested, 15, replace=True) is None
    assert calls == [(str(requested.resolve()), 15)]


def test_install_repairs_same_config_without_replace(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=requested,
        interval=30,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert install_timer(requested, 15) is None
    assert calls == [(str(requested.resolve()), 15)]


def test_install_repairs_installed_unreadable_definition(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        mismatches=["definition"],
        error="definition could not be parsed",
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert install_timer(requested, 15) is None
    assert calls == [(str(requested.resolve()), 15)]


def test_uninstall_requires_force_for_another_config(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    other = tmp_path / "other.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=other,
        interval=15,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_uninstall_windows", lambda: calls.append(True))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert "another config" in uninstall_timer(requested)
    assert calls == []
    assert uninstall_timer(requested, force=True) is None
    assert calls == [True]


def test_uninstall_force_removes_installed_unreadable_definition(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        mismatches=["definition"],
        error="definition could not be parsed",
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_uninstall_windows", lambda: calls.append(True))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert "could not be parsed" in uninstall_timer(requested)
    assert uninstall_timer(requested, force=True) is None
    assert calls == [True]


# ── Step 6: Linux and macOS definition tests ─────────────────────────────────


def test_linux_status_validates_units_config_and_interval(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "python3"
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_install, "SYSTEMD_USER_DIR", tmp_path / "systemd")
    monkeypatch.setattr(dispatch_install, "_linux_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(
            stdout="enabled\n" if "is-enabled" in command else "active\n"
        ),
    )
    assert dispatch_install._install_linux(str(config_path), 15) is None
    status = dispatch_install._status_linux(config_path, 15)
    assert status["state"] == "active"
    assert status["config_path"] == str(config_path.resolve())
    assert status["interval"] == 15
    assert status["mismatches"] == []


def test_linux_status_reports_wrong_config_and_interval(tmp_path, monkeypatch):
    expected = tmp_path / "expected.yaml"
    other = tmp_path / "other.yaml"
    python_path = tmp_path / "python3"
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "agency-dispatch.service").write_text(
        f'ExecStart="{python_path}" -m agency.dispatch.run --config "{other.resolve()}"\n',
        encoding="utf-8",
    )
    (unit_dir / "agency-dispatch.timer").write_text(
        "[Timer]\nOnUnitActiveSec=30m\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_install, "SYSTEMD_USER_DIR", unit_dir)
    monkeypatch.setattr(dispatch_install, "_linux_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(
            stdout="enabled\n" if "is-enabled" in command else "active\n"
        ),
    )
    status = dispatch_install._status_linux(expected, 15)
    assert status["state"] == "misconfigured"
    assert status["config_conflict"] is True
    assert status["mismatches"] == ["config_path", "interval"]


def test_macos_status_validates_plist_config_and_interval(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "python3"
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_install, "LAUNCHD_AGENTS_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(dispatch_install, "_macos_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(returncode=0),
    )
    assert dispatch_install._install_macos(str(config_path), 15) is None
    status = dispatch_install._status_macos(config_path, 15)
    assert status["state"] == "active"
    assert status["config_path"] == str(config_path.resolve())
    assert status["interval"] == 15
    assert status["mismatches"] == []


def test_macos_status_reports_wrong_module_and_interval(tmp_path, monkeypatch):
    import plistlib

    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "python3"
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    with (launch_agents / "com.agency.dispatch.plist").open("wb") as plist_file:
        plistlib.dump(
            {
                "Label": "com.agency.dispatch",
                "ProgramArguments": [
                    str(python_path),
                    "-m",
                    "wrong.module",
                    "--config",
                    str(config_path.resolve()),
                ],
                "StartInterval": 1800,
                "RunAtLoad": True,
            },
            plist_file,
        )
    monkeypatch.setattr(dispatch_install, "LAUNCHD_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(dispatch_install, "_macos_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(returncode=0),
    )
    status = dispatch_install._status_macos(config_path, 15)
    assert status["state"] == "misconfigured"
    assert status["mismatches"] == ["module", "interval"]


def test_uninstall_linux_removes_both_units(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    for name in ("agency-dispatch.timer", "agency-dispatch.service"):
        (unit_dir / name).write_text("unit", encoding="utf-8")
    calls = []
    monkeypatch.setattr(dispatch_install, "SYSTEMD_USER_DIR", unit_dir)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or MagicMock(returncode=0),
    )
    assert dispatch_install._uninstall_linux() is None
    assert not (unit_dir / "agency-dispatch.timer").exists()
    assert not (unit_dir / "agency-dispatch.service").exists()
    assert ["systemctl", "--user", "daemon-reload"] in calls


def test_uninstall_macos_unloads_and_removes_plist(tmp_path, monkeypatch):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    plist_path = launch_agents / "com.agency.dispatch.plist"
    plist_path.write_text("plist", encoding="utf-8")
    calls = []
    monkeypatch.setattr(dispatch_install, "LAUNCHD_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or MagicMock(returncode=0),
    )
    assert dispatch_install._uninstall_macos() is None
    assert not plist_path.exists()
    assert calls == [["launchctl", "unload", str(plist_path)]]


def test_get_timer_status_converts_unexpected_exception_to_error_status(monkeypatch):
    """Prove that unexpected platform-helper exceptions are converted to rich inactive TimerStatus."""
    def raise_unexpected(*args, **kwargs):
        raise IOError("Unexpected file read failure")

    monkeypatch.setattr(dispatch_install, "_status_windows", raise_unexpected)
    monkeypatch.setattr("platform.system", lambda: "Windows")

    status = get_timer_status("C:\\config.yaml", 15)

    assert status["state"] == "inactive"
    assert status["installed"] is False
    assert status["expected_config_path"] == str(Path("C:\\config.yaml").resolve())
    assert status["expected_interval"] == 15
    assert status["error"] == "Unexpected file read failure"
    assert status["config_path"] is None
    assert status["interval"] is None
