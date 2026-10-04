from __future__ import annotations

import json
from pathlib import Path

from flowgency.fs.atomic import atomic_write_text
from flowgency.integrations.models import TicketToolLaunch
from flowgency.tickets.models import LiveTicketEndpoint


def build_ticket_tool_launch(endpoint: LiveTicketEndpoint) -> TicketToolLaunch:
    return TicketToolLaunch(
        url=f"{endpoint.url}/mcp",
        headers={"Authorization": f"Bearer {endpoint.grant.token}"},
    )


def write_copilot_ticket_config(launch: TicketToolLaunch, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcpServers": {
            launch.server_name: {
                "type": "http",
                "url": launch.url,
                "headers": dict(launch.headers),
                "tools": ["*"],
            },
        },
    }
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
    return path
