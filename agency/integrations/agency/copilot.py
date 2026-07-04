"""GitHub Copilot CLI integration."""

import subprocess
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
        cmd = self._find_cmd()
        if sandbox_root is not None:
            # Confined mode: run FROM the sandbox root. Copilot reliably grants
            # native file access to paths under the working directory, so
            # launching with cwd=sandbox_root puts the whole tree in scope
            # (this mirrors the proven task-scheduler launch). With cwd at the
            # root, --autopilot has nothing outside-scope to approve, so it runs
            # non-interactively AND enables shell commands across the tree. The
            # read/write/shell grants keep the -p run from stalling; the timeout
            # guards against a tool invoked outside the allow-list.
            work_dir = str(sandbox_root)
            cmd_args = [
                cmd, "-p", prompt_text, "--autopilot",
                "--allow-tool=read",
                "--allow-tool=write",
                "--allow-tool=shell",
                "--experimental",
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
        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True, text=True, timeout=timeout,
                cwd=work_dir,
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


_register(CopilotIntegration())
