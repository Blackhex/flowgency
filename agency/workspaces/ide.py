"""Generic IDE workspace plugin (VS Code, Windsurf, JetBrains, etc.)."""

from agency.workspaces import BaseWorkspace, _register


class IdeWorkspace(BaseWorkspace):
    name = "ide"
    display_name = "IDE"
    icon_svg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>'
    description = "Generic IDE workspace (VS Code, Windsurf, JetBrains)"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "ide_name", "label": "IDE Name", "type": "text", "placeholder": "VS Code, Windsurf, JetBrains...", "required": True},
            {"key": "project_path", "label": "Project Path", "type": "text", "placeholder": "/path/to/project", "required": True},
            {"key": "launch_cmd", "label": "Launch Command", "type": "text", "placeholder": "code /path/to/project", "required": False},
            {"key": "notes", "label": "Notes", "type": "textarea", "placeholder": "Workspace layout, extensions, config details...", "required": False},
        ]

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("ide_name"):
            errors.append("'ide_name' is required for IDE workspace")
        if not config.get("project_path"):
            errors.append("'project_path' is required for IDE workspace")
        return errors

    def render_summary(self, config: dict) -> str:
        name = config.get("ide_name", "IDE")
        path = config.get("project_path", "")
        return f'{name} at <span class="font-mono text-xs text-gray-500">{path}</span>'

    def supports_launch(self) -> bool:
        return True

    def launch_command(self, config: dict, group_path: str) -> str | None:
        return config.get("launch_cmd")


_register(IdeWorkspace())
