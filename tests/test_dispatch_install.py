import sys
import pytest
from unittest.mock import patch
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
