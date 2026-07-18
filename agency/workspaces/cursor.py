"""Cursor IDE workspace plugin."""

from pathlib import Path
from agency.workspaces import BaseWorkspace, _register


class CursorWorkspace(BaseWorkspace):
    name = "cursor"
    display_name = "Cursor"
    icon_svg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>'
    description = "Cursor IDE with rules and parallel agents"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "project_path", "label": "Project Path", "type": "text", "placeholder": "/path/to/project", "required": True},
            {"key": "notes", "label": "Notes", "type": "textarea", "placeholder": "Which rules files, worktree setup, agent layout...", "required": False},
        ]

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("project_path"):
            errors.append("'project_path' is required for Cursor workspace")
        return errors

    def render_summary(self, config: dict) -> str:
        path = config.get("project_path", "")
        return f'Cursor project at <span class="font-mono text-xs text-gray-500">{path}</span>'

    def get_config_files(self, config: dict) -> list[dict]:
        path = config.get("project_path", "")
        if not path:
            return []
        files = []
        cursor_rules = Path(path) / ".cursor" / "rules"
        if cursor_rules.is_dir():
            for mdc in sorted(cursor_rules.glob("*.mdc")):
                files.append({"label": mdc.name, "path": str(mdc), "language": "markdown"})
        root_rules_file = Path(path) / ".cursorrules"
        if root_rules_file.is_file():
            files.append({"label": ".cursorrules (root rules file)", "path": str(root_rules_file), "language": "markdown"})
        worktrees = Path(path) / ".cursor" / "worktrees.json"
        if worktrees.is_file():
            files.append({"label": "worktrees.json", "path": str(worktrees), "language": "json"})
        return files

    def supports_launch(self) -> bool:
        return True

    def launch_command(self, config: dict, group_path: str) -> str | None:
        path = config.get("project_path")
        return f"cursor {path}" if path else None

    def detect(self, group_path: str) -> dict | None:
        gp = Path(group_path)
        if (gp / ".cursor" / "rules").is_dir():
            return {"project_path": str(gp)}
        if (gp / ".cursorrules").is_file():
            return {"project_path": str(gp)}
        return None


_register(CursorWorkspace())
