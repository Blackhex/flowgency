from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Protocol


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