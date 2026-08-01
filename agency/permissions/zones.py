"""Agency's own grants inside a job's launch view.

These rules are generated, never authored, and cannot be widened by
configuration: the instructions an agent runs under must not be writable by
that agent.
"""

from __future__ import annotations

from pathlib import Path

from agency.integrations.models import ResolvedPermissionRule

ZONE_INSTRUCTIONS = "instructions"
ZONE_OUTBOX = ".agency/outbox"
ZONE_MEMORY = ".agency/memory"


def launch_zone_rules(launch_dir: Path) -> tuple[ResolvedPermissionRule, ...]:
    launch_dir = Path(launch_dir)
    return (
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_INSTRUCTIONS.split("/")),
            tools=("read",),
        ),
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_OUTBOX.split("/")),
            tools=("read", "write"),
        ),
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_MEMORY.split("/")),
            tools=("read", "write"),
        ),
    )
