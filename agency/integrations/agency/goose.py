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
        path = agent_dir / ".goosehints"
        if not path.is_file():
            return None
        body = path.read_text()
        meta = read_sidecar(agent_dir)
        return AgentIdentity(
            display_name=meta.get("display_name"),
            title=meta.get("title"),
            emoji=meta.get("emoji"),
            body=body,
        )

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        path = agent_dir / ".goosehints"
        path.write_text(identity.body)
        meta = read_sidecar(agent_dir)
        for key, value in [
            ("display_name", identity.display_name),
            ("title", identity.title),
            ("emoji", identity.emoji),
        ]:
            if value:
                meta[key] = value
            elif key in meta and not value:
                del meta[key]
        write_sidecar(agent_dir, meta)

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
            raise IntegrationError(f"Goose CLI not found. Looked for: {cmd}")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("goose")


_register(GooseIntegration())
