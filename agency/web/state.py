from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agency.configuration import ConfigSnapshot
from agency.jobs.authority import JobStore


def agency_settings(snapshot: ConfigSnapshot) -> dict[str, Any]:
    agency_raw = snapshot.raw.get("agency")
    if not isinstance(agency_raw, Mapping):
        agency_raw = {}
    dispatch_raw = agency_raw.get("dispatch")
    if not isinstance(dispatch_raw, Mapping):
        dispatch_raw = {}

    dismissed = agency_raw.get("tips_dismissed")
    if not isinstance(dismissed, list):
        dismissed = []

    resolved = snapshot.config.agency
    default_group = str(
        agency_raw.get("default_group")
        or resolved.default_group
        or next(iter(snapshot.config.groups), "")
    )
    return {
        "title": str(agency_raw.get("title", resolved.title)),
        "default_group": default_group,
        "decided_by": str(agency_raw.get("decided_by", "admin")),
        "ai_backend": str(agency_raw.get("ai_backend", resolved.ai_backend)),
        "theme": str(agency_raw.get("theme", "")),
        "dispatch_interval": int(
            dispatch_raw.get("interval", resolved.dispatch.interval)
        ),
        "show_tips": agency_raw.get("show_tips", True) is not False,
        "tips_dismissed": [str(item) for item in dismissed if str(item)],
        "agent_library": str(resolved.agent_library or ""),
        "compilation_cache": str(resolved.compilation_cache or ""),
        "memory_store": str(resolved.memory_store or ""),
    }


def runtime_group(snapshot: ConfigSnapshot, group_id: str) -> dict[str, Any]:
    group = snapshot.config.groups[group_id]
    job_store = JobStore(snapshot.config.agency.memory_store)
    agents_full = [
        instance.model_dump(mode="json") for instance in group.agents.values()
    ]
    return {
        "key": group_id,
        "name": group.name,
        "path": Path(group.path),
        "shared": Path(group.path) / "shared",
        "job_paths": job_store.paths(group_id),
        "agents": list(group.agents.keys()),
        "agents_full": agents_full,
        "dispatch": group.dispatch.model_dump(mode="json"),
        "runtime": group.runtime.model_dump(mode="json"),
        "workspaces": [
            workspace.model_dump(mode="json")
            for workspace in group.workspaces
        ],
    }


def all_runtime_groups(snapshot: ConfigSnapshot) -> dict[str, dict[str, Any]]:
    return {
        group_id: runtime_group(snapshot, group_id)
        for group_id in snapshot.config.groups
    }