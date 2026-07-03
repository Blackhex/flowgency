"""Cross-platform dispatch timer installer (systemd + launchd)."""

import platform
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DISPATCH_CONF_DIR = Path.home() / ".config" / "agency"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
LAUNCHD_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_PLIST = "com.agency.dispatch"
WINDOWS_TASK_NAME = "AgencyDispatch"


def detect_platform() -> str:
    """Return 'linux', 'macos', or 'windows'."""
    s = platform.system()
    if s == "Linux":
        return "linux"
    elif s == "Darwin":
        return "macos"
    else:
        return "windows"


def get_timer_status() -> dict:
    """Check if timer is installed and active. Returns dict with 'installed' and 'timer_active'."""
    plat = detect_platform()
    if plat == "linux":
        return _status_linux()
    elif plat == "macos":
        return _status_macos()
    else:
        return {"installed": False, "timer_active": False}


def install_timer(config_path: str, interval: int = 15) -> str | None:
    """Install platform-native timer. Returns error string or None on success."""
    plat = detect_platform()
    if plat == "linux":
        return _install_linux(config_path, interval)
    elif plat == "macos":
        return _install_macos(config_path, interval)
    else:
        return "Windows timer installation is not yet implemented. Please set up a Task Scheduler entry manually."


def uninstall_timer() -> str | None:
    """Remove platform-native timer. Returns error string or None on success."""
    plat = detect_platform()
    if plat == "linux":
        return _uninstall_linux()
    elif plat == "macos":
        return _uninstall_macos()
    else:
        return "Windows timer uninstallation is not yet implemented."


# ── Windows (Task Scheduler) ─────────────────────────────────────────────────


def _windows_python_launcher() -> str:
    """Return pythonw.exe (no console window) if present, else sys.executable."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return str(exe)


# ── Linux (systemd) ──────────────────────────────────────────────────────────


def _status_linux() -> dict:
    """Check systemd timer status."""
    service_file = SYSTEMD_USER_DIR / "agency-dispatch.service"
    installed = service_file.exists()
    timer_active = False
    if installed:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "agency-dispatch.timer"],
                capture_output=True, text=True, timeout=5,
            )
            timer_active = result.stdout.strip() == "active"
        except Exception:
            timer_active = False
    return {"installed": installed, "timer_active": timer_active}


def _build_path_env() -> str:
    """Build a PATH that includes the user's local bin dirs alongside system defaults."""
    path_dirs = []
    # Include dirs where user-installed CLIs live (e.g. claude, uv)
    for candidate in [
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
    ]:
        if candidate.is_dir():
            path_dirs.append(str(candidate))
    # System defaults
    path_dirs.extend(["/usr/local/bin", "/usr/bin", "/bin"])
    return ":".join(path_dirs)


def _install_linux(config_path: str, interval: int) -> str | None:
    """Write systemd service + timer and enable them."""
    try:
        # Find venv python
        venv_python = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"
        if not venv_python.exists():
            venv_python = Path(sys.executable)

        path_env = _build_path_env()

        # Write systemd service
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        service_file = SYSTEMD_USER_DIR / "agency-dispatch.service"
        service_file.write_text(
            "[Unit]\n"
            "Description=Agency Agent Dispatch\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart={venv_python} -m agency.dispatch.run --config {config_path}\n"
            f"Environment=PATH={path_env}\n"
            "Environment=HOME=%h\n"
        )

        # Write systemd timer
        timer_file = SYSTEMD_USER_DIR / "agency-dispatch.timer"
        timer_file.write_text(
            "[Unit]\n"
            "Description=Agency Agent Dispatch Timer\n"
            "\n"
            "[Timer]\n"
            f"OnBootSec={interval}m\n"
            f"OnUnitActiveSec={interval}m\n"
            "Persistent=true\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )

        # Enable and start timer
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "agency-dispatch.timer"],
            capture_output=True, text=True, timeout=10, check=True,
        )

        return None
    except Exception as e:
        return str(e)


def _uninstall_linux() -> str | None:
    """Stop, disable, and remove systemd units."""
    try:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", "agency-dispatch.timer"],
            capture_output=True, text=True, timeout=10,
        )
        for name in ("agency-dispatch.timer", "agency-dispatch.service"):
            unit_file = SYSTEMD_USER_DIR / name
            if unit_file.exists():
                unit_file.unlink()
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
        return None
    except Exception as e:
        return str(e)


# ── macOS (launchd) ───────────────────────────────────────────────────────────


def _status_macos() -> dict:
    """Check launchd plist status."""
    plist_file = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
    installed = plist_file.exists()
    timer_active = False
    if installed:
        try:
            result = subprocess.run(
                ["launchctl", "list", LAUNCHD_PLIST],
                capture_output=True, text=True, timeout=5,
            )
            timer_active = result.returncode == 0
        except Exception:
            timer_active = False
    return {"installed": installed, "timer_active": timer_active}


def _install_macos(config_path: str, interval: int) -> str | None:
    """Write launchd plist and load it."""
    try:
        # Find venv python
        venv_python = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"
        if not venv_python.exists():
            venv_python = Path(sys.executable)

        interval_seconds = interval * 60

        LAUNCHD_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        plist_file = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
        plist_file.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
            ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            f'    <key>Label</key>\n'
            f'    <string>{LAUNCHD_PLIST}</string>\n'
            '    <key>ProgramArguments</key>\n'
            '    <array>\n'
            f'        <string>{venv_python}</string>\n'
            '        <string>-m</string>\n'
            '        <string>agency.dispatch.run</string>\n'
            '        <string>--config</string>\n'
            f'        <string>{config_path}</string>\n'
            '    </array>\n'
            '    <key>StartInterval</key>\n'
            f'    <integer>{interval_seconds}</integer>\n'
            '    <key>RunAtLoad</key>\n'
            '    <true/>\n'
            '</dict>\n'
            '</plist>\n'
        )

        subprocess.run(
            ["launchctl", "load", str(plist_file)],
            capture_output=True, text=True, timeout=10, check=True,
        )

        return None
    except Exception as e:
        return str(e)


def _uninstall_macos() -> str | None:
    """Unload and remove launchd plist."""
    try:
        plist_file = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
        if plist_file.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist_file)],
                capture_output=True, text=True, timeout=10,
            )
            plist_file.unlink()
        return None
    except Exception as e:
        return str(e)
