"""Canonical workflow-instance configuration: patches and binding resolution.

Workflow instances are named entries in the canonical control plane. This module
mutates only the addressed workflow through the shared revision-checked config
patch, and resolves a stored instance into a :class:`WorkflowBinding` whose
physical identity (``binding_id``) is independent of the display name, while its
``context_digest`` tracks the blueprint selection, workflow-library root, and the
internal ``context_generation`` fence.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flowgency.configuration.store import ConfigSnapshot, ConfigStore
from flowgency.tickets.models import StorageBinding


@dataclass(frozen=True)
class WorkflowInstancePatch:
    name: str
    blueprint: str
    integration: str
    integration_config: dict[str, Any]


@dataclass(frozen=True)
class WorkflowBinding:
    team_id: str
    workflow_id: str
    blueprint_id: str
    storage: StorageBinding
    context_digest: str


def _normalized_provider_config(integration: str, config: Any) -> Any:
    """Return a spelling-insensitive view of provider settings for comparison.

    Equivalent path spellings must compare equal so a no-op re-save does not look
    like a selection change; normalization here is cwd-independent so it cannot
    silently absorb a real move.
    """
    if not isinstance(config, dict):
        return config
    normalized = dict(config)
    if integration == "local" and normalized.get("root") is not None:
        normalized["root"] = os.path.normcase(
            os.path.normpath(str(normalized["root"]))
        )
    return normalized


def patch_workflow_instance(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    workflow_id: str,
    patch: WorkflowInstancePatch,
    *,
    create: bool = False,
) -> ConfigSnapshot:
    """Create or update a single workflow instance under a team.

    Only the addressed workflow is touched; sibling workflows, agents, and every
    other team field are preserved by the shared config patch. A change to the
    blueprint or provider selection advances ``context_generation``; a rename
    does not.
    """

    def apply(raw: dict[str, Any]) -> None:
        teams = raw.get("teams")
        if not isinstance(teams, dict):
            raise TypeError("teams must be a mapping")
        team = teams[team_id]
        if not isinstance(team, dict):
            raise TypeError(f"teams.{team_id} must be a mapping")
        workflows = team.setdefault("workflows", {})
        if not isinstance(workflows, dict):
            raise TypeError(f"teams.{team_id}.workflows must be a mapping")
        if create and workflow_id in workflows:
            raise ValueError(f"Workflow already exists: {workflow_id}")
        if not create and workflow_id not in workflows:
            raise KeyError(workflow_id)
        current = workflows.setdefault(workflow_id, {})
        previous_selection = (
            current.get("blueprint"),
            current.get("integration"),
            _normalized_provider_config(
                current.get("integration"), current.get("integration_config", {})
            ),
        )
        next_selection = (
            patch.blueprint,
            patch.integration,
            _normalized_provider_config(patch.integration, patch.integration_config),
        )
        generation = int(current.get("context_generation", 0))
        if not create and previous_selection != next_selection:
            generation += 1
        current.update(
            {
                "name": patch.name,
                "blueprint": patch.blueprint,
                "integration": patch.integration,
                "integration_config": dict(patch.integration_config),
                "context_generation": generation,
            }
        )

    return store.patch(expected_revision, apply)


def _canonical_library(workflow_library: Path | None) -> str | None:
    if workflow_library is None:
        return None
    return os.path.normcase(str(Path(workflow_library).resolve(strict=False)))


def _context_digest(
    *,
    storage: StorageBinding,
    blueprint_id: str,
    workflow_library: Path | None,
    context_generation: int,
) -> str:
    payload = json.dumps(
        {
            "binding_id": storage.binding_id,
            "blueprint_id": blueprint_id,
            "workflow_library": _canonical_library(workflow_library),
            "context_generation": context_generation,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_workflow_binding(
    snapshot: ConfigSnapshot, team_id: str, workflow_id: str
) -> WorkflowBinding:
    """Resolve a stored workflow instance into its physical and context identity."""
    team = snapshot.config.teams[team_id]
    workflow = team.workflows[workflow_id]
    storage = StorageBinding(
        integration=workflow.integration,
        config=dict(workflow.integration_config),
        team_id=team_id,
        workflow_id=workflow_id,
    )
    context_digest = _context_digest(
        storage=storage,
        blueprint_id=workflow.blueprint,
        workflow_library=snapshot.config.flowgency.workflow_library,
        context_generation=workflow.context_generation,
    )
    return WorkflowBinding(
        team_id=team_id,
        workflow_id=workflow_id,
        blueprint_id=workflow.blueprint,
        storage=storage,
        context_digest=context_digest,
    )
