"""Cursor workspace plugin."""

from agency.workspaces import BaseWorkspace, _register


class CursorWorkspace(BaseWorkspace):
    name = "cursor"
    display_name = "Cursor"
    description = "IDE workspace with rules and worktrees"


_register(CursorWorkspace())
