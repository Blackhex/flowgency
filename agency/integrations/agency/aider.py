"""Aider CLI integration."""

import shutil
import subprocess
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
    read_sidecar, write_sidecar,
)


class AiderIntegration(BaseIntegration):
    name = "aider"
    display_name = "Aider"
    supports_execution = True
    supports_ai_backend = False
    detect_priority = 10

    def identity_filename(self) -> str:
        return "CONVENTIONS.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".aider.conf.yml").is_file()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, agent_dir / "CONVENTIONS.md")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, agent_dir / "CONVENTIONS.md", identity)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: Path | None = None) -> RunResult:
        cmd = self._find_cmd()
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "--message-file", str(prompt_file)],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(agent_dir),
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
            raise IntegrationError(f"Aider CLI not found. Looked for: {cmd}")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("aider")


_register(AiderIntegration())
