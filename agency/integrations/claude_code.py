"""Stub — replaced in Task 2."""
from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, _register


class ClaudeCodeIntegration(BaseIntegration):
    name = "claude-code"
    display_name = "Claude Code"
    detect_priority = 10

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / "CLAUDE.md").is_file()

    def identity_filename(self) -> str:
        return "CLAUDE.md"

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return None

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        pass


_register(ClaudeCodeIntegration())
