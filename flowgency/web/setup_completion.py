"""Closed completion contract for explicit setup-completion decisions.

Configuration readiness alone never proves setup is finished; these types
require an explicit, strictly-typed assertion that setup work and scheduler
disposition are both closed before a completion decision can be formed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

SchedulerResult = Literal[
    "manual-only", "inactive", "declined", "confirmed", "failed", "unknown"
]

_LAUNCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEDULER_RESULTS_REQUIRING_ACKNOWLEDGEMENT = {"failed", "unknown"}


class SetupCompletionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch_id: StrictStr
    revision: StrictStr
    scheduler_result: SchedulerResult
    all_questions_answered: StrictBool
    summary_delivered: StrictBool
    limitations_acknowledged: StrictBool = False

    @model_validator(mode="after")
    def validate_identifiers(self) -> "SetupCompletionCommand":
        if not _LAUNCH_ID_RE.fullmatch(self.launch_id):
            raise ValueError("launch_id must be a 32-character lowercase hex UUID value")
        if not _REVISION_RE.fullmatch(self.revision):
            raise ValueError("revision must be a 64-character lowercase SHA-256 hex value")
        return self

    @model_validator(mode="after")
    def require_finished_work(self) -> "SetupCompletionCommand":
        if self.all_questions_answered is not True or self.summary_delivered is not True:
            raise ValueError("Setup work is not complete")
        if (
            self.scheduler_result in _SCHEDULER_RESULTS_REQUIRING_ACKNOWLEDGEMENT
            and not self.limitations_acknowledged
        ):
            raise ValueError("Scheduler limitation requires acknowledgement")
        return self


@dataclass(frozen=True)
class SetupCompletionLaunch:
    launch_id: str
    origin: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class SetupCompletionDecision:
    launch_id: str | None
    phase: Literal["pending", "acknowledged", "complete", "attention", "cancelled"]
    redirect_allowed: bool
    message: str
