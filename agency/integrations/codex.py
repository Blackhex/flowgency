"""Stub — replaced in Task 3."""
from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, _register


class CodexIntegration(BaseIntegration):
    name = "codex"
    display_name = "Codex"
    detect_priority = 10

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / "AGENTS.md").is_file()

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return None

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        pass


_register(CodexIntegration())
