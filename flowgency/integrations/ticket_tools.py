from __future__ import annotations

import json
import sys
from pathlib import Path

from flowgency.fs.atomic import atomic_write_text
from flowgency.integrations.models import TicketToolLaunch
from flowgency.tickets.models import LiveTicketEndpoint


def _ticket_tool_command() -> str:
    """The interpreter that launches the ticket MCP server.

    An MCP host spawns this `command` through a shell (Copilot wraps it in a
    PowerShell `& '...'` invocation). A Microsoft Store Python's native image
    lives under `C:\\Program Files\\WindowsApps`, whose execution alias a shell
    refuses to run ("Access is denied"), so the server never starts and no ticket
    tools appear. `sys.executable` is the venv launcher a shell can run and that
    still resolves this project's environment. Its stdio transport dies with the
    host's pipe, so the server is still bounded by the supervised host.
    """
    return sys.executable


def build_ticket_tool_launch(endpoint: LiveTicketEndpoint) -> TicketToolLaunch:
    return TicketToolLaunch(
        command=_ticket_tool_command(),
        args=("-m", "flowgency.tickets.mcp_server"),
        env={
            "FLOWGENCY_TICKET_ENDPOINT": endpoint.url,
            "FLOWGENCY_TICKET_TOKEN": endpoint.grant.token,
        },
    )


def write_copilot_ticket_config(launch: TicketToolLaunch, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcpServers": {
            launch.server_name: {
                "command": launch.command,
                "args": list(launch.args),
                "env": dict(launch.env),
                "tools": ["*"],
            },
        },
    }
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
    return path