"""Integration plugin system for Agency."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.integrations.errors import IntegrationError
from agency.integrations.interactive import (
    format_interactive_command,
    spawn_interactive_terminal,
    terminal_available,
)
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    InteractiveSetupRequest,
    InteractiveSetupResult,
    ProjectorCapabilities,
    RuntimeCapabilities,
)

SIDECAR_FILENAME = ".agency-meta.yaml"


@dataclass
class FileChange:
    """A single file change reported by an integration after a run."""
    path: str            # relative to sandbox root when possible; absolute fallback
    status: str          # "added" | "modified" | "deleted"
    lines_added: int
    lines_removed: int


@dataclass
class RunResult:
    """Result of running an agent via an integration."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    changed_files: list["FileChange"] = field(default_factory=list)


@dataclass
class AgentIdentity:
    """Agent identity extracted from a tool's native file."""
    display_name: str | None
    title: str | None
    emoji: str | None
    body: str


def parse_identity_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Shared by integrations that use frontmatter identity files."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 3:].strip()
    try:
        meta = yaml.safe_load(front) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, body


class BaseIntegration:
    """Base class for all integrations. Subclass and register to add a new one."""
    name: str = ""
    display_name: str = ""
    supports_execution: bool = True
    supports_ai_backend: bool = False
    supports_sandbox: bool = False
    detect_priority: int = 100
    runtime_capabilities: RuntimeCapabilities = RuntimeCapabilities()
    projector = None

    def run(self, request: IntegrationRunRequest) -> RunResult:
        """Execute an agent with a typed immutable run request."""
        raise NotImplementedError

    def identity_filename(self) -> str:
        """The native file this tool uses for project instructions."""
        raise NotImplementedError

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        """Read the tool's native file and extract agent identity."""
        raise NotImplementedError

    def prepare_agent_dir(self, agent_dir: Path) -> None:
        """Create integration-specific filesystem markers before identity writes."""

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        """Write agent identity back in the tool's native format."""
        raise NotImplementedError

    @staticmethod
    def _resolve_cmd(name: str) -> str:
        """Find a CLI command by name, checking user-local paths as fallback.

        Systemd services have minimal PATH and won't find tools installed to
        ~/.local/bin or similar. This checks common locations after which().
        """
        found = shutil.which(name)
        if found:
            return found
        home = Path.home()
        for candidate in [
            home / ".local" / "bin" / name,
            home / ".npm-global" / "bin" / name,
            Path(f"/usr/local/bin/{name}"),
        ]:
            if candidate.exists():
                return str(candidate)
        return name

    def detect(self, agent_dir: Path) -> bool:
        """Does this directory look like an agent managed by this tool?"""
        return False

    def default_config(self) -> dict:
        """Default integration_config values for this integration."""
        return {}

    def validate_config(self, config: dict) -> list[str]:
        """Validate integration_config. Return list of error messages."""
        return []

    def validate_runtime_policy(
        self,
        policy: EffectiveRuntimePolicy,
    ) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if policy.sandbox_mode not in self.runtime_capabilities.path_modes:
            issues.append(
                ValidationIssue(
                    code="unsupported-path-policy",
                    scope=f"integrations.{self.name}",
                    field="runtime.sandbox.mode",
                    message=(
                        f"Integration '{self.name}' cannot enforce sandbox mode "
                        f"'{policy.sandbox_mode}'."
                    ),
                    corrective_hint="Use a supported sandbox mode for this integration.",
                )
            )
        if policy.tools.mode not in self.runtime_capabilities.tool_modes:
            issues.append(
                ValidationIssue(
                    code="unsupported-tool-policy",
                    scope=f"integrations.{self.name}",
                    field="runtime.tools.mode",
                    message=(
                        f"Integration '{self.name}' cannot enforce tool mode "
                        f"'{policy.tools.mode}'."
                    ),
                    corrective_hint="Use a supported tool mode for this integration.",
                )
            )
        return tuple(issues)

    def validate_run(self, request: IntegrationRunRequest) -> tuple[ValidationIssue, ...]:
        issues = list(self.validate_runtime_policy(request.runtime_policy))
        if not self.supports_execution:
            issues.append(
                ValidationIssue(
                    code="integration-not-executable",
                    scope=f"integrations.{self.name}",
                    field="runtime.execution",
                    message=f"Integration '{self.name}' does not support runtime execution.",
                    corrective_hint="Choose an executable integration before scheduling or launching a run.",
                )
            )
            return tuple(issues)
        projector = self.projector
        if projector is None:
            issues.append(
                ValidationIssue(
                    code="missing-runtime-projector",
                    scope=f"integrations.{self.name}",
                    field="runtime.projector",
                    message=f"Integration '{self.name}' has no runtime projector.",
                    corrective_hint="Attach a runtime projector before executing this integration.",
                )
            )
            return tuple(issues)
        if request.skill is not None:
            caps = projector.capabilities
            if not (caps.discovers_skills and caps.activates_selected_skill):
                issues.append(
                    ValidationIssue(
                        code="unsupported-skill-activation",
                        scope=f"integrations.{self.name}",
                        field="runtime.skill",
                        message=(
                            f"Integration '{self.name}' cannot reliably discover and activate routine skills "
                            "with the current CLI contract."
                        ),
                        corrective_hint="Use a verified skill-compatible integration or run without a routine skill.",
                    )
                )
        return tuple(issues)

    def require_valid_run(self, request: IntegrationRunRequest) -> None:
        if not request.enforce_validation:
            return
        issues = self.validate_run(request)
        if issues:
            raise ValidationFailed(issues)

    def interactive_setup_available(self) -> bool:
        return False

    def launch_interactive_setup(
        self,
        request: InteractiveSetupRequest,
    ) -> InteractiveSetupResult:
        raise IntegrationError(
            f"{self.display_name or self.name or 'Integration'} does not support interactive setup."
        )

    def interactive_setup_fallback_command(
        self,
        request: InteractiveSetupRequest,
    ) -> str:
        raise IntegrationError(
            f"{self.display_name or self.name or 'Integration'} does not support interactive setup fallback commands."
        )

    @staticmethod
    def _default_projector(instruction_name: str) -> "RuntimeProjector":
        from agency.blueprints.projectors import StaticRuntimeProjector

        return StaticRuntimeProjector(
            version="v1",
            capabilities=ProjectorCapabilities(
                instruction_target=PurePosixPath(instruction_name),
                skills_target=PurePosixPath(".agents/skills"),
                discovers_skills=False,
                activates_selected_skill=False,
            ),
        )

    def _write_sidecar_identity(self, agent_dir: Path, identity_file: Path, identity: AgentIdentity) -> None:
        """Write identity for sidecar-based integrations (body to identity file, meta to sidecar)."""
        identity_file.write_text(identity.body)
        meta = read_sidecar(agent_dir)
        for key, value in [
            ("display_name", identity.display_name),
            ("title", identity.title),
            ("emoji", identity.emoji),
        ]:
            if value:
                meta[key] = value
            elif key in meta and not value:
                del meta[key]
        write_sidecar(agent_dir, meta)

    def _parse_sidecar_identity(self, agent_dir: Path, identity_file: Path) -> AgentIdentity | None:
        """Parse identity for sidecar-based integrations (body from file, meta from sidecar)."""
        if not identity_file.is_file():
            return None
        body = identity_file.read_text()
        meta = read_sidecar(agent_dir)
        return AgentIdentity(
            display_name=meta.get("display_name"),
            title=meta.get("title"),
            emoji=meta.get("emoji"),
            body=body,
        )

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
            "agency.aider", "agency.goose", "agency.opencode", "agency.pi",
            "agency.copilot", "agency.script", "agency.sdk",
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
