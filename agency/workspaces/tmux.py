"""tmux workspace plugin."""

from agency.workspaces import BaseWorkspace, _register


class TmuxWorkspace(BaseWorkspace):
    name = "tmux"
    display_name = "tmux"
    description = "Terminal multiplexer session layout"


_register(TmuxWorkspace())
