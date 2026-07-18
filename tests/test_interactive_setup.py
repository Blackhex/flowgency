from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from agency.integrations import BaseIntegration, IntegrationError
from agency.integrations.agency.copilot import CopilotIntegration
from agency.integrations.models import InteractiveSetupRequest


def test_base_integration_does_not_advertise_interactive_setup(tmp_path: Path) -> None:
    integration = BaseIntegration()
    request = InteractiveSetupRequest(
        project_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        prompt="Set up Agency.",
    )

    assert integration.interactive_setup_available() is False

    with pytest.raises(IntegrationError):
        integration.launch_interactive_setup(request)

    with pytest.raises(IntegrationError):
        integration.interactive_setup_fallback_command(request)


def test_copilot_launches_interactive_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    from agency.integrations.interactive import format_interactive_command

    monkeypatch.setattr(
        "agency.integrations.agency.copilot.spawn_interactive_terminal",
        lambda command, cwd: captured.update(command=tuple(command), cwd=cwd)
        or format_interactive_command(command),
    )
    monkeypatch.setattr(
        CopilotIntegration,
        "_find_cmd",
        lambda self: r"C:\shim\copilot.cmd",
    )
    monkeypatch.setattr(
        CopilotIntegration,
        "_resolve_real_cmd",
        staticmethod(lambda cmd: r"C:\Program Files\GitHub Copilot\copilot.exe"),
    )
    monkeypatch.setattr(
        CopilotIntegration,
        "_command_exists",
        staticmethod(lambda cmd: True),
    )

    integration = CopilotIntegration()
    request = InteractiveSetupRequest(
        project_dir=tmp_path,
        config_path=tmp_path / "agency.yaml",
        prompt="Use the agency-setup skill.",
    )

    result = integration.launch_interactive_setup(request)

    assert captured["command"] == (
        r"C:\Program Files\GitHub Copilot\copilot.exe",
        "-C",
        str(tmp_path.resolve()),
        "-i",
        "Use the agency-setup skill.",
        "--name",
        "Agency setup",
    )
    assert captured["cwd"] == tmp_path.resolve()
    assert result.fallback_command == integration.interactive_setup_fallback_command(
        request
    )


def test_copilot_builds_fallback_command_without_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: r"C:\shim\copilot.cmd")
    monkeypatch.setattr(
        CopilotIntegration,
        "_resolve_real_cmd",
        staticmethod(lambda cmd: r"C:\Program Files\GitHub Copilot\copilot.exe"),
    )
    monkeypatch.setattr(
        CopilotIntegration,
        "_command_exists",
        staticmethod(lambda cmd: True),
    )

    integration = CopilotIntegration()
    request = InteractiveSetupRequest(
        project_dir=tmp_path,
        config_path=tmp_path / "agency.yaml",
        prompt="Use the agency-setup skill.",
    )

    assert integration.interactive_setup_fallback_command(request) == (
        "& 'C:\\Program Files\\GitHub Copilot\\copilot.exe' "
        f"'-C' '{tmp_path.resolve()}' "
        "'-i' 'Use the agency-setup skill.' "
        "'--name' 'Agency setup'"
    )


def test_copilot_launches_interactive_setup_via_powershell_for_npm_only_windows_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import agency.integrations.agency.copilot as copilot_mod

    wrappers = tmp_path / "npm-bin"
    wrappers.mkdir()
    cmd_wrapper = wrappers / "copilot.cmd"
    cmd_wrapper.write_text("@echo off\r\n", encoding="utf-8")
    ps1_wrapper = wrappers / "copilot.ps1"
    ps1_wrapper.write_text("Write-Host 'copilot'\r\n", encoding="utf-8")
    project_dir = tmp_path / "project & docs"
    project_dir.mkdir()
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    captured: dict[str, object] = {}

    from agency.integrations.interactive import format_interactive_command

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(copilot_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        copilot_mod.shutil,
        "which",
        lambda name, path=None: {
            "copilot.exe": None,
            "powershell.exe": powershell,
            "pwsh.exe": None,
        }.get(name),
    )
    monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: str(cmd_wrapper))
    monkeypatch.setattr(
        "agency.integrations.agency.copilot.spawn_interactive_terminal",
        lambda command, cwd: captured.update(command=tuple(command), cwd=cwd)
        or format_interactive_command(command),
    )

    integration = CopilotIntegration()
    request = InteractiveSetupRequest(
        project_dir=project_dir,
        config_path=tmp_path / "agency.yaml",
        prompt="Use the agency-setup skill.",
    )

    result = integration.launch_interactive_setup(request)

    assert captured["cwd"] == project_dir.resolve()
    assert captured["command"][:8] == (
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1_wrapper),
    )
    assert captured["command"][8:] == (
        "-C",
        str(project_dir.resolve()),
        "-i",
        "Use the agency-setup skill.",
        "--name",
        "Agency setup",
    )
    assert str(cmd_wrapper) not in captured["command"]
    assert all(
        not str(arg).lower().endswith((".cmd", ".bat"))
        for arg in captured["command"]
    )
    assert result.fallback_command == (
        f"& '{powershell}' '-NoLogo' '-NoProfile' '-NonInteractive' "
        f"'-ExecutionPolicy' 'Bypass' '-File' '{ps1_wrapper}' "
        f"'-C' '{project_dir.resolve()}' "
        "'-i' 'Use the agency-setup skill.' "
        "'--name' 'Agency setup'"
    )


