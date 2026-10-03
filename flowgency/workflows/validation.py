from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from flowgency.configuration import ConfigSnapshot
from flowgency.configuration.issues import ValidationIssue
from flowgency.workflows.library import WorkflowLibrary
from flowgency.workflows.models import ContractError


_CONTRACT_DESCRIPTIONS: Dict[str, str] = {
    "missing-blueprint": "the definition is missing",
    "unsafe-blueprint": "the definition path is unsafe",
    "corrupt-blueprint": "the definition is corrupt",
    "source-too-large": "the definition exceeds the allowed size",
}


def validate_workflow_references(snapshot: ConfigSnapshot) -> tuple[ValidationIssue, ...]:
    root = snapshot.config.flowgency.workflow_library
    library = WorkflowLibrary(Path(root)) if root is not None else None
    failures: dict[str, Tuple[str, str] | None] = {}
    issues: List[ValidationIssue] = []

    for team_id, team in sorted(snapshot.config.teams.items()):
        for workflow_id, workflow in sorted(team.workflows.items()):
            blueprint_id = workflow.blueprint
            if blueprint_id not in failures:
                if library is None:
                    failure = (
                        "missing-workflow-library",
                        "the workflow library is not configured",
                    )
                else:
                    try:
                        definition = library.inspect(blueprint_id).definition
                        failure = (
                            (
                                "workflow-identity-mismatch",
                                "the definition identity does not match its reference",
                            )
                            if definition.id != blueprint_id
                            else None
                        )
                    except ContractError as error:
                        code = getattr(error, "code", None) or str(error)
                        # Map a few well-known contract codes to safe messages.
                        reason = _CONTRACT_DESCRIPTIONS.get(code, "the definition is missing or unsafe")
                        failure = (code, reason)
                    except (yaml.YAMLError, ValueError):
                        failure = (
                            "invalid-workflow-definition",
                            "the definition is invalid",
                        )
                    except OSError:
                        failure = (
                            "unreadable-workflow-definition",
                            "the definition cannot be read",
                        )
                failures[blueprint_id] = failure

            failure = failures[blueprint_id]
            if failure is None:
                continue
            code, reason = failure
            issues.append(
                ValidationIssue(
                    code=code,
                    scope=f"teams.{team_id}.workflows.{workflow_id}",
                    field="blueprint",
                    message=(
                        f"Workflow '{getattr(workflow, 'name', blueprint_id)}' ({team_id}/{workflow_id}) "
                        f"cannot use blueprint '{blueprint_id}': {reason}."
                    ),
                    corrective_hint=(
                        "Configure the approved workflow library and create or "
                        "correct this blueprint definition before completing setup."
                    ),
                )
            )

    return tuple(issues)
