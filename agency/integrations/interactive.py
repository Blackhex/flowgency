"""Shared helpers for launching interactive setup terminals."""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from agency.integrations.errors import IntegrationError

_POSIX_TERMINAL_CANDIDATES = (
    "x-terminal-emulator",
    "gnome-terminal",
    "konsole",
    "xterm",
    "xfce4-terminal",
    "mate-terminal",
    "tilix",
)


def terminal_available() -> bool:
    if platform.system() == "Windows":
        return True
    return _find_posix_terminal() is not None


def format_interactive_command(command: Sequence[str]) -> str:
    parts = [str(part) for part in command]
    if platform.system() == "Windows":
        return "& " + " ".join(_powershell_quote(part) for part in parts)
    return shlex.join(parts)


def _powershell_quote(argument: str) -> str:
    return "'" + argument.replace("'", "''") + "'"


def _find_posix_terminal() -> str | None:
    for candidate in _POSIX_TERMINAL_CANDIDATES:
        terminal = shutil.which(candidate)
        if terminal:
            return terminal
    return None


def _resolved_terminal_name(terminal: str) -> str:
    resolved = terminal
    try:
        resolved = str(Path(terminal).resolve())
    except OSError:
        pass
    name = Path(resolved).name.lower()
    if name.endswith(".wrapper"):
        name = name[: -len(".wrapper")]
    return name


def _posix_terminal_command(terminal: str, command: Sequence[str]) -> list[str]:
    parts = [str(part) for part in command]
    terminal_name = _resolved_terminal_name(terminal)
    if terminal_name.startswith("gnome-terminal"):
        return [terminal, "--", *parts]
    if terminal_name == "x-terminal-emulator" or terminal_name.startswith(("xterm", "konsole")):
        return [terminal, "-e", *parts]
    return [terminal, "-e", shlex.join(parts)]


def spawn_interactive_terminal(command: Sequence[str], cwd: Path) -> str:
    resolved_cwd = cwd.resolve(strict=True)
    argv = [str(part) for part in command]
    command_line = format_interactive_command(argv)
    if platform.system() == "Windows":
        subprocess.Popen(
            argv,
            cwd=str(resolved_cwd),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
        )
        return command_line
    terminal = _find_posix_terminal()
    if terminal:
        subprocess.Popen(
            _posix_terminal_command(terminal, argv),
            cwd=str(resolved_cwd),
            start_new_session=True,
        )
        return command_line

    raise IntegrationError("No supported interactive terminal is available.")
