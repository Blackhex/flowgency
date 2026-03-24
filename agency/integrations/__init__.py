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
INTEGRATIONS_DIR = Path(__file__).parent


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


def _get_config_path() -> Path:
    """Path to integrations.yaml."""
    return INTEGRATIONS_DIR / "integrations.yaml"


def _read_config() -> list[str]:
    """Read the list of integration module paths from config."""
    config_path = _get_config_path()
    if not config_path.exists():
        return []
    data = yaml.safe_load(config_path.read_text()) or {}
    return data.get("integrations", [])


def _write_config(modules: list[str]) -> None:
    """Write the integration module list to config."""
    config_path = _get_config_path()
    data = {"integrations": modules}
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    config_path.write_text(content)


def load_integrations() -> None:
    """Load integrations from integrations.yaml config."""
    import importlib
    import logging
    logger = logging.getLogger("agency.integrations")

    modules = _read_config()
    if not modules:
        # First run or missing config — create default
        modules = [
            "agency.claude_code", "agency.codex", "agency.gemini",
            "agency.aider", "agency.goose", "agency.script", "agency.sdk",
        ]
        _write_config(modules)

    for module_path in modules:
        # module_path is like "agency.claude_code" → import "agency.integrations.agency.claude_code"
        full_module = f"agency.integrations.{module_path}"
        try:
            importlib.import_module(full_module)
        except Exception as e:
            logger.warning(f"Failed to load integration '{module_path}': {e}")


def scan_available() -> list[dict]:
    """Scan subdirectories for integration files not yet in config.

    Returns list of dicts: {"module_path": "author.name", "author": "author", "filename": "name.py"}
    """
    registered = set(_read_config())
    available = []

    for subdir in sorted(INTEGRATIONS_DIR.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(("_", ".")):
            continue
        if subdir.name == "__pycache__":
            continue
        for py_file in sorted(subdir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_path = f"{subdir.name}.{py_file.stem}"
            if module_path in registered:
                continue
            # Check if file likely contains a BaseIntegration subclass
            try:
                content = py_file.read_text()
                if "BaseIntegration" in content and "_register" in content:
                    available.append({
                        "module_path": module_path,
                        "author": subdir.name,
                        "filename": py_file.name,
                    })
            except (OSError, UnicodeDecodeError):
                continue

    return available


def register_integration(module_path: str) -> None:
    """Add an integration to integrations.yaml."""
    modules = _read_config()
    if module_path not in modules:
        modules.append(module_path)
        _write_config(modules)


def unregister_integration(module_path: str) -> None:
    """Remove an integration from integrations.yaml."""
    modules = _read_config()
    if module_path in modules:
        modules.remove(module_path)
        _write_config(modules)


# Load integrations on import
load_integrations()
