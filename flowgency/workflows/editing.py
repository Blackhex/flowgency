"""Stable editing commands over an immutable workflow definition.

Every command returns a re-validated :class:`~flowgency.workflows.models.WorkflowDefinition`
wrapped in :class:`DefinitionEdit`. Identity is stable: existing slug ids are
preserved, new elements get a ``uuid4().hex`` id with a type prefix, and fields
are reused only through an explicit :class:`FieldUse` – never by merging equal
labels. Renames change display text, never the ids that references point at.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from flowgency.workflows.models import (
    ContractError,
    FieldDefinition,
    FieldKind,
    FieldUse,
    StateDefinition,
    TransitionDefinition,
    WorkflowDefinition,
)

_DEFAULT_STATE_COLOR = "#3b82f6"


@dataclass(frozen=True)
class DefinitionEdit:
    definition: WorkflowDefinition
    created_id: str | None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _rebuild(definition: WorkflowDefinition, **updates) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        definition.model_copy(update=updates).model_dump()
    )


def _require_label(label: str) -> str:
    if not label or not label.strip():
        raise ContractError("blank-label", "Label must not be blank")
    return label


def new_definition(name: str) -> DefinitionEdit:
    workflow_id = _new_id("wf")
    state_id = _new_id("st")
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": 1,
            "id": workflow_id,
            "name": _require_label(name),
            "description": "",
            "initial_state": state_id,
            "states": [
                {"id": state_id, "name": "Start", "color": _DEFAULT_STATE_COLOR}
            ],
            "fields": [],
            "transitions": [],
        }
    )
    return DefinitionEdit(definition, workflow_id)


def add_state(definition: WorkflowDefinition, name: str, color: str) -> DefinitionEdit:
    state_id = _new_id("st")
    state = StateDefinition(id=state_id, name=_require_label(name), color=color)
    updated = _rebuild(definition, states=definition.states + (state,))
    return DefinitionEdit(updated, state_id)


def rename_state(
    definition: WorkflowDefinition, state_id: str, name: str
) -> DefinitionEdit:
    definition.state(state_id)
    label = _require_label(name)
    states = tuple(
        s.model_copy(update={"name": label}) if s.id == state_id else s
        for s in definition.states
    )
    return DefinitionEdit(_rebuild(definition, states=states), None)


def move_state(
    definition: WorkflowDefinition, state_id: str, index: int
) -> DefinitionEdit:
    definition.state(state_id)
    remaining = [s for s in definition.states if s.id != state_id]
    moved = next(s for s in definition.states if s.id == state_id)
    if index < 0 or index > len(remaining):
        raise ContractError("invalid-index", f"State index out of range: {index}")
    remaining.insert(index, moved)
    return DefinitionEdit(_rebuild(definition, states=tuple(remaining)), None)


def add_transition(
    definition: WorkflowDefinition, name: str, from_state: str, to_state: str
) -> DefinitionEdit:
    definition.state(from_state)
    definition.state(to_state)
    transition_id = _new_id("tr")
    transition = TransitionDefinition(
        id=transition_id,
        name=_require_label(name),
        from_state=from_state,
        to_state=to_state,
        inputs=(),
        outputs=(),
        preconditions=(),
        criteria=(),
    )
    updated = _rebuild(definition, transitions=definition.transitions + (transition,))
    return DefinitionEdit(updated, transition_id)


def add_field(
    definition: WorkflowDefinition, label: str, kind: FieldKind
) -> DefinitionEdit:
    field_id = _new_id("fl")
    field = FieldDefinition(id=field_id, label=_require_label(label), type=kind)
    updated = _rebuild(definition, fields=definition.fields + (field,))
    return DefinitionEdit(updated, field_id)


def use_field(
    definition: WorkflowDefinition,
    transition_id: str,
    direction: str,
    field_id: str,
    required: bool,
) -> DefinitionEdit:
    if direction not in ("input", "output"):
        raise ContractError("invalid-direction", f"Unknown direction: {direction!r}")
    transition = definition.transition(transition_id)
    definition.field(field_id)
    use = FieldUse(field_id=field_id, required=required)
    attribute = "inputs" if direction == "input" else "outputs"
    existing = getattr(transition, attribute)
    if any(current.field_id == field_id for current in existing):
        raise ContractError(
            "duplicate-use", f"Field {field_id!r} already used as {direction}"
        )
    updated_transition = transition.model_copy(update={attribute: existing + (use,)})
    transitions = tuple(
        updated_transition if t.id == transition_id else t
        for t in definition.transitions
    )
    return DefinitionEdit(_rebuild(definition, transitions=transitions), None)


def rename_field(
    definition: WorkflowDefinition, field_id: str, label: str
) -> DefinitionEdit:
    definition.field(field_id)
    new_label = _require_label(label)
    fields = tuple(
        f.model_copy(update={"label": new_label}) if f.id == field_id else f
        for f in definition.fields
    )
    return DefinitionEdit(_rebuild(definition, fields=fields), None)
