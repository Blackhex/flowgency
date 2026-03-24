"""
Integration template for Agency.

HOW TO USE:
1. Create your author directory: agency/integrations/{your-name}/
2. Add an empty __init__.py to your directory
3. Copy this file there and rename it: agency/integrations/{your-name}/your_tool.py
4. Fill in each method below (see comments for guidance)
5. Visit Admin → Integrations in the dashboard to register
6. Restart the service

TESTING:
  .venv/bin/python -m pytest tests/test_integration_contract.py -v
"""

from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, RunResult, _register


class YourToolIntegration(BaseIntegration):
    """Integration for YourTool CLI."""

    name = "your-tool"
    display_name = "Your Tool"
    supports_execution = False
    supports_ai_backend = False
    detect_priority = 100

    def identity_filename(self) -> str:
        """The identity/config file this tool uses natively.
        Example: 'CLAUDE.md', 'AGENTS.md', '.cursorrules'
        """
        return "YOUR_CONFIG_FILE"

    def detect(self, agent_dir: Path) -> bool:
        """Return True if agent_dir belongs to this tool.
        Example:
            return (agent_dir / self.identity_filename()).exists()
        """
        return (agent_dir / self.identity_filename()).exists()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        """Read the agent's identity from its native file.
        Return an AgentIdentity(display_name, title, emoji, body).

        For tools with YAML frontmatter in their native file,
        parse it directly. For tools without frontmatter support,
        read from .agency-meta.yaml sidecar file instead.
        See existing integrations for examples of both patterns.
        """
        return AgentIdentity(display_name="", title="", emoji="", body="")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        """Write agent identity back to the native file or sidecar."""
        pass

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        """Execute the tool with a prompt file. Return a RunResult.
        Only needed if supports_execution is True.

        Example:
            import subprocess, time
            start = time.time()
            result = subprocess.run(
                ["your-tool", "--prompt", str(prompt_file)],
                cwd=str(agent_dir),
                capture_output=True, text=True, timeout=timeout
            )
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=time.time() - start,
            )
        """
        raise NotImplementedError("This integration does not support execution")


# Do NOT register the template — it's a scaffolding file, not a real integration.
# Uncomment this line after you've filled in your integration:
# _register(YourToolIntegration())
