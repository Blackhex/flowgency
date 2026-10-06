"""Closed completion contract for explicit setup-completion decisions.

Configuration readiness alone never proves setup is finished; these types
require an explicit, strictly-typed assertion that setup work and scheduler
disposition are both closed before a completion decision can be formed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

from flowgency.blueprints import BlueprintLibrary
from flowgency.configuration import ConfigStore, ValidationFailed
from flowgency.configuration.paths import validate_resolved_paths
from flowgency.integrations import REGISTRY
from flowgency.prompts import validate_prompt_catalogs
from flowgency.prompts.store import ReadOnlyPromptStore
from flowgency.web.dependencies import FlowgencyServices
from flowgency.web.validation import collect_validation_issues

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


def completion_environment(launch: SetupCompletionLaunch) -> dict[str, str]:
    return {
        "FLOWGENCY_SETUP_ORIGIN": launch.origin,
        "FLOWGENCY_SETUP_TOKEN": launch.token,
        "FLOWGENCY_SETUP_LAUNCH_ID": launch.launch_id,
    }


class SetupCompletionStale(Exception):
    """The configuration revision no longer matches the one the agent reported."""


class SetupCompletionUnavailable(Exception):
    """The configuration could not be shown ready; carries only a fixed code."""

    def __init__(self, code: Literal["not-ready", "unavailable"]) -> None:
        self.code = code
        super().__init__(code)


_UNREADABLE_CONFIG = (FileNotFoundError, ValidationFailed, yaml.YAMLError, TypeError, ValueError)


def _require_ready(config_store: ConfigStore, snapshot) -> None:
    config = snapshot.config
    if not config.teams or validate_resolved_paths(config):
        raise SetupCompletionUnavailable("not-ready")
    flowgency = config.flowgency
    library = BlueprintLibrary(Path(flowgency.agent_library))
    services = FlowgencyServices(
        config_path=snapshot.path,
        config_store=config_store,
        blueprint_library=library,
        compilation_cache=None,
        memory_store=None,
        prompt_store=None,
        job_store=None,
        instances=None,
        integrations=REGISTRY,
        prompt_issues=validate_prompt_catalogs(
            snapshot, library, ReadOnlyPromptStore(Path(flowgency.prompt_store))
        ),
    )
    if collect_validation_issues(services, snapshot):
        raise SetupCompletionUnavailable("not-ready")


def validate_current_completion(config_path: Path, expected_revision: str) -> str:
    """Re-check readiness without creating or repairing any source; return the revision."""
    store = ConfigStore(config_path)
    try:
        snapshot = store.load()
    except _UNREADABLE_CONFIG:
        raise SetupCompletionUnavailable("not-ready") from None
    except Exception:
        raise SetupCompletionUnavailable("unavailable") from None
    if snapshot.revision != expected_revision:
        raise SetupCompletionStale()
    try:
        _require_ready(store, snapshot)
        current = store.inspect()
    except SetupCompletionUnavailable:
        raise
    except ValidationFailed:
        raise SetupCompletionUnavailable("not-ready") from None
    except Exception:
        raise SetupCompletionUnavailable("unavailable") from None
    if not current.exists or current.revision != expected_revision:
        raise SetupCompletionStale()
    return current.revision
