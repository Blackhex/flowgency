"""Tests for the CLI interface."""

from argparse import Namespace
import subprocess
import sys

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
