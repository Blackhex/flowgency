"""Superset.sh workspace plugin."""

from pathlib import Path
from agency.workspaces import BaseWorkspace, _register


class SupersetWorkspace(BaseWorkspace):
    name = "superset"
    display_name = "Superset"
    icon_svg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>'
    description = "Superset.sh parallel agent orchestrator"

    def config_schema(self) -> list[dict]:
        return [{"key": "project_path", "label": "Project Path", "type": "text", "placeholder": "/path/to/project", "required": True}]

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("project_path"):
            errors.append("'project_path' is required for Superset workspace")
        return errors

    def render_summary(self, config: dict) -> str:
        path = config.get("project_path", "")
        return f'Superset project at <span class="font-mono text-xs text-gray-500">{path}</span>'

    def get_config_files(self, config: dict) -> list[dict]:
        path = config.get("project_path", "")
        if not path:
            return []
        files = []
        for name, lang in [("config.json", "json"), ("setup.sh", "bash"), ("teardown.sh", "bash")]:
            f = Path(path) / ".superset" / name
            if f.is_file():
                files.append({"label": name, "path": str(f), "language": lang})
        return files

    def supports_launch(self) -> bool:
        return True

    def launch_command(self, config: dict, group_path: str) -> str | None:
        path = config.get("project_path")
        return f"superset {path}" if path else None

    def detect(self, group_path: str) -> dict | None:
        gp = Path(group_path)
        if (gp / ".superset" / "config.json").is_file():
            return {"project_path": str(gp)}
        return None


_register(SupersetWorkspace())
