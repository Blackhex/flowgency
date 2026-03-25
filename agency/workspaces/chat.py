"""Chat platform workspace plugin (Slack, Mattermost, Discord, Teams)."""

from agency.workspaces import BaseWorkspace, _register


class ChatWorkspace(BaseWorkspace):
    name = "chat"
    display_name = "Chat"
    icon_svg = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>'
    description = "Chat platform (Slack, Mattermost, Discord, Teams)"

    def config_schema(self) -> list[dict]:
        return [
            {"key": "platform", "label": "Platform", "type": "select", "options": ["Slack", "Mattermost", "Discord", "Microsoft Teams", "Other"], "required": True},
            {"key": "channel_url", "label": "Channel URL or Name", "type": "text", "placeholder": "https://team.slack.com/channels/agents or #agents", "required": True},
            {"key": "notes", "label": "Notes", "type": "textarea", "placeholder": "Channel mapping, bot setup, webhook URLs...", "required": False},
        ]

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("platform"):
            errors.append("'platform' is required for chat workspace")
        if not config.get("channel_url"):
            errors.append("'channel_url' is required for chat workspace")
        return errors

    def render_summary(self, config: dict) -> str:
        platform = config.get("platform", "Chat")
        channel = config.get("channel_url", "")
        return f'{platform}: <span class="font-mono text-xs text-gray-500">{channel}</span>'


_register(ChatWorkspace())
