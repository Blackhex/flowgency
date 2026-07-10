from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable, Protocol


DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass(frozen=True)
class LaunchResult:
    worker_pid: int | None


class JobLauncher(Protocol):
    def launch(self, job_path: Path) -> LaunchResult: ...


class DetachedProcessLauncher:
    def launch(self, job_path: Path) -> LaunchResult:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agency.jobs.worker",
                str(job_path.resolve()),
            ],
            **kwargs,
        )
        return LaunchResult(worker_pid=process.pid)


def _sanitize_unit_name(job_id: str) -> str:
    """Convert a job ID into a systemd unit-safe string."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", job_id)


def _systemd_available() -> bool:
    """Check if user systemd manager and systemd-run are usable."""
    if sys.platform != "linux":
        return False
    if not shutil.which("systemd-run"):
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        # "running" or "degraded" both mean the manager is responsive
        output = result.stdout.decode().strip()
        return output in ("running", "degraded")
    except (OSError, subprocess.TimeoutExpired):
        return False


class SystemdRunLauncher:
    """Launch jobs as transient user systemd services via systemd-run."""

    def launch(self, job_path: Path) -> LaunchResult:
        unit_name = f"agency-job-{_sanitize_unit_name(job_path.stem)}"
        worker_cmd = [
            sys.executable,
            "-m",
            "agency.jobs.worker",
            str(job_path.resolve()),
        ]
        argv = [
            "systemd-run",
            "--user",
            "--collect",
            f"--unit={unit_name}",
            "--",
            *worker_cmd,
        ]
        subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=True,
        )
        # systemd-run does not report the service PID directly
        return LaunchResult(worker_pid=None)


def default_launcher(
    *, _detect: "Callable[[], bool] | None" = None
) -> JobLauncher:
    """Select the best launcher for the current platform.

    On Linux with a usable user systemd manager, returns SystemdRunLauncher.
    Otherwise returns DetachedProcessLauncher.

    The _detect parameter is for testing only — pass a callable returning bool
    to override systemd availability detection.
    """
    detect = _detect if _detect is not None else _systemd_available
    if detect():
        return SystemdRunLauncher()
    return DetachedProcessLauncher()
