"""Tests for the CLI interface."""

from argparse import Namespace
import subprocess
import sys

import pytest
import yaml

from agency import cli


def test_cli_help_shows_subcommands():
    """Running agency --help should list available subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "inbox" in result.stdout
    assert "status" in result.stdout


def test_cli_no_args_shows_help():
    """Running agency with no args should show help."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert "serve" in output or result.returncode == 0


def test_cli_serve_help_shows_reload():
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "serve", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--reload" in result.stdout


def test_cmd_serve_forwards_arguments_without_mutating_sys_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_server", lambda **options: calls.append(options))
    original_argv = sys.argv.copy()

    cli.cmd_serve(Namespace(host="127.0.0.1", port=8700, reload=True))

    assert calls == [{"host": "127.0.0.1", "port": 8700, "reload": True}]
    assert sys.argv == original_argv


# Task 2 (Official Dispatch CLI): Tests for cmd_dispatch


def _dispatch_status(state="active", installed=True, error=None):
    return {
        "state": state,
        "installed": installed,
        "enabled": state == "active",
        "timer_active": state == "active",
        "definition_matches": installed and state != "misconfigured",
        "config_conflict": False,
        "config_path": None,
        "interval": 15 if installed else None,
        "expected_config_path": "C:/config.yaml",
        "expected_interval": 15,
        "mismatches": [] if state != "misconfigured" else ["interval"],
        "error": error,
    }


def test_cli_help_shows_dispatch_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "dispatch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert all(command in result.stdout for command in ("install", "status", "uninstall"))


def test_cmd_dispatch_install_persists_interval_and_forwards_replace(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency: {}\ngroups: {}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "install_timer", lambda path, interval, replace=False: calls.append((path, interval, replace)))
    monkeypatch.setattr(cli, "get_timer_status", lambda path, interval: _dispatch_status())
    exit_code = cli.cmd_dispatch(
        Namespace(dispatch_command="install", config=str(config_path), interval=30, replace=True, force=False)
    )
    assert exit_code == 0
    assert calls == [(str(config_path.resolve()), 30, True)]
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["agency"]["dispatch"] == {"interval": 30}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_dispatch_status(), 0),
        (_dispatch_status(state="inactive", installed=False), 1),
        (_dispatch_status(state="inactive", installed=True), 2),
        (_dispatch_status(state="misconfigured", installed=True), 3),
        (_dispatch_status(state="inactive", installed=False, error="unavailable"), 4),
    ],
)
def test_dispatch_status_exit_codes(tmp_path, monkeypatch, status, expected):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency:\n  dispatch:\n    interval: 15\ngroups: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "get_timer_status", lambda path, interval: status)
    args = Namespace(dispatch_command="status", config=str(config_path), interval=None, replace=False, force=False)
    assert cli.cmd_dispatch(args) == expected


def test_cmd_dispatch_uninstall_forwards_force(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency: {}\ngroups: {}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "uninstall_timer", lambda path, force=False: calls.append((path, force)))
    args = Namespace(dispatch_command="uninstall", config=str(config_path), interval=None, replace=False, force=True)
    assert cli.cmd_dispatch(args) == 0
    assert calls == [(str(config_path.resolve()), True)]
