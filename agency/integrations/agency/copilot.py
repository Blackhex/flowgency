"""GitHub Copilot CLI integration."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
)


class CopilotIntegration(BaseIntegration):
    name = "copilot"
    display_name = "GitHub Copilot"
    supports_execution = True
    supports_ai_backend = True
    supports_sandbox = True
    detect_priority = 7

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def _identity_file(self, agent_dir: Path) -> Path:
        return agent_dir / "AGENTS.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".copilot").is_dir() or (agent_dir / ".github").is_dir()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, self._identity_file(agent_dir))

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, self._identity_file(agent_dir), identity)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: Path | None = None) -> RunResult:
        prompt_text = prompt_file.read_text()
        cmd = self._resolve_real_cmd(self._find_cmd())
        if sandbox_root is not None:
            # Confined mode: run FROM the sandbox root so relative writes land
            # in the sandbox tree (this mirrors the proven task-scheduler
            # launch).
            #
            # Tools are pre-authorized with a single --allow-all-tools rather
            # than enumerated --allow-tool grants (github/copilot-cli#3699:
            # --allow-tool is not honored in non-interactive -p mode). This is
            # the canonical non-interactive form from `copilot --help`.
            #
            # --allow-all-paths is required: Copilot's shell AND native file
            # tools deny operations that touch paths outside the working
            # directory ("Permission denied and could not request permission
            # from user") without it. Real routines legitimately read agency
            # data outside the sandbox (e.g. ~/.agency-cowork/), and the proven
            # production task-scheduler launch sets this flag. cwd still anchors
            # relative writes to the sandbox tree.
            #
            # --autopilot is deliberately omitted: under --autopilot the shell
            # and write tools still perform a permission round-trip that fails
            # closed once the permission channel degrades mid-session
            # (github/copilot-cli#2971), even with --allow-all-tools set. Plain
            # -p --allow-all-tools pre-approves without that round-trip.
            work_dir = str(sandbox_root)
            cmd_args = [
                cmd, "-p", prompt_text,
                "--no-custom-instructions",
                "--no-ask-user",
                "--allow-all-tools",
                "--allow-all-paths",
                "--no-color",
            ]
        else:
            # Unrestricted mode: run from the agent dir with full filesystem
            # access and all tools pre-authorized, so --autopilot cannot stall.
            work_dir = str(agent_dir)
            cmd_args = [
                cmd, "-p", prompt_text, "--autopilot",
                "--allow-all-paths", "--allow-all-tools", "--experimental",
            ]
        start = time.monotonic()
        # On Windows `copilot` resolves to a .bat wrapper that spawns
        # powershell -> copilot.ps1 -> the real copilot.exe. That chain
        # re-allocates a console for the grandchild exe, so the CLI decides it
        # is interactive and tries to prompt for tool permission, which fails
        # closed in headless dispatch with "Permission denied and could not
        # request permission from user" (github/copilot-cli#2971) even with
        # --allow-all-tools set. _resolve_real_cmd() bypasses the wrapper and
        # returns copilot.exe directly; CREATE_NO_WINDOW then actually
        # suppresses the console for that process, so the CLI stays
        # non-interactive -- matching the proven no-console Start-Job launch
        # used by production dispatchers.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True, text=True, timeout=timeout,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            duration = time.monotonic() - start
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return RunResult(exit_code=124, stdout="", stderr="Timed out", duration_seconds=duration)
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")

    def prompt(self, text: str, timeout: int = 60) -> str:
        cmd = self._find_cmd()
        try:
            result = subprocess.run(
                [cmd, "-p", text, "--autopilot", "--experimental"],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                raise IntegrationError(f"copilot exited with code {result.returncode}: {result.stderr}")
            return result.stdout
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")
        except subprocess.TimeoutExpired:
            raise IntegrationError(f"copilot timed out after {timeout}s")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("copilot")

    @staticmethod
    def _resolve_real_cmd(cmd: str) -> str:
        """On Windows, resolve the real copilot.exe behind the wrapper.

        `shutil.which("copilot")` returns a .bat/.cmd/.ps1 bootstrapper that
        launches powershell + the real copilot.exe, re-allocating a console
        that makes the CLI think it is interactive. Invoking the .exe directly
        lets CREATE_NO_WINDOW keep the process console-free and headless.

        On non-Windows platforms (or when no wrapper is detected) the original
        command is returned unchanged.
        """
        if not sys.platform.startswith("win"):
            return cmd
        wrapper = Path(cmd)
        if wrapper.suffix.lower() not in (".bat", ".cmd", ".ps1"):
            return cmd
        # Re-search PATH with the wrapper's own directory removed, mirroring
        # the wrapper's Find-RealCopilot logic, to locate the real binary. Pass
        # the pruned path explicitly (rather than mutating os.environ) so the
        # lookup is thread-safe under concurrent dispatch.
        search = os.pathsep.join(
            d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and Path(d) != wrapper.parent
        )
        real = shutil.which("copilot", path=search)
        if real and Path(real).suffix.lower() == ".exe":
            return real
        return cmd


_register(CopilotIntegration())
