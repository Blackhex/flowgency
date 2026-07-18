from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from agency.integrations import BaseIntegration, IntegrationError
from agency.integrations.models import InteractiveSetupRequest


def test_base_integration_does_not_advertise_interactive_setup(tmp_path: Path) -> None:
    integration = BaseIntegration()

    assert integration.interactive_setup_available() is False

    with pytest.raises(IntegrationError):
        integration.launch_interactive_setup(
            InteractiveSetupRequest(
                project_dir=tmp_path,
                config_path=tmp_path / "config.yaml",
                prompt="Set up Agency.",
            )
        )


def test_terminal_available_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: "C:\\Windows\\System32\\cmd.exe" if name == "cmd.exe" else None)

    from agency.integrations.interactive import terminal_available

    assert terminal_available() is True


def test_terminal_available_on_windows_without_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    from agency.integrations.interactive import terminal_available

    assert terminal_available() is False


def test_terminal_available_on_posix_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: "x-terminal-emulator" if name == "x-terminal-emulator" else None)

    from agency.integrations.interactive import terminal_available

    assert terminal_available() is True


def test_spawn_interactive_terminal_windows_uses_cmd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((list(args), kwargs)),
    )

    from agency.integrations.interactive import spawn_interactive_terminal

    command = ["copilot", "-C", str(tmp_path), "-i", "Set up Agency."]
    result = spawn_interactive_terminal(command, tmp_path)

    assert result == subprocess.list2cmdline(command)
    assert calls == [
        (
            ["cmd.exe", "/k", subprocess.list2cmdline(command)],
            {
                "cwd": str(tmp_path.resolve()),
                "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
            },
        )
    ]


def test_spawn_interactive_terminal_posix_uses_x_terminal_emulator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: "x-terminal-emulator" if name == "x-terminal-emulator" else None)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((list(args), kwargs)),
    )

    from agency.integrations.interactive import spawn_interactive_terminal

    command = ["copilot", "-i", "Set up Agency."]
    result = spawn_interactive_terminal(command, tmp_path)

    assert result == shlex.join(command)
    assert calls == [
        (
            ["x-terminal-emulator", "-e", shlex.join(command)],
            {
                "cwd": str(tmp_path.resolve()),
                "start_new_session": True,
            },
        )
    ]


def test_spawn_interactive_terminal_raises_without_supported_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    from agency.integrations.interactive import spawn_interactive_terminal

    with pytest.raises(IntegrationError, match="No supported interactive terminal is available"):
        spawn_interactive_terminal(["copilot"], tmp_path)


def test_spawn_interactive_terminal_raises_for_nonexistent_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: "x-terminal-emulator" if name == "x-terminal-emulator" else None)

    from agency.integrations.interactive import spawn_interactive_terminal

    with pytest.raises(FileNotFoundError):
        spawn_interactive_terminal(["copilot"], tmp_path / "missing")
