"""Validation issue primitives used by configuration loaders."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    scope: str
    field: str
    message: str
    corrective_hint: str


class ValidationFailed(ValueError):
    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in issues))