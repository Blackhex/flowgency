"""Cross-platform dispatch timer installer (systemd + launchd)."""

import os
import plistlib
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

DISPATCH_CONF_DIR = Path.home() / ".config" / "agency"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
LAUNCHD_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_PLIST = "com.agency.dispatch"
WINDOWS_TASK_NAME = "AgencyDispatch"


TimerState = Literal["active", "inactive", "misconfigured"]


class TimerStatus(TypedDict):
    state: TimerState
    installed: bool
    enabled: bool
    timer_active: bool
    definition_matches: bool
    config_conflict: bool
    config_path: str | None
    interval: int | None
    expected_config_path: str
    expected_interval: int
    mismatches: list[str]
    error: str | None


def _canonical_config_path(config_path: str | Path) -> str:
    return str(Path(config_path).expanduser().resolve())


def _paths_equal(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(_canonical_config_path(left)) == os.path.normcase(
        _canonical_config_path(right)
    )


def _extract_config_path(arguments: str) -> str | None:
    match = re.search(r'(?:^|\s)--config\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))', arguments)
    if not match:
        return None
    value = next(group for group in match.groups() if group is not None)
    return _canonical_config_path(value)


def _parse_iso_minutes(value: str) -> int | None:
    match = re.fullmatch(r"PT(\d+)M", value or "")
    return int(match.group(1)) if match else None


def _make_status(
    *,
    expected_config_path: str | Path,
    expected_interval: int,
    installed: bool,
    enabled: bool = False,
    timer_active: bool = False,
    config_path: str | Path | None = None,
    interval: int | None = None,
    mismatches: list[str] | None = None,
    error: str | None = None,
) -> TimerStatus:
    expected_path = _canonical_config_path(expected_config_path)
    actual_path = _canonical_config_path(config_path) if config_path else None
    mismatch_list = list(mismatches or [])
    if installed and actual_path is None and "config_path" not in mismatch_list:
        mismatch_list.append("config_path")
    elif installed and actual_path and not _paths_equal(actual_path, expected_path):
        if "config_path" not in mismatch_list:
            mismatch_list.append("config_path")
    if installed and interval != expected_interval and "interval" not in mismatch_list:
        mismatch_list.append("interval")
    definition_matches = installed and not mismatch_list
    state: TimerState
    if installed and mismatch_list:
        state = "misconfigured"
    elif installed and enabled and timer_active:
        state = "active"
    else:
        state = "inactive"
    return {
        "state": state,
        "installed": installed,
        "enabled": enabled,
        "timer_active": timer_active,
        "definition_matches": definition_matches,
        "config_conflict": bool(installed and actual_path and not _paths_equal(actual_path, expected_path)),
        "config_path": actual_path,
        "interval": interval,
        "expected_config_path": expected_path,
        "expected_interval": expected_interval,
        "mismatches": mismatch_list,
        "error": error,
    }


def detect_platform() -> str:
    """Return 'linux', 'macos', or 'windows'."""
    s = platform.system()
    if s == "Linux":
        return "linux"
    elif s == "Darwin":
        return "macos"
    else:
        return "windows"


def get_timer_status(config_path: str | Path, interval: int = 15) -> TimerStatus:
    """Check if timer is installed and active. Returns rich status dict."""
    try:
        platform_name = detect_platform()
        if platform_name == "linux":
            return _status_linux(config_path, interval)
        if platform_name == "macos":
            return _status_macos(config_path, interval)
        return _status_windows(config_path, interval)
    except Exception as e:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
            error=str(e),
        )


def install_timer(config_path: str | Path, interval: int = 15, replace: bool = False) -> str | None:
    """Install platform-native timer. Returns error string or None on success."""
    # Validate interval range before any platform operations
    if not (5 <= interval <= 120):
        return f"Dispatch interval must be between 5 and 120 minutes (got {interval})."

    canonical_path = _canonical_config_path(config_path)
    status = get_timer_status(canonical_path, interval)
    if status["error"] and not status["installed"]:
        return status["error"]
    if status["config_conflict"] and not replace:
        return f"Agency dispatcher already targets another config: {status['config_path']}. Re-run with explicit replacement approval."
    platform_name = detect_platform()
    if platform_name == "linux":
        return _install_linux(canonical_path, interval)
    if platform_name == "macos":
        return _install_macos(canonical_path, interval)
    return _install_windows(canonical_path, interval)


def uninstall_timer(config_path: str | Path, force: bool = False) -> str | None:
    """Remove platform-native timer. Returns error string or None on success."""
    canonical_path = _canonical_config_path(config_path)
    status = get_timer_status(canonical_path, 15)  # Use default interval for status check
    if status["error"] and (not status["installed"] or not force):
        return status["error"]
    if not status["installed"]:
        return None
    if status["config_conflict"] and not force:
        return f"Agency dispatcher targets another config: {status['config_path']}. Re-run with explicit force approval."
    platform_name = detect_platform()
    if platform_name == "linux":
        return _uninstall_linux()
    if platform_name == "macos":
        return _uninstall_macos()
    return _uninstall_windows()


