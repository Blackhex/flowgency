"""Shared navigation shell that rides the workflow board snapshot.

The board controller owns the single passive read for a workflow page, so the shared
navigation regions travel inside that snapshot (under ``shell``) instead of a second poll.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from flowgency.web.live import (
    SHARED_NAVIGATION_TEMPLATE,
    LiveBinding,
    LivePagePolicy,
    render_live_snapshot,
    shared_region_macros,
)

WORKFLOW_SHELL_STRUCTURE = "workflow-shell:1"


def _binding(team_id: str, workflow_id: str) -> LiveBinding:
    return LiveBinding(page="workflow-board", team=team_id, entity=workflow_id)


def workflow_shell_initial(team_id: str, workflow_id: str) -> dict[str, Any]:
    """Identity the page embeds so its controller can reconcile the shell regions later."""
    return {
        "format": 1,
        "binding": _binding(team_id, workflow_id).model_dump(mode="json"),
        "structure": WORKFLOW_SHELL_STRUCTURE,
    }


def workflow_shell_snapshot(
    templates: Any, context: Mapping[str, Any], team_id: str, workflow_id: str
) -> dict[str, Any]:
    """The navigation regions rendered by the same macros as the initial page."""
    policy = LivePagePolicy(
        template_name=SHARED_NAVIGATION_TEMPLATE,
        binding=_binding(team_id, workflow_id),
        structure=WORKFLOW_SHELL_STRUCTURE,
        region_macros={},
        snapshot_url=f"/{team_id}/workflows/{workflow_id}/snapshot",
    )
    policy = replace(policy, region_macros=shared_region_macros(context, policy))
    shell_context = {**context, "active": "workflow-board", "active_workflow_id": workflow_id}
    return render_live_snapshot(templates, shell_context, policy).model_dump(mode="json")
