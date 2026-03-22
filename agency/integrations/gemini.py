"""Stub — replaced in Task 3."""
from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, _register


class GeminiIntegration(BaseIntegration):
    name = "gemini"
    display_name = "Gemini"
    detect_priority = 10

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / "GEMINI.md").is_file()

    def identity_filename(self) -> str:
        return "GEMINI.md"

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return None

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        pass


_register(GeminiIntegration())
