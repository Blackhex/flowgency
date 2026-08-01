"""Goose CLI integration."""

import subprocess
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
    read_sidecar, write_sidecar,
)
from agency.integrations.models import IntegrationRunRequest, RuntimeCapabilities


class GooseIntegration(BaseIntegration):
    name = "goose"
    display_name = "Goose"
    cli_command = "goose"
    supports_execution = True
    supports_ai_backend = True
    detect_priority = 10
    projector = BaseIntegration._default_projector(".goosehints", discovers_instructions=True)
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"unrestricted"}),
    )

    def identity_filename(self) -> str:
        return ".goosehints"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".goosehints").is_file()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, agent_dir / ".goosehints")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, agent_dir / ".goosehints", identity)

    def run(self, request: IntegrationRunRequest) -> RunResult:
        self.require_valid_run(request)
        prompt_text = request.task_file.read_text()
        cmd = self.require_executable()
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "run", prompt_text],
                capture_output=True, text=True, timeout=request.timeout,
                cwd=str(request.launch_dir),
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


_register(GooseIntegration())
