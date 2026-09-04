from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agency.configuration import ConfigSnapshot, resolve_team_paths
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
    default_team = str(
        agency_raw.get("default_team")
        or resolved.default_team
        or next(iter(snapshot.config.teams), "")
    )
    return {
        "title": str(agency_raw.get("title", resolved.title)),
        "default_team": default_team,
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
        "prompt_store": str(resolved.prompt_store or ""),
    }


def runtime_team(snapshot: ConfigSnapshot, team_id: str) -> dict[str, Any]:
    team = snapshot.config.teams[team_id]
    paths = resolve_team_paths(team)
    job_store = JobStore(snapshot.config.agency.memory_store)
    agents_full = [
        instance.model_dump(mode="json") for instance in team.agents.values()
    ]
    return {
        "key": team_id,
        "name": team.name,
        "workspace_root": paths.workspace_root,
        "team_root": paths.team_root,
        "observations": paths.observations,
        "proposals": paths.proposals,
        "decisions": paths.decisions,
        "locks": paths.locks,
        "logs": paths.logs,
        "job_paths": job_store.paths(team_id),
        "dispatch_interval": snapshot.config.agency.dispatch.interval,
        "agents": list(team.agents.keys()),
        "agents_full": agents_full,
        "dispatch": team.dispatch.model_dump(mode="json"),
        "runtime": team.runtime.model_dump(mode="json"),
        "workspaces": [
            workspace.model_dump(mode="json")
            for workspace in team.workspaces
        ],
    }


def all_runtime_teams(snapshot: ConfigSnapshot) -> dict[str, dict[str, Any]]:
    return {
        team_id: runtime_team(snapshot, team_id)
        for team_id in snapshot.config.teams
    }