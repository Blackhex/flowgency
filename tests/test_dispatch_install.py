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