def test_copilot_interactive_setup_unavailable_without_safe_windows_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import agency.integrations.agency.copilot as copilot_mod

    wrappers = tmp_path / "npm-bin"
    wrappers.mkdir()
    cmd_wrapper = wrappers / "copilot.cmd"
    cmd_wrapper.write_text("@echo off\r\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(copilot_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        copilot_mod.shutil,
        "which",
        lambda name, path=None: {
            "copilot.exe": None,
            "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "pwsh.exe": None,
        }.get(name),
    )
    monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: str(cmd_wrapper))
    monkeypatch.setattr(
        "agency.integrations.agency.copilot.spawn_interactive_terminal",
        lambda command, cwd: (_ for _ in ()).throw(AssertionError("spawn must not run")),
    )

    integration = CopilotIntegration()
    request = InteractiveSetupRequest(
        project_dir=project_dir,
        config_path=tmp_path / "agency.yaml",
        prompt="Use the agency-setup skill.",
    )

    assert integration.interactive_setup_available() is False
    with pytest.raises(IntegrationError, match="requires copilot.exe or a copilot.ps1 wrapper"):
        integration.interactive_setup_fallback_command(request)
    with pytest.raises(IntegrationError, match="requires copilot.exe or a copilot.ps1 wrapper"):
        integration.launch_interactive_setup(request)


def test_copilot_interactive_setup_unavailable_without_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        CopilotIntegration,
        "_find_cmd",
        lambda self: str(tmp_path / "copilot"),
    )
    (tmp_path / "copilot").write_text("")

    integration = CopilotIntegration()

    assert integration.interactive_setup_available() is False


def test_copilot_interactive_setup_available_when_terminal_and_executable_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "x-terminal-emulator" if name == "x-terminal-emulator" else None,
    )
    executable = tmp_path / "copilot"
    executable.write_text("")
    monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: str(executable.resolve()))

    integration = CopilotIntegration()

    assert integration.interactive_setup_available() is True


def test_copilot_interactive_setup_unavailable_without_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "x-terminal-emulator" if name == "x-terminal-emulator" else None,
    )
    monkeypatch.setattr(
        CopilotIntegration,
        "_find_cmd",
        lambda self: str(tmp_path / "copilot"),
    )

    integration = CopilotIntegration()

    assert integration.interactive_setup_available() is False


def test_terminal_available_on_windows_does_not_require_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    from agency.integrations.interactive import terminal_available

    assert terminal_available() is True


def test_terminal_available_on_posix_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: "x-terminal-emulator" if name == "x-terminal-emulator" else None)

    from agency.integrations.interactive import terminal_available

    assert terminal_available() is True


def test_spawn_interactive_terminal_windows_launches_direct_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    project_dir = tmp_path / "project & docs"
    project_dir.mkdir()

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((list(args), kwargs)),
    )

    from agency.integrations.interactive import spawn_interactive_terminal

    command = [
        r"C:\Program Files\GitHub Copilot\copilot.exe",
        "-C",
        str(project_dir.resolve()),
        "-i",
        "Set up Agency.",
    ]
    result = spawn_interactive_terminal(command, project_dir)

    assert result == (
        "& 'C:\\Program Files\\GitHub Copilot\\copilot.exe' "
        f"'-C' '{project_dir.resolve()}' "
        "'-i' 'Set up Agency.'"
    )
    assert calls == [
        (
            command,
            {
                "cwd": str(project_dir.resolve()),
                "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
            },
        )
    ]
    assert "cmd.exe" not in calls[0][0]
    assert calls[0][0][2] == str(project_dir.resolve())


def test_spawn_interactive_terminal_posix_uses_separate_argv_for_xterm_like_terminals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/xterm" if name == "xterm" else None,
    )
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
            ["/usr/bin/xterm", "-e", *command],
            {
                "cwd": str(tmp_path.resolve()),
                "start_new_session": True,
            },
        )
    ]


def test_spawn_interactive_terminal_posix_uses_double_dash_for_gnome_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None,
    )
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
            ["/usr/bin/gnome-terminal", "--", *command],
            {
                "cwd": str(tmp_path.resolve()),
                "start_new_session": True,
            },
        )
    ]


def test_spawn_interactive_terminal_posix_quotes_joined_command_for_string_e_terminals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/xfce4-terminal" if name == "xfce4-terminal" else None,
    )
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
            ["/usr/bin/xfce4-terminal", "-e", shlex.join(command)],
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
