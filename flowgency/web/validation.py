"""Shared validation-issue collection used by the CLI and setup completion."""

from __future__ import annotations

from flowgency.configuration import ConfigSnapshot, ValidationFailed
from flowgency.configuration.issues import ValidationIssue
from flowgency.web.dependencies import FlowgencyServices
from flowgency.workflows.validation import validate_workflow_references


def collect_validation_issues(
    services: FlowgencyServices, snapshot: ConfigSnapshot
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = list(services.prompt_issues)
    seen: set[tuple[str, str, str]] = {(i.code, i.field, i.message) for i in issues}

    if services.blueprint_library is not None:
        try:
            services.blueprint_library.list()
        except ValidationFailed as exc:
            for issue in exc.issues:
                key = (issue.code, issue.field, issue.message)
                if key not in seen:
                    seen.add(key)
                    issues.append(issue)

    for issue in validate_workflow_references(snapshot):
        key = (issue.code, issue.field, issue.message)
        if key not in seen:
            seen.add(key)
            issues.append(issue)

    return tuple(issues)
