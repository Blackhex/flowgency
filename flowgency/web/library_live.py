from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from flowgency.web.live import LiveBinding, LivePagePolicy, shared_region_macros

__all__ = [
    "library_list_policy",
    "blueprint_detail_policy",
    "blueprint_skill_policy",
    "blueprint_prompts_policy",
    "channel_list_policy",
    "channel_detail_policy",
]

LIBRARY_LIST_STRUCTURE = "agent-library:1"
BLUEPRINT_DETAIL_STRUCTURE = "blueprint-detail:1"
BLUEPRINT_SKILL_STRUCTURE = "blueprint-skill:1"
BLUEPRINT_PROMPTS_STRUCTURE = "blueprint-prompts:1"
CHANNEL_LIST_STRUCTURE = "memory-channels:1"
CHANNEL_DETAIL_STRUCTURE = "memory-channel:1"

_LIBRARY_LIST_REGIONS = {"library-blueprints": "live_library_blueprints"}
_BLUEPRINT_DETAIL_REGIONS = {
    "blueprint-header": "live_blueprint_header",
    "blueprint-files": "live_blueprint_files",
    "blueprint-users": "live_blueprint_users",
    "blueprint-runtimes": "live_blueprint_runtimes",
}
_BLUEPRINT_SKILL_REGIONS = {
    "skill-header": "live_skill_header",
    "skill-files": "live_skill_files",
    "skill-source": "live_skill_source",
}
_BLUEPRINT_PROMPTS_REGIONS = {
    "prompts-header": "live_prompts_header",
    "prompts-list": "live_prompts_list",
}
_CHANNEL_LIST_REGIONS = {
    "channels-status": "live_channels_status",
    "channels-table": "live_channels_table",
}
_CHANNEL_DETAIL_REGIONS = {
    "channel-header": "live_channel_header",
    "channel-status": "live_channel_status",
    "channel-references": "live_channel_references",
    "channel-files": "live_channel_files",
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


def _selection_query(selected_path: str | None) -> dict[str, str]:
    return {"path": selected_path} if selected_path else {}


def _snapshot_url(base: str, query: Mapping[str, str]) -> str:
    return f"{base}?{urlencode({**query, '__live': '1'})}"


def library_list_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_agent_library.html", LiveBinding(page="agent-library"), LIBRARY_LIST_STRUCTURE,
        _LIBRARY_LIST_REGIONS, "/admin/agent-library?__live=1", context,
    )


def blueprint_detail_policy(key: str, context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_blueprint_detail.html", LiveBinding(page="blueprint-detail", entity=key),
        BLUEPRINT_DETAIL_STRUCTURE, _BLUEPRINT_DETAIL_REGIONS,
        f"/admin/agent-library/blueprints/{quote(key, safe='')}?__live=1", context,
    )


def blueprint_skill_policy(
    key: str, skill: str, selected_path: str, context: Mapping[str, Any]
) -> LivePagePolicy:
    # The resolved skill and file, not the requested URL: a stale selection never reads as another record.
    query = _selection_query(selected_path)
    return _policy(
        "admin_blueprint_skill.html",
        LiveBinding(page="blueprint-skill", entity=key, tab=skill, query=query),
        BLUEPRINT_SKILL_STRUCTURE, _BLUEPRINT_SKILL_REGIONS,
        _snapshot_url(f"/admin/agent-library/blueprints/{quote(key, safe='')}/skills/{quote(skill, safe='')}", query),
        context,
    )


def blueprint_prompts_policy(
    key: str, selected_path: str | None, context: Mapping[str, Any]
) -> LivePagePolicy:
    query = _selection_query(selected_path)
    return _policy(
        "admin_blueprint_prompts.html",
        LiveBinding(page="blueprint-prompts", entity=key, query=query),
        BLUEPRINT_PROMPTS_STRUCTURE, _BLUEPRINT_PROMPTS_REGIONS,
        _snapshot_url(f"/admin/agent-library/blueprints/{quote(key, safe='')}/prompts", query),
        context,
    )


def channel_list_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_memory_channels.html", LiveBinding(page="memory-channels"), CHANNEL_LIST_STRUCTURE,
        _CHANNEL_LIST_REGIONS, "/admin/memory-channels?__live=1", context,
    )


def channel_detail_policy(channel_key: str, context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "admin_memory_channel.html", LiveBinding(page="memory-channel", entity=channel_key),
        CHANNEL_DETAIL_STRUCTURE, _CHANNEL_DETAIL_REGIONS,
        f"/admin/memory-channels/{quote(channel_key, safe='')}?__live=1", context,
    )
