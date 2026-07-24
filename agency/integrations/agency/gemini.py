"""Google Gemini CLI integration."""

import subprocess
import time
from pathlib import Path

from agency.blueprints.projectors import get_projector
from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
    read_sidecar, write_sidecar,
)
from agency.integrations.models import IntegrationRunRequest, RuntimeCapabilities


class GeminiIntegration(BaseIntegration):
    name = "gemini"
    display_name = "Google Gemini CLI"
    cli_command = "gemini"
    supports_execution = True
    supports_ai_backend = False
    detect_priority = 10
    projector = get_projector("gemini")
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"unrestricted"}),
        tool_modes=frozenset({"all"}),
    )

    def identity_filename(self) -> str:
        return "GEMINI.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / "GEMINI.md").is_file()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, agent_dir / "GEMINI.md")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, agent_dir / "GEMINI.md", identity)

    def run(self, request: IntegrationRunRequest) -> RunResult:
        self.require_valid_run(request)
        prompt_text = request.task_file.read_text()
        cmd = self.require_executable()
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "-p", prompt_text],
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
            raise IntegrationError(f"Gemini CLI not found. Looked for: {cmd}")


_register(GeminiIntegration())
