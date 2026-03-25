"""OpenCode CLI integration."""

import subprocess
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
)


class OpenCodeIntegration(BaseIntegration):
    name = "opencode"
    display_name = "OpenCode"
    supports_execution = True
    supports_ai_backend = False
    detect_priority = 8

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".opencode").is_dir()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, agent_dir / "AGENTS.md")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, agent_dir / "AGENTS.md", identity)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        prompt_text = prompt_file.read_text()
        cmd = self._find_cmd()
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "run", prompt_text],
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
            raise IntegrationError(f"OpenCode CLI not found. Looked for: {cmd}")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("opencode")


_register(OpenCodeIntegration())