# ── Windows (Task Scheduler) ─────────────────────────────────────────────────


def _windows_python_launcher() -> str:
    """Return pythonw.exe (no console window) if present, else sys.executable."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return str(exe)


def _install_windows(config_path: str, interval: int) -> str | None:
    """Register the AgencyDispatch Task Scheduler task."""
    try:
        from win32com.client import Dispatch
    except ImportError:
        return (
            "pywin32 is required for Windows dispatch. "
            "Install it with: pip install pywin32"
        )
    try:
        launcher = _windows_python_launcher()
        canonical_path = _canonical_config_path(config_path)
        working_dir = str(Path(__file__).parent.parent.parent)

        scheduler = Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        task_def = scheduler.NewTask(0)

        task_def.RegistrationInfo.Description = "Agency Agent Dispatch"
        task_def.RegistrationInfo.Author = "Agency"

        settings = task_def.Settings
        settings.Enabled = True
        settings.StartWhenAvailable = True

        trigger = task_def.Triggers.Create(1)  # TASK_TRIGGER_TIME
        trigger.StartBoundary = datetime.now().replace(microsecond=0).isoformat()
        trigger.Repetition.Interval = f"PT{interval}M"

        action = task_def.Actions.Create(0)  # TASK_ACTION_EXEC
        action.Path = launcher
        action.Arguments = f'-m agency.dispatch.run --config "{canonical_path}"'
        action.WorkingDirectory = working_dir

        folder.RegisterTaskDefinition(
            WINDOWS_TASK_NAME,
            task_def,
            6,      # TASK_CREATE_OR_UPDATE
            None,   # user (current)
            None,   # password
            3,      # TASK_LOGON_INTERACTIVE_TOKEN
        )
        return None
    except Exception as e:
        return str(e)


def _status_windows(config_path: str | Path, interval: int) -> TimerStatus:
    """Report whether the AgencyDispatch task exists and is active."""
    try:
        from win32com.client import Dispatch
    except ImportError:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
            error="pywin32 is required for Windows dispatch. Install it with: pip install pywin32",
        )
    try:
        scheduler = Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
    except Exception as error:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
            error=str(error),
        )
    try:
        task = folder.GetTask(WINDOWS_TASK_NAME)
    except Exception:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
        )
    mismatches: list[str] = []
    actual_config_path = None
    actual_interval = None
    try:
        action = task.Definition.Actions.Item(1)
        if not _paths_equal(action.Path, _windows_python_launcher()):
            mismatches.append("executable")
        arguments = str(action.Arguments or "")
        if "-m agency.dispatch.run" not in arguments:
            mismatches.append("module")
        actual_config_path = _extract_config_path(arguments)
    except Exception:
        mismatches.append("action")
    try:
        trigger = task.Definition.Triggers.Item(1)
        actual_interval = _parse_iso_minutes(str(trigger.Repetition.Interval or ""))
    except Exception:
        mismatches.append("trigger")
    enabled = bool(task.Enabled)
    timer_active = enabled and task.State in (3, 4)
    return _make_status(
        expected_config_path=config_path,
        expected_interval=interval,
        installed=True,
        enabled=enabled,
        timer_active=timer_active,
        config_path=actual_config_path,
        interval=actual_interval,
        mismatches=mismatches,
    )


def _uninstall_windows() -> str | None:
    """Delete the AgencyDispatch task. Missing task is treated as success."""
    try:
        from win32com.client import Dispatch
    except ImportError:
        return None
    try:
        scheduler = Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
    except Exception as e:
        # Could not reach the Task Scheduler service — a real failure.
        return str(e)
    try:
        folder.DeleteTask(WINDOWS_TASK_NAME, 0)
    except Exception:
        # Task not found (or already removed) — treat as success.
        return None
    return None


# ── Linux (systemd) ──────────────────────────────────────────────────────────


def _linux_python_launcher() -> Path:
    candidate = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"
    return candidate if candidate.exists() else Path(sys.executable)


def _systemd_quote(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _status_linux(config_path: str | Path, interval: int) -> TimerStatus:
    """Check systemd timer status."""
    service_file = SYSTEMD_USER_DIR / "agency-dispatch.service"
    timer_file = SYSTEMD_USER_DIR / "agency-dispatch.timer"
    installed = service_file.exists() or timer_file.exists()
    if not installed:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
        )
    mismatches: list[str] = []
    actual_config_path = None
    actual_interval = None
    if not service_file.exists() or not timer_file.exists():
        mismatches.append("units")
    if service_file.exists():
        service_text = service_file.read_text(encoding="utf-8")
        exec_line = next(
            (line.removeprefix("ExecStart=") for line in service_text.splitlines() if line.startswith("ExecStart=")),
            "",
        )
        try:
            arguments = shlex.split(exec_line)
        except ValueError:
            arguments = []
        if not arguments or not _paths_equal(arguments[0], _linux_python_launcher()):
            mismatches.append("executable")
        if arguments[1:3] != ["-m", "agency.dispatch.run"]:
            mismatches.append("module")
        if "--config" in arguments:
            config_index = arguments.index("--config") + 1
            if config_index < len(arguments):
                actual_config_path = _canonical_config_path(arguments[config_index])
    if timer_file.exists():
        timer_text = timer_file.read_text(encoding="utf-8")
        match = re.search(r"^OnUnitActiveSec=(\d+)m$", timer_text, re.MULTILINE)
        actual_interval = int(match.group(1)) if match else None
    try:
        enabled_result = subprocess.run(
            ["systemctl", "--user", "is-enabled", "agency-dispatch.timer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active_result = subprocess.run(
            ["systemctl", "--user", "is-active", "agency-dispatch.timer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        enabled = enabled_result.stdout.strip() == "enabled"
        active = active_result.stdout.strip() == "active"
        inspection_error = None
    except Exception as error:
        enabled = False
        active = False
        inspection_error = str(error)
    return _make_status(
        expected_config_path=config_path,
        expected_interval=interval,
        installed=True,
        enabled=enabled,
        timer_active=enabled and active,
        config_path=actual_config_path,
        interval=actual_interval,
        mismatches=mismatches,
        error=inspection_error,
    )


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
        launcher = _linux_python_launcher()
        canonical_path = _canonical_config_path(config_path)
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        (SYSTEMD_USER_DIR / "agency-dispatch.service").write_text(
            "[Unit]\nDescription=Agency Agent Dispatch\n\n"
            "[Service]\nType=oneshot\n"
            f"ExecStart={_systemd_quote(launcher)} -m agency.dispatch.run --config {_systemd_quote(canonical_path)}\n"
            f"Environment=PATH={_build_path_env()}\nEnvironment=HOME=%h\n",
            encoding="utf-8",
        )
        (SYSTEMD_USER_DIR / "agency-dispatch.timer").write_text(
            "[Unit]\nDescription=Agency Agent Dispatch Timer\n\n"
            f"[Timer]\nOnBootSec={interval}m\nOnUnitActiveSec={interval}m\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "agency-dispatch.timer"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return None
    except Exception as error:
        return str(error)


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


def _macos_python_launcher() -> Path:
    candidate = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"
    return candidate if candidate.exists() else Path(sys.executable)


def _status_macos(config_path: str | Path, interval: int) -> TimerStatus:
    """Check launchd plist status."""
    plist_path = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
    if not plist_path.exists():
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
        )
    mismatches: list[str] = []
    try:
        with plist_path.open("rb") as plist_file:
            definition = plistlib.load(plist_file)
        arguments = definition.get("ProgramArguments", [])
        if not arguments or not _paths_equal(arguments[0], _macos_python_launcher()):
            mismatches.append("executable")
        if arguments[1:3] != ["-m", "agency.dispatch.run"]:
            mismatches.append("module")
        actual_config_path = None
        if "--config" in arguments:
            config_index = arguments.index("--config") + 1
            if config_index < len(arguments):
                actual_config_path = _canonical_config_path(arguments[config_index])
        seconds = definition.get("StartInterval")
        actual_interval = seconds // 60 if isinstance(seconds, int) and seconds % 60 == 0 else None
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=True,
            mismatches=["definition"],
            error=str(error),
        )
    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_PLIST],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active = result.returncode == 0
        inspection_error = None
    except Exception as error:
        active = False
        inspection_error = str(error)
    return _make_status(
        expected_config_path=config_path,
        expected_interval=interval,
        installed=True,
        enabled=active,
        timer_active=active,
        config_path=actual_config_path,
        interval=actual_interval,
        mismatches=mismatches,
        error=inspection_error,
    )


def _install_macos(config_path: str, interval: int) -> str | None:
    """Write launchd plist and load it."""
    try:
        canonical_path = _canonical_config_path(config_path)
        plist_path = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
        LAUNCHD_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        if plist_path.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        with plist_path.open("wb") as plist_file:
            plistlib.dump(
                {
                    "Label": LAUNCHD_PLIST,
                    "ProgramArguments": [
                        str(_macos_python_launcher()),
                        "-m",
                        "agency.dispatch.run",
                        "--config",
                        canonical_path,
                    ],
                    "StartInterval": interval * 60,
                    "RunAtLoad": True,
                },
                plist_file,
            )
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return None
    except Exception as error:
        return str(error)


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
