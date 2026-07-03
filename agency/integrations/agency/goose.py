"""Goose CLI integration."""

import shutil
import subprocess
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
    read_sidecar, write_sidecar,
)


class GooseIntegration(BaseIntegration):
    name = "goose"
    display_name = "Goose"
    supports_execution = True
    supports_ai_backend = True
    detect_priority = 10

    def identity_filename(self) -> str:
        return ".goosehints"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".goosehints").is_file()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, agent_dir / ".goosehints")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, agent_dir / ".goosehints", identity)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: Path | None = None) -> RunResult:
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
            raise IntegrationError(f"Goose CLI not found. Looked for: {cmd}")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("goose")


_register(GooseIntegration())
