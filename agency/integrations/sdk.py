"""File-contract-only integration (no execution)."""

from pathlib import Path

import yaml

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, _register,
)
from agency.integrations.claude_code import _parse_frontmatter


class SdkIntegration(BaseIntegration):
    name = "sdk"
    display_name = "SDK (File Contract)"
    supports_execution = False
    supports_ai_backend = False
    detect_priority = 999  # Last resort

    def identity_filename(self) -> str:
        return "agent.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / "agent.md").is_file()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        path = agent_dir / "agent.md"
        if not path.is_file():
            return None
        meta, body = _parse_frontmatter(path.read_text())
        return AgentIdentity(
            display_name=meta.get("display_name"),
            title=meta.get("title"),
            emoji=meta.get("emoji"),
            body=body,
        )

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        path = agent_dir / "agent.md"
        meta = {}
        if identity.display_name:
            meta["display_name"] = identity.display_name
        if identity.title:
            meta["title"] = identity.title
        if identity.emoji:
            meta["emoji"] = identity.emoji
        if meta:
            front = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
            path.write_text(f"---\n{front}\n---\n\n{identity.body}")
        else:
            path.write_text(identity.body)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        return RunResult(
            exit_code=1,
            stdout="",
            stderr="This agent is externally managed (sdk integration). Agency does not execute it.",
            duration_seconds=0.0,
        )


_register(SdkIntegration())
