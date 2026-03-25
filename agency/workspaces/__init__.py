"""Workspace plugin system for Agency.

Workspaces represent how users visualize and interact with their agent groups
at runtime — tmux grids, IDE windows, chat channels, dedicated UIs, etc.
Modeled after the integration plugin system.
"""


class BaseWorkspace:
    """Base class for all workspace plugins. Subclass and register to add a new one."""
    name: str = ""
    display_name: str = ""
    icon_svg: str = ""  # Inline SVG for sidebar/badges
    description: str = ""  # One-line description shown in admin

    def config_schema(self) -> list[dict]:
        """Return list of config fields for the admin form.

        Each dict: {"key": str, "label": str, "type": "text"|"textarea"|"select",
                     "placeholder": str, "required": bool, "options": list[str] (for select)}
        """
        return []

    def validate_config(self, config: dict) -> list[str]:
        """Validate workspace config. Return list of error messages."""
        return []

    def render_summary(self, config: dict) -> str:
        """Return a short HTML summary for the workspace list page."""
        return f"{self.display_name} workspace"

    def get_config_files(self, config: dict) -> list[dict]:
        """Return list of viewable/editable config files.

        Each dict: {"label": str, "path": str, "language": str}
        language is for syntax highlighting hints: "bash", "json", "markdown", "yaml", etc.
        """
        return []

    def supports_launch(self) -> bool:
        """Whether this workspace type can be launched programmatically."""
        return False

    def launch_command(self, config: dict, group_path: str) -> str | None:
        """Return a shell command to launch this workspace, or None."""
        return None

    def detect(self, group_path: str) -> dict | None:
        """Auto-detect this workspace type from a group directory.

        Returns a config dict if detected, None otherwise.
        Used during group setup/autodetect.
        """
        return None


# ── Registry ──────────────────────────────────────────────────────────────────

REGISTRY: dict[str, BaseWorkspace] = {}


def _register(workspace: BaseWorkspace) -> None:
    """Register a workspace plugin instance."""
    REGISTRY[workspace.name] = workspace


def get_workspace(name: str) -> BaseWorkspace:
    """Get workspace plugin by name. Raises KeyError if not found."""
    return REGISTRY[name]


# Import all workspace plugins to trigger registration.
from agency.workspaces.tmux import TmuxWorkspace  # noqa: E402, F401
from agency.workspaces.cursor import CursorWorkspace  # noqa: E402, F401
from agency.workspaces.superset import SupersetWorkspace  # noqa: E402, F401
from agency.workspaces.ide import IdeWorkspace  # noqa: E402, F401
from agency.workspaces.chat import ChatWorkspace  # noqa: E402, F401
from agency.workspaces.custom import CustomWorkspace  # noqa: E402, F401
