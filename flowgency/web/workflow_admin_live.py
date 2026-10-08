from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import quote

from fastapi import Request

from flowgency.web.live import LiveBinding, LivePagePolicy, shared_region_macros

__all__ = [
    "is_live_read",
    "workflow_library_policy",
    "workflow_blueprint_policy",
    "workflow_blueprint_new_policy",
    "workflow_settings_policy",
    "workflow_create_policy",
]

LIBRARY_STRUCTURE = "workflow-library:1"
BLUEPRINT_STRUCTURE = "workflow-blueprint:1"
BLUEPRINT_NEW_STRUCTURE = "workflow-blueprint-new:1"
SETTINGS_STRUCTURE = "workflow-settings:1"
CREATE_STRUCTURE = "workflow-create:1"

_LIBRARY_REGIONS = {"workflow-library-blueprints": "live_workflow_library_blueprints"}
_BLUEPRINT_REGIONS = {"workflow-blueprint-source": "live_workflow_blueprint_source"}
# Editors and creation forms are controller-owned: only read-only title and revision markers ride a snapshot.
_SETTINGS_REGIONS = {
    "workflow-settings-header": "live_workflow_settings_header",
    "workflow-settings-source": "live_workflow_settings_source",
}


def is_live_read(request: Request) -> bool:
    return request.method == "GET" and request.query_params.get("__live") == "1"


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


def workflow_library_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "workflow_library.html", LiveBinding(page="workflow-library"), LIBRARY_STRUCTURE,
        _LIBRARY_REGIONS, "/admin/workflow-library?__live=1", context,
    )


def workflow_blueprint_policy(blueprint_id: str, context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "workflow_blueprint.html", LiveBinding(page="workflow-blueprint", entity=blueprint_id),
        BLUEPRINT_STRUCTURE, _BLUEPRINT_REGIONS,
        f"/admin/workflow-library/blueprints/{quote(blueprint_id, safe='')}?__live=1", context,
    )


def workflow_blueprint_new_policy(context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "workflow_blueprint.html", LiveBinding(page="workflow-blueprint-new"),
        BLUEPRINT_NEW_STRUCTURE, _BLUEPRINT_REGIONS,
        "/admin/workflow-library/blueprints/new?__live=1", context,
    )


def workflow_settings_policy(team_id: str, workflow_id: str, context: Mapping[str, Any]) -> LivePagePolicy:
    return _policy(
        "workflow_settings.html",
        LiveBinding(page="workflow-settings", team=team_id, entity=workflow_id),
        SETTINGS_STRUCTURE, _SETTINGS_REGIONS,
        f"/{quote(team_id, safe='')}/workflows/{quote(workflow_id, safe='')}/settings?__live=1", context,
    )


def workflow_create_policy(team_id: str, context: Mapping[str, Any]) -> LivePagePolicy:
    # The id a new workflow will get is generated per request, so it is never part of the binding.
    return _policy(
        "workflow_settings.html", LiveBinding(page="workflow-create", team=team_id),
        CREATE_STRUCTURE, _SETTINGS_REGIONS,
        f"/{quote(team_id, safe='')}/workflows/new?__live=1", context,
    )
