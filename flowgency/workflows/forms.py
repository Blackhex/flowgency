from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr

from pathlib import Path

from flowgency.workflows.library import WorkflowSnapshot
from flowgency.workflows.editing import new_definition
from flowgency.workflows.models import (
    AgentCriterion,
    FieldDefinition,
    FieldKind,
    FieldUse,
    Precondition,
    StateDefinition,
    TransitionDefinition,
    WorkflowDefinition,
)

DraftScalar = StrictBool | StrictInt | StrictFloat | StrictStr | None


def _draft_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class DraftField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    existing_field_id: str | None = None
    label: str
    type: FieldKind


class DraftFieldUse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    existing_field_id: str | None = None
    draft_field_key: str | None = None
    required: StrictBool


class DraftState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    existing_state_id: str | None = None
    name: str
    color: str
    initial: StrictBool = False


class DraftPrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    existing_field_id: str | None = None
    draft_field_key: str | None = None
    operator: Literal["equals", "not_equals", "is_present"]
    value: DraftScalar = None


class DraftCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    existing_criterion_id: str | None = None
    description: str


class DraftTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    existing_transition_id: str | None = None
    name: str
    from_state_id: str
    to_state_id: str
    inputs: tuple[DraftFieldUse, ...]
    outputs: tuple[DraftFieldUse, ...]
    preconditions: tuple[DraftPrecondition, ...]
    criteria: tuple[DraftCriterion, ...]


class WorkflowEditorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str
    name: str
    description: str
    states: tuple[DraftState, ...]
    fields: tuple[DraftField, ...]
    transitions: tuple[DraftTransition, ...]


def new_editor_snapshot(name: str = "New workflow") -> WorkflowSnapshot:
    definition = new_definition(name).definition
    return WorkflowSnapshot(
        definition=definition,
        digest="",
        source_path=Path("workflow.yaml"),
    )


def editor_payload(snapshot: WorkflowSnapshot) -> dict[str, object]:
    definition = snapshot.definition
    initial_state = definition.initial_state
    criteria_keys: dict[tuple[str, str], str] = {}
    for transition in definition.transitions:
        for criterion in transition.criteria:
            criteria_keys[(transition.id, criterion.id)] = _draft_key("dc")
    payload = WorkflowEditorDraft(
        id=definition.id,
        name=definition.name,
        description=definition.description,
        states=tuple(
            DraftState(
                key=_draft_key("ds"),
                existing_state_id=state.id,
                name=state.name,
                color=state.color,
                initial=state.id == initial_state,
            )
            for state in definition.states
        ),
        fields=tuple(
            DraftField(
                key=_draft_key("df"),
                existing_field_id=field.id,
                label=field.label,
                type=field.type,
            )
            for field in definition.fields
        ),
        transitions=tuple(
            DraftTransition(
                key=_draft_key("dt"),
                existing_transition_id=transition.id,
                name=transition.name,
                from_state_id=transition.from_state,
                to_state_id=transition.to_state,
                inputs=tuple(
                    DraftFieldUse(existing_field_id=field_use.field_id, required=field_use.required)
                    for field_use in transition.inputs
                ),
                outputs=tuple(
                    DraftFieldUse(existing_field_id=field_use.field_id, required=field_use.required)
                    for field_use in transition.outputs
                ),
                preconditions=tuple(
                    DraftPrecondition(
                        existing_field_id=precondition.field_id,
                        operator=precondition.operator,
                        value=precondition.value,
                    )
                    for precondition in transition.preconditions
                ),
                criteria=tuple(
                    DraftCriterion(
                        key=criteria_keys[(transition.id, criterion.id)],
                        existing_criterion_id=criterion.id,
                        description=criterion.description,
                    )
                    for criterion in transition.criteria
                ),
            )
            for transition in definition.transitions
        ),
    )
    return payload.model_dump(mode="json")


def _require_exactly_one(existing_id: str | None, draft_key: str | None, label: str) -> None:
    if (existing_id is None) == (draft_key is None):
        raise ValueError(f"{label} must reference exactly one existing id or draft key")


