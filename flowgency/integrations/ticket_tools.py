from __future__ import annotations

import json
import sys
from pathlib import Path

from flowgency.fs.atomic import atomic_write_text
from flowgency.integrations.models import TicketToolLaunch
from flowgency.tickets.models import LiveTicketEndpoint


def build_ticket_tool_launch(endpoint: LiveTicketEndpoint) -> TicketToolLaunch:
    return TicketToolLaunch(
        command=sys.executable,
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