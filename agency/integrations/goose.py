"""Stub — replaced in Task 3."""
from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, _register


class GooseIntegration(BaseIntegration):
    name = "goose"
    display_name = "Goose"
    detect_priority = 10

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".goosehints").is_file()

    def identity_filename(self) -> str:
        return ".goosehints"

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return None

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        pass


_register(GooseIntegration())
