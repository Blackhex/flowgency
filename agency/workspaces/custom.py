"""Custom workspace plugin — any config file the user wants to manage."""

from agency.workspaces import BaseWorkspace, _register


class CustomWorkspace(BaseWorkspace):
    name = "custom"
    display_name = "Custom"
    icon_svg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v 2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v 2m0-6V4"/></svg>'
    description = "Any custom config file or workspace"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "label", "label": "Display Name", "type": "text", "placeholder": "My Custom Workspace", "required": False},
            {"key": "config_path", "label": "Config File Path", "type": "text", "placeholder": "/path/to/config.yaml", "required": True},
            {"key": "language", "label": "File Language", "type": "select", "options": ["bash", "json", "yaml", "markdown", "toml", "text"], "required": False},
            {"key": "launch_cmd", "label": "Launch Command", "type": "text", "placeholder": "Optional command to launch this workspace", "required": False},
        ]

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("config_path"):
            errors.append("'config_path' is required for custom workspace")
        return errors

    def render_summary(self, config: dict) -> str:
        label = config.get("label", "Custom")
        path = config.get("config_path", "")
        return f'{label}: <span class="font-mono text-xs text-gray-500">{path}</span>'

    def get_config_files(self, config: dict) -> list[dict]:
        path = config.get("config_path", "")
        if not path:
            return []
        return [{"label": config.get("label", "Config"), "path": path, "language": config.get("language", "text")}]

    def supports_launch(self) -> bool:
        return True

    def launch_command(self, config: dict, group_path: str) -> str | None:
        return config.get("launch_cmd")


_register(CustomWorkspace())
