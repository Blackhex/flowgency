"""Chat workspace plugin."""

from agency.workspaces import BaseWorkspace, _register


class ChatWorkspace(BaseWorkspace):
    name = "chat"
    display_name = "Chat"
    description = "Chat platform (Slack, Mattermost, Discord)"


_register(ChatWorkspace())
