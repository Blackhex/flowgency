import sys
import pytest
from unittest.mock import patch, MagicMock
from agency.dispatch.install import detect_platform, get_timer_status


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
    trigger = task_def.Triggers.Create.return_value
    action = task_def.Actions.Create.return_value

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import install_timer
        err = install_timer(r"C:\cfg\config.yaml", 15)

    assert err is None
    scheduler.Connect.assert_called_once()
    task_def.Triggers.Create.assert_called_once_with(1)   # TASK_TRIGGER_TIME
    assert trigger.Repetition.Interval == "PT15M"
    task_def.Actions.Create.assert_called_once_with(0)    # TASK_ACTION_EXEC
    assert action.Arguments == '-m agency.dispatch.run --config "C:\\cfg\\config.yaml"'
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

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status()

    assert status == {"installed": True, "timer_active": True}


def test_status_windows_not_installed():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    folder.GetTask.side_effect = Exception("The system cannot find the file specified")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status()

    assert status == {"installed": False, "timer_active": False}


def test_uninstall_windows_deletes_task():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer()

    assert err is None
    folder.DeleteTask.assert_called_once_with("AgencyDispatch", 0)


def test_uninstall_windows_missing_task_is_success():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    folder.DeleteTask.side_effect = Exception("The system cannot find the file specified")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer()

    assert err is None
