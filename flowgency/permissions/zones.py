"""Flowgency's own grants inside a job's launch view.

These rules are generated, never authored, and cannot be widened by
configuration: the instructions an agent runs under must not be writable by
that agent.
"""

from __future__ import annotations

from pathlib import Path

from flowgency.integrations.models import ResolvedPermissionRule

ZONE_INSTRUCTIONS = "instructions"
ZONE_OUTBOX = ".flowgency/outbox"
ZONE_MEMORY = ".flowgency/memory"


def launch_zone_rules(launch_dir: Path) -> tuple[ResolvedPermissionRule, ...]:
    launch_dir = Path(launch_dir)
    return (
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_INSTRUCTIONS.split("/")),
            tools=("read",),
            generated=True,
        ),
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_OUTBOX.split("/")),
            tools=("read", "write"),
            generated=True,
        ),
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_MEMORY.split("/")),
            tools=("read", "write"),
            generated=True,
        ),
    )
