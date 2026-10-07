from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import quote

from flowgency.configuration import resolve_team_paths
from flowgency.web.live import LiveBinding, LivePagePolicy, shared_region_macros

__all__ = [
    "team_summary",
    "settings_policy",
    "integrations_policy",
    "dispatch_policy",
    "teams_policy",
    "team_edit_policy",
    "team_new_policy",
]

SETTINGS_STRUCTURE = "admin-settings:1"
INTEGRATIONS_STRUCTURE = "admin-integrations:1"
DISPATCH_STRUCTURE = "admin-dispatch:1"
TEAMS_STRUCTURE = "admin-teams:1"
TEAM_EDIT_STRUCTURE = "admin-team-edit:1"
TEAM_NEW_STRUCTURE = "admin-team-new:1"

_SETTINGS_REGIONS = {"settings-status": "live_settings_status"}
_INTEGRATIONS_REGIONS = {
    "integrations-status": "live_integrations_status",
    "integrations-installed": "live_integrations_installed",
    "integrations-available": "live_integrations_available",
}
_DISPATCH_REGIONS = {
    "dispatch-status": "live_dispatch_status",
    "dispatch-action": "live_dispatch_action",
    "dispatch-teams": "live_dispatch_teams",
}
_TEAMS_REGIONS = {"teams-status": "live_teams_status", "teams-list": "live_teams_list"}
_TEAM_EDIT_REGIONS = {
    "team-edit-status": "live_team_edit_status",
    "team-edit-agents": "live_team_edit_agents",
}
_TEAM_NEW_REGIONS = {"team-new-status": "live_team_new_status"}


def team_summary(key: str, team_config: Any) -> dict[str, Any]:
    """One team's read-only listing row, shared by the page, its snapshot and error renders."""
    paths = resolve_team_paths(team_config)
    return {
        "key": key,
        "name": team_config.name,
        "workspace_path": str(team_config.workspace_path),
        "team_path": str(team_config.path),
        "agents": list(team_config.agents.keys()),
        "agent_count": len(team_config.agents),
        "initialized": all(path.is_dir() for path in paths.runtime_directories),
        "workspace_exists": paths.workspace_root.exists(),
        "dispatch_enabled": team_config.dispatch.enabled,
    }


def _policy(
    template_name: str,
    binding: LiveBinding,
    structure: str,
    region_macros: Mapping[str, str],
    snapshot_url: str,
    context: Mapping[str, Any],
) -> LivePagePolicy:
    policy = LivePagePolicy(
        template_name=template_name,
        binding=binding,
        structure=structure,
        region_macros=region_macros,
        snapshot_url=snapshot_url,
    )
    return replace(policy, region_macros=shared_region_macros(context, policy))


def settings_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_settings.html", LiveBinding(page="admin-settings"), SETTINGS_STRUCTURE,
        _SETTINGS_REGIONS, "/admin/?__live=1", context,
    )


def integrations_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    # The restart banner is local action output, never part of the polled read.
    return _policy(
        "admin_integrations.html", LiveBinding(page="admin-integrations"), INTEGRATIONS_STRUCTURE,
        _INTEGRATIONS_REGIONS, "/admin/integrations?__live=1", context,
    )


def dispatch_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_dispatch.html", LiveBinding(page="admin-dispatch"), DISPATCH_STRUCTURE,
        _DISPATCH_REGIONS, "/admin/dispatch?__live=1", context,
    )


def teams_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_teams.html", LiveBinding(page="admin-teams"), TEAMS_STRUCTURE,
        _TEAMS_REGIONS, "/admin/teams?__live=1", context,
    )


def team_edit_policy(team: str, context: Mapping[str, Any]) -> LivePagePolicy:
    # Always the canonical GET of the team being edited, even when a POST rendered the page.
    return _policy(
        "admin_team_edit.html", LiveBinding(page="admin-team-edit", entity=team), TEAM_EDIT_STRUCTURE,
        _TEAM_EDIT_REGIONS, f"/admin/teams/{quote(team, safe='')}/edit?__live=1", context,
    )


def team_new_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_team_edit.html", LiveBinding(page="admin-team-new"), TEAM_NEW_STRUCTURE,
        _TEAM_NEW_REGIONS, "/admin/teams/new?__live=1", context,
    )