def _nonblank(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value


def _index_existing(source: WorkflowSnapshot) -> tuple[dict[str, StateDefinition], dict[str, FieldDefinition], dict[str, TransitionDefinition], dict[tuple[str, str], AgentCriterion]]:
    state_ids = {state.id: state for state in source.definition.states}
    field_ids = {field.id: field for field in source.definition.fields}
    transition_ids = {transition.id: transition for transition in source.definition.transitions}
    criterion_ids: dict[tuple[str, str], AgentCriterion] = {}
    for transition in source.definition.transitions:
        for criterion in transition.criteria:
            criterion_ids[(transition.id, criterion.id)] = criterion
    return state_ids, field_ids, transition_ids, criterion_ids


def parse_editor_draft(source: WorkflowSnapshot, payload: dict) -> WorkflowDefinition:
    draft = WorkflowEditorDraft.model_validate(payload)
    if draft.id != source.definition.id:
        raise ValueError(f"Draft id {draft.id!r} does not match source {source.definition.id!r}")

    source_states, source_fields, source_transitions, _ = _index_existing(source)

    seen_state_ids: set[str] = set()
    seen_state_keys: set[str] = set()
    state_id_by_key: dict[str, str] = {}
    states: list[StateDefinition] = []
    initial_state_id: str | None = None
    for row in draft.states:
        if row.key in seen_state_keys:
            raise ValueError(f"Duplicate state draft key {row.key!r}")
        seen_state_keys.add(row.key)
        if row.existing_state_id is not None:
            if row.existing_state_id not in source_states:
                raise ValueError(f"Unknown source state id {row.existing_state_id!r}")
            state_id = row.existing_state_id
            if state_id in seen_state_ids:
                raise ValueError(f"Duplicate state id {state_id!r}")
            seen_state_ids.add(state_id)
        else:
            state_id = _draft_key("st")
        if row.initial:
            if initial_state_id is not None:
                raise ValueError("Draft must mark exactly one initial state")
            initial_state_id = state_id
        state_id_by_key[row.key] = state_id
        states.append(
            StateDefinition(id=state_id, name=_nonblank(row.name, "State name"), color=row.color)
        )
    if initial_state_id is None:
        raise ValueError("Draft must mark exactly one initial state")

    seen_field_ids: set[str] = set()
    seen_field_keys: set[str] = set()
    field_id_by_key: dict[str, str] = {}
    fields: list[FieldDefinition] = []
    for row in draft.fields:
        if row.key in seen_field_keys:
            raise ValueError(f"Duplicate field draft key {row.key!r}")
        seen_field_keys.add(row.key)
        if row.existing_field_id is not None:
            if row.existing_field_id not in source_fields:
                raise ValueError(f"Unknown source field id {row.existing_field_id!r}")
            field_id = row.existing_field_id
            if field_id in seen_field_ids:
                raise ValueError(f"Duplicate field id {field_id!r}")
            seen_field_ids.add(field_id)
        else:
            field_id = _draft_key("fl")
        field_id_by_key[row.key] = field_id
        fields.append(
            FieldDefinition(id=field_id, label=_nonblank(row.label, "Field label"), type=row.type)
        )

    final_state_ids = {state.id for state in states}
    final_field_ids = {field.id for field in fields}

    def resolve_state_id(value: str) -> str:
        if value in final_state_ids:
            return value
        if value in state_id_by_key:
            return state_id_by_key[value]
        raise ValueError(f"Unknown state reference {value!r}")

    def resolve_field_id(existing_id: str | None, draft_key: str | None, label: str) -> str:
        _require_exactly_one(existing_id, draft_key, label)
        if existing_id is not None:
            if existing_id not in source_fields:
                raise ValueError(f"Unknown source field id {existing_id!r}")
            if existing_id not in final_field_ids:
                raise ValueError(f"Removed field {existing_id!r} is still referenced")
            return existing_id
        assert draft_key is not None
        if draft_key not in field_id_by_key:
            raise ValueError(f"Unknown draft field key {draft_key!r}")
        return field_id_by_key[draft_key]

    seen_transition_ids: set[str] = set()
    seen_transition_keys: set[str] = set()
    transitions: list[TransitionDefinition] = []
    for row in draft.transitions:
        if row.key in seen_transition_keys:
            raise ValueError(f"Duplicate transition draft key {row.key!r}")
        seen_transition_keys.add(row.key)
        if row.existing_transition_id is not None:
            if row.existing_transition_id not in source_transitions:
                raise ValueError(
                    f"Unknown source transition id {row.existing_transition_id!r}"
                )
            transition_id = row.existing_transition_id
            if transition_id in seen_transition_ids:
                raise ValueError(f"Duplicate transition id {transition_id!r}")
            seen_transition_ids.add(transition_id)
        else:
            transition_id = _draft_key("tr")

        inputs = tuple(
            FieldUse(
                field_id=resolve_field_id(item.existing_field_id, item.draft_field_key, "Field use"),
                required=item.required,
            )
            for item in row.inputs
        )
        outputs = tuple(
            FieldUse(
                field_id=resolve_field_id(item.existing_field_id, item.draft_field_key, "Field use"),
                required=item.required,
            )
            for item in row.outputs
        )
        criteria_seen_ids: set[str] = set()
        criteria_seen_keys: set[str] = set()
        criteria: list[AgentCriterion] = []
        for item in row.criteria:
            if item.key in criteria_seen_keys:
                raise ValueError(f"Duplicate criterion draft key {item.key!r}")
            criteria_seen_keys.add(item.key)
            if item.existing_criterion_id is not None:
                source_transition = source_transitions.get(row.existing_transition_id or "")
                if source_transition is None or not any(
                    criterion.id == item.existing_criterion_id
                    for criterion in source_transition.criteria
                ):
                    raise ValueError(
                        f"Unknown criterion id {item.existing_criterion_id!r} for transition {row.existing_transition_id!r}"
                    )
                criterion_id = item.existing_criterion_id
                if criterion_id in criteria_seen_ids:
                    raise ValueError(f"Duplicate criterion id {criterion_id!r}")
                criteria_seen_ids.add(criterion_id)
            else:
                criterion_id = _draft_key("cr")
            criteria.append(
                AgentCriterion(
                    id=criterion_id,
                    description=_nonblank(item.description, "Criterion description"),
                )
            )

        preconditions = tuple(
            Precondition(
                field_id=resolve_field_id(item.existing_field_id, item.draft_field_key, "Precondition"),
                operator=item.operator,
                value=item.value,
            )
            for item in row.preconditions
        )
        transitions.append(
            TransitionDefinition(
                id=transition_id,
                name=_nonblank(row.name, "Transition name"),
                from_state=resolve_state_id(row.from_state_id),
                to_state=resolve_state_id(row.to_state_id),
                inputs=inputs,
                outputs=outputs,
                preconditions=preconditions,
                criteria=tuple(criteria),
            )
        )

    return WorkflowDefinition(
        schema_version=1,
        id=source.definition.id,
        name=_nonblank(draft.name, "Workflow name"),
        description=draft.description,
        initial_state=initial_state_id,
        states=tuple(states),
        fields=tuple(fields),
        transitions=tuple(transitions),
    )