from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flowgency.blueprints.library import BlueprintLibrary
from flowgency.configuration import (
    ConfigConflictError,
    ConfigSnapshot,
    ConfigStore,
    FlowgencyConfig,
    ValidationFailed,
    ValidationIssue,
    parse_config,
)
from flowgency.prompts import PromptStore, effective_prompt_catalog

from .forms import RoutineForm, RoutinesDraft, build_form, serialize_routines


class RoutinesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: str = Field(min_length=1)
    draft_version: int = Field(ge=0)
    draft: RoutinesDraft


@dataclass(frozen=True)
class RoutineChoices:
    prompts: tuple[tuple[str, str], ...]
    channels: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PreparedRoutines:
    candidate: dict[str, Any]
    config: FlowgencyConfig
    form: RoutineForm
    choices: RoutineChoices


def load_choices(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    prompts: PromptStore,
    team_id: str,
    agent_id: str,
) -> RoutineChoices:
    catalog = effective_prompt_catalog(snapshot, library, prompts, team_id, agent_id)
    return RoutineChoices(
        prompts=tuple((item.scope, item.document.name) for item in catalog),
        channels=tuple(
            (key, channel.display_name)
            for key, channel in snapshot.config.memory.channels.items()
        ),
    )


def prepare_routines(
    snapshot: ConfigSnapshot,
    team_id: str,
    agent_id: str,
    request: RoutinesRequest,
    choices: RoutineChoices,
) -> PreparedRoutines:
    if request.revision != snapshot.revision:
        raise ConfigConflictError("config.yaml changed; reload before saving")

    candidate = deepcopy(snapshot.raw)
    agent = find_agent(candidate, team_id, agent_id)
    serialized = serialize_routines(agent, request.draft)
    if serialized is None:
        agent.pop("routines", None)
    else:
        agent["routines"] = serialized

    config = parse_config(candidate, snapshot.path).resolved
    _validate_prompt_choices(request.draft, choices)
    return PreparedRoutines(
        candidate=candidate,
        config=config,
        form=build_form(agent),
        choices=choices,
    )


def save_routines(
    store: ConfigStore,
    library: BlueprintLibrary | None,
    prompts: PromptStore | None,
    team_id: str,
    agent_id: str,
    request: RoutinesRequest,
) -> ConfigSnapshot:
    if library is None or prompts is None:
        raise OSError("Routine dependencies are unavailable")

    def apply(raw: dict[str, Any]) -> None:
        snapshot = ConfigSnapshot(
            store.path,
            request.revision,
            raw,
            parse_config(raw, store.path).resolved,
        )
        choices = load_choices(snapshot, library, prompts, team_id, agent_id)
        prepared = prepare_routines(snapshot, team_id, agent_id, request, choices)
        target = find_agent(raw, team_id, agent_id)
        source = find_agent(prepared.candidate, team_id, agent_id)
        if "routines" in source:
            target["routines"] = deepcopy(source["routines"])
        else:
            target.pop("routines", None)

    return store.patch(request.revision, apply)


def find_agent(raw: dict[str, Any], team_id: str, agent_id: str) -> dict[str, Any]:
    team = raw["teams"][team_id]
    for entry in team["agents"]:
        if isinstance(entry, dict) and entry.get("name") == agent_id:
            return entry
    raise KeyError(agent_id)


def _validate_prompt_choices(
    draft: RoutinesDraft,
    choices: RoutineChoices,
) -> None:
    available = set(choices.prompts)
    issues: list[ValidationIssue] = []
    for index, routine in enumerate(draft.routines):
        if (routine.prompt_scope, routine.prompt_name) in available:
            continue
        issues.append(
            ValidationIssue(
                code="invalid-routine-form",
                scope=f"routines.{index}",
                field=f"routines.{index}.prompt",
                message="Routine prompt must be selected from the effective prompt catalog.",
                corrective_hint="Choose one of the available blueprint or instance prompts.",
            )
        )
    if issues:
        raise ValidationFailed(issues)


__all__ = [
    "PreparedRoutines",
    "RoutineChoices",
    "RoutinesRequest",
    "find_agent",
    "load_choices",
    "prepare_routines",
    "save_routines",
]