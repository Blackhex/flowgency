"""Stub — replaced in Task 4."""
from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, _register


class ScriptIntegration(BaseIntegration):
    name = "script"
    display_name = "Script"
    detect_priority = 1000

    def detect(self, agent_dir: Path) -> bool:
        return False

    def identity_filename(self) -> str:
        return ""

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return None

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        pass


_register(ScriptIntegration())
