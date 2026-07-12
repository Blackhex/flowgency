"""Tests for interval range validation in install_timer."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from agency.dispatch import install as dispatch_install


@pytest.mark.parametrize("interval", [4, 0, -1, 121, 150])
def test_install_timer_rejects_out_of_range_interval(tmp_path, monkeypatch, interval):
    """Prove install_timer rejects intervals outside 5-120 before platform calls."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    
    # Mock platform detection
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "linux")
    
    # Track if status or install was called
    status_calls = []
    install_calls = []
    monkeypatch.setattr(
        dispatch_install, 
        "get_timer_status", 
        lambda path, interval: status_calls.append((path, interval)) or {}
    )
    monkeypatch.setattr(
        dispatch_install,
        "_install_linux",
        lambda path, interval: install_calls.append((path, interval))
    )
    
    error = dispatch_install.install_timer(config_path, interval)
    
    # Must return error
    assert error is not None
    assert "5" in error and "120" in error
    assert "interval" in error.lower()
    
    # Must not call status or backend
    assert status_calls == []
    assert install_calls == []


@pytest.mark.parametrize("interval", [5, 120])
def test_install_timer_accepts_boundary_intervals(tmp_path, monkeypatch, interval):
    """Prove install_timer accepts boundary values 5 and 120."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "linux")
    
    # Stub out the actual status/install to return success
    monkeypatch.setattr(
        dispatch_install,
        "get_timer_status",
        lambda path, interval: dispatch_install._make_status(
            expected_config_path=path,
            expected_interval=interval,
            installed=False,
        )
    )
    monkeypatch.setattr(dispatch_install, "_install_linux", lambda path, interval: None)
    
    error = dispatch_install.install_timer(config_path, interval)
    
    # Must succeed
    assert error is None
