"""Custom workspace plugin."""

from agency.workspaces import BaseWorkspace, _register


class CustomWorkspace(BaseWorkspace):
    name = "custom"
    display_name = "Custom"
    description = "Any custom config file or workspace"


_register(CustomWorkspace())
