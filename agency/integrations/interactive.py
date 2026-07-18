"""Shared helpers for launching interactive setup terminals."""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from agency.integrations.errors import IntegrationError


def terminal_available() -> bool:
    if platform.system() == "Windows":
        return shutil.which("cmd.exe") is not None
    return shutil.which("x-terminal-emulator") is not None


def spawn_interactive_terminal(command: Sequence[str], cwd: Path) -> str:
    resolved_cwd = cwd.resolve(strict=True)
    if platform.system() == "Windows":
        command_line = subprocess.list2cmdline(list(command))
        subprocess.Popen(
            ["cmd.exe", "/k", command_line],
            cwd=str(resolved_cwd),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
        )
        return command_line

    terminal = shutil.which("x-terminal-emulator")
    if terminal:
        command_line = shlex.join(command)
        subprocess.Popen(
            [terminal, "-e", command_line],
            cwd=str(resolved_cwd),
            start_new_session=True,
        )
        return command_line

    raise IntegrationError("No supported interactive terminal is available.")
