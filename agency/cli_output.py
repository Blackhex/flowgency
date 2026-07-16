from __future__ import annotations

from dataclasses import asdict
from enum import IntEnum
import json
import sys
from typing import TextIO

from agency.configuration import ValidationFailed, ValidationIssue
from agency.fs.locks import LockCancelledError, ResourceBusyError


class ExitCode(IntEnum):
    SUCCESS = 0
    OPERATIONAL_FAILURE = 1
    USAGE = 2
    VALIDATION = 3
    RESOURCE_BUSY = 4


def exit_code_for(error: BaseException) -> ExitCode:
    if isinstance(error, (ResourceBusyError, LockCancelledError)):
        return ExitCode.RESOURCE_BUSY
    if isinstance(error, ValidationFailed):
        return ExitCode.VALIDATION
    return ExitCode.OPERATIONAL_FAILURE


def issue_payload(issue: ValidationIssue) -> dict[str, str]:
    return asdict(issue)


def render_error(
    *,
    code: str,
    message: str,
    issues: tuple[ValidationIssue, ...] = (),
    json_output: bool = False,
    stream: TextIO | None = None,
) -> None:
    target = stream or sys.stderr
    if json_output:
        print(
            json.dumps(
                {
                    "code": code,
                    "message": message,
                    "issues": [issue_payload(issue) for issue in issues],
                },
                sort_keys=True,
            ),
            file=target,
        )
        return
    print(f"Error: {message}", file=target)
    for issue in issues:
        location = ".".join(part for part in (issue.scope, issue.field) if part)
        prefix = f"{location}: " if location else ""
        print(f"  {prefix}{issue.message}", file=target)
        if issue.corrective_hint:
            print(f"  Hint: {issue.corrective_hint}", file=target)


__all__ = [
    "ExitCode",
    "exit_code_for",
    "issue_payload",
    "render_error",
]