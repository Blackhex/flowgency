"""Integration plugin system for Agency."""

from dataclasses import dataclass
from pathlib import Path

import yaml

SIDECAR_FILENAME = ".agency-meta.yaml"


@dataclass
class RunResult:
    """Result of running an agent via an integration."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass
class AgentIdentity:
    """Agent identity extracted from a tool's native file."""
    display_name: str | None
    title: str | None
    emoji: str | None
    body: str


class IntegrationError(Exception):
    """Raised when an integration fails during prompt() or other operations."""
    pass


class BaseIntegration:
    """Base class for all integrations. Subclass and register to add a new one."""
    name: str = ""
    display_name: str = ""
    supports_execution: bool = True
    supports_ai_backend: bool = False
    detect_priority: int = 100

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        """Execute an agent with a prompt.

        prompt_file is always a Path to the prompt file on disk. The integration
        is responsible for deciding how to pass it to the tool.
        """
        raise NotImplementedError

    def identity_filename(self) -> str:
        """The native file this tool uses for project instructions."""
        raise NotImplementedError

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        """Read the tool's native file and extract agent identity."""
        raise NotImplementedError

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        """Write agent identity back in the tool's native format."""
        raise NotImplementedError

    def detect(self, agent_dir: Path) -> bool:
        """Does this directory look like an agent managed by this tool?"""
        return False

    def default_config(self) -> dict:
        """Default integration_config values for this integration."""
        return {}

    def validate_config(self, config: dict) -> list[str]:
        """Validate integration_config. Return list of error messages."""
        return []

    def prompt(self, text: str, timeout: int = 60) -> str:
        """Simple prompt -> response for Agency's own AI features.

        Raises IntegrationError on failure.
        """
        raise NotImplementedError


# ── Sidecar Helpers ───────────────────────────────────────────────────────────

def read_sidecar(agent_dir: Path) -> dict:
    """Read .agency-meta.yaml from agent dir. Returns {} if not found."""
    path = agent_dir / SIDECAR_FILENAME
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}


def write_sidecar(agent_dir: Path, meta: dict) -> None:
    """Write .agency-meta.yaml to agent dir."""
    path = agent_dir / SIDECAR_FILENAME
    path.write_text(yaml.dump(meta, default_flow_style=False, sort_keys=False))


# ── Registry ──────────────────────────────────────────────────────────────────

REGISTRY: dict[str, BaseIntegration] = {}


def _register(integration: BaseIntegration) -> None:
    """Register an integration instance."""
    REGISTRY[integration.name] = integration


def get_integration(name: str) -> BaseIntegration:
    """Get integration by name. Raises KeyError if not found."""
    return REGISTRY[name]


def detect_integration(agent_dir: Path) -> BaseIntegration | None:
    """Auto-detect which integration an agent directory belongs to.

    Checks in detect_priority order (lower first). The sdk integration
    (priority 999) is the fallback. script never auto-detects.
    """
    candidates = sorted(REGISTRY.values(), key=lambda i: i.detect_priority)
    for integration in candidates:
        if integration.detect(agent_dir):
            return integration
    return None


# Import all integrations to trigger registration.
# Each module calls _register() at import time.
from agency.integrations.claude_code import ClaudeCodeIntegration  # noqa: E402, F401
from agency.integrations.codex import CodexIntegration  # noqa: E402, F401
from agency.integrations.gemini import GeminiIntegration  # noqa: E402, F401
from agency.integrations.aider import AiderIntegration  # noqa: E402, F401
from agency.integrations.goose import GooseIntegration  # noqa: E402, F401
from agency.integrations.script import ScriptIntegration  # noqa: E402, F401
from agency.integrations.sdk import SdkIntegration  # noqa: E402, F401
