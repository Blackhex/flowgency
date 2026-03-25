"""IDE workspace plugin."""

from agency.workspaces import BaseWorkspace, _register


class IdeWorkspace(BaseWorkspace):
    name = "ide"
    display_name = "IDE"
    description = "Generic IDE workspace (VS Code, Windsurf, JetBrains)"


_register(IdeWorkspace())
