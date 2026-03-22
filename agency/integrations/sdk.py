"""Stub — replaced in Task 4."""
from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, _register


class SdkIntegration(BaseIntegration):
    name = "sdk"
    display_name = "SDK"
    detect_priority = 999

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / "agent.md").is_file()

    def identity_filename(self) -> str:
        return "agent.md"

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return None

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        pass


_register(SdkIntegration())
