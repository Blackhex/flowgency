"""Superset workspace plugin."""

from agency.workspaces import BaseWorkspace, _register


class SupersetWorkspace(BaseWorkspace):
    name = "superset"
    display_name = "Superset"
    description = "Parallel agent orchestrator"


_register(SupersetWorkspace())
