"""tmux workspace plugin."""

from pathlib import Path
from agency.workspaces import BaseWorkspace, _register


class TmuxWorkspace(BaseWorkspace):
    name = "tmux"
    display_name = "tmux"
    icon_svg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
    description = "Terminal multiplexer session layout"

    def config_schema(self) -> list[dict]:
        return [{"key": "script_path", "label": "Session Script", "type": "text", "placeholder": "/path/to/tmux-agents.sh", "required": True}]

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("script_path"):
            errors.append("'script_path' is required for tmux workspace")
        return errors

    def render_summary(self, config: dict) -> str:
        path = config.get("script_path", "")
        return f'<span class="font-mono text-xs text-gray-500">{path}</span>'

    def get_config_files(self, config: dict) -> list[dict]:
        path = config.get("script_path", "")
        if not path:
            return []
        return [{"label": "Session Script", "path": path, "language": "bash"}]

    def supports_launch(self) -> bool:
        return True

    def launch_command(self, config: dict, group_path: str) -> str | None:
        path = config.get("script_path")
        if not path:
            return None
        return f"bash {path}"

    def detect(self, group_path: str) -> dict | None:
        gp = Path(group_path)
        for script in sorted(gp.glob("tmux-*.sh")):
            return {"script_path": str(script)}
        tmux_sh = gp / "tmux.sh"
        if tmux_sh.is_file():
            return {"script_path": str(tmux_sh)}
        return None


_register(TmuxWorkspace())
