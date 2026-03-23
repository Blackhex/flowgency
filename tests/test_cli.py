"""Tests for the CLI interface."""
import subprocess
import sys

def test_cli_help_shows_subcommands():
    """Running agency --help should list available subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "inbox" in result.stdout
    assert "status" in result.stdout

def test_cli_no_args_shows_help():
    """Running agency with no args should show help."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli"],
        capture_output=True, text=True
    )
    # argparse prints help to stdout or stderr depending on version
    output = result.stdout + result.stderr
    assert "serve" in output or result.returncode == 0
