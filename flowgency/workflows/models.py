"""Workflow contract models – pure data types, no I/O or side effects."""

import re
from typing import Literal, Union
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_serializer,
    model_validator,
)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

FieldKind = Literal["text", "number", "boolean", "artifact"]

MAX_STATES = 256
MAX_FIELDS = 1_024
MAX_TRANSITIONS = 2_048
MAX_CRITERIA_PER_TRANSITION = 128
MAX_BLUEPRINT_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB


class ContractError(Exception):
    """Raised when a workflow contract is violated."""

    def __init__(self, code: str, message: str, field_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_id = field_id

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["id", "url"]
    value: str

    @model_validator(mode="after")
    def _validate_ref(self) -> "ArtifactRef":
        if self.kind == "url":
            parsed = urlparse(self.value)
            if parsed.scheme != "https":
                raise ValueError("Artifact URL must use HTTPS")
            if not parsed.netloc:
                raise ValueError("Artifact URL must have a valid host")
            if "@" in parsed.netloc:
                raise ValueError("Artifact URL must not contain credentials")
        elif self.kind == "id":
            if "/" in self.value or "\\" in self.value:
                raise ValueError("Artifact ID must not contain path separators")
        return self


# Strict union – bool before int so True/False never coerce to 1/0.
FieldValue = Union[StrictBool, StrictInt, StrictFloat, StrictStr, ArtifactRef, None]


class FieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    type: FieldKind
    artifact_format: Literal["git-change"] | None = None

    @model_validator(mode="after")
    def _validate_artifact_format(self) -> "FieldDefinition":
        if self.artifact_format is not None and self.type != "artifact":
            raise ValueError("Artifact format requires an artifact field")
        return self

    @model_serializer(mode="wrap")
    def _serialize_field(self, handler):
        payload = handler(self)
        if self.artifact_format is None:
            payload.pop("artifact_format", None)
        return payload


class FieldUse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field_id: str
    required: bool


class StateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    color: str

    @model_validator(mode="after")
    def _validate_color(self) -> "StateDefinition":
        if not _HEX_COLOR_RE.match(self.color):
            raise ValueError(f"Invalid color hex: {self.color!r}")
        return self


class Precondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field_id: str
    operator: Literal["equals", "not_equals", "is_present"]
    value: FieldValue = None

    @model_validator(mode="after")
    def _validate_value(self) -> "Precondition":
        if self.operator in ("equals", "not_equals") and self.value is None:
            raise ValueError(f"Operator {self.operator!r} requires a comparison value")
        if self.operator == "is_present" and self.value is not None:
            raise ValueError("Operator 'is_present' must not have a value")
        return self


class AgentCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    description: str


class TransitionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    from_state: str
    to_state: str
    inputs: tuple[FieldUse, ...]
    outputs: tuple[FieldUse, ...]
    preconditions: tuple[Precondition, ...]
    criteria: tuple[AgentCriterion, ...]

    @model_validator(mode="after")
    def _validate_criteria_count(self) -> "TransitionDefinition":
        if len(self.criteria) > MAX_CRITERIA_PER_TRANSITION:
            raise ValueError(
                f"At most {MAX_CRITERIA_PER_TRANSITION} criteria per transition"
            )
        criterion_ids = [c.id for c in self.criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("Duplicate criterion IDs in transition")
        input_ids = [u.field_id for u in self.inputs]
        if len(set(input_ids)) != len(input_ids):
            raise ValueError("Duplicate input field uses in transition")
        output_ids = [u.field_id for u in self.outputs]
        if len(set(output_ids)) != len(output_ids):
            raise ValueError("Duplicate output field uses in transition")
        input_id_set = set(input_ids)
        for precondition in self.preconditions:
            if precondition.field_id not in input_id_set:
                raise ValueError(
                    f"Transition {self.id!r}: precondition field {precondition.field_id!r}"
                    " must be declared as an input"
                )
        return self


class CriterionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    criterion_id: str
    satisfied: StrictBool
    reasoning: str
    supporting_fields: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_reasoning(self) -> "CriterionAssessment":
        if not self.reasoning.strip():
            raise ValueError("Assessment reasoning must not be blank")
        return self


def _kind_matches_value(kind: FieldKind, value: object) -> bool:
    """Return True only when value's type is exactly right for kind."""
    if kind == "text":
        return type(value) is str
    if kind == "boolean":
        return type(value) is bool
    if kind == "number":
        return type(value) in (int, float)
    if kind == "artifact":
        return isinstance(value, ArtifactRef)
    return False


def check_source_size(source: bytes) -> None:
    """Raise ContractError if source exceeds MAX_BLUEPRINT_SOURCE_BYTES."""
    if len(source) > MAX_BLUEPRINT_SOURCE_BYTES:
        raise ContractError(
            "source-too-large",
            f"Blueprint source must not exceed {MAX_BLUEPRINT_SOURCE_BYTES} bytes,"
            f" got {len(source)}",
        )


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    id: str
    name: str
    description: str
    initial_state: str
    states: tuple[StateDefinition, ...]
    fields: tuple[FieldDefinition, ...]
    transitions: tuple[TransitionDefinition, ...]

    @model_validator(mode="after")
    def _validate_definition(self) -> "WorkflowDefinition":
        state_ids = {s.id for s in self.states}
        field_ids = {f.id for f in self.fields}
        transition_ids = {t.id for t in self.transitions}

        if len(self.states) > MAX_STATES:
            raise ValueError(f"At most {MAX_STATES} states allowed")
        if len(self.fields) > MAX_FIELDS:
            raise ValueError(f"At most {MAX_FIELDS} fields allowed")
        if len(self.transitions) > MAX_TRANSITIONS:
            raise ValueError(f"At most {MAX_TRANSITIONS} transitions allowed")

        if len(state_ids) != len(self.states):
            raise ValueError("Duplicate state IDs")
        if len(field_ids) != len(self.fields):
            raise ValueError("Duplicate field IDs")
        if len(transition_ids) != len(self.transitions):
            raise ValueError("Duplicate transition IDs")

        if self.initial_state not in state_ids:
            raise ValueError(
                f"initial_state {self.initial_state!r} is not a declared state"
            )

        for t in self.transitions:
            if t.from_state not in state_ids:
                raise ValueError(
                    f"Transition {t.id!r}: from_state {t.from_state!r} not declared"
                )
            if t.to_state not in state_ids:
                raise ValueError(
                    f"Transition {t.id!r}: to_state {t.to_state!r} not declared"
                )
            for use in t.inputs:
                if use.field_id not in field_ids:
                    raise ValueError(
                        f"Transition {t.id!r}: input field {use.field_id!r} not declared"
                    )
            for use in t.outputs:
                if use.field_id not in field_ids:
                    raise ValueError(
                        f"Transition {t.id!r}: output field {use.field_id!r} not declared"
                    )
            for pre in t.preconditions:
                if pre.field_id not in field_ids:
                    raise ValueError(
                        f"Transition {t.id!r}: precondition field {pre.field_id!r} not declared"
                    )
                if pre.operator in ("equals", "not_equals"):
                    field_kind = next(
                        f.type for f in self.fields if f.id == pre.field_id
                    )
                    if not _kind_matches_value(field_kind, pre.value):
                        raise ValueError(
                            f"Transition {t.id!r}: precondition on {pre.field_id!r}"
                            f" value type does not match field type {field_kind!r}"
                        )
        return self

    def state(self, state_id: str) -> StateDefinition:
        for s in self.states:
            if s.id == state_id:
                return s
        raise ContractError("unknown-reference", f"Unknown state {state_id!r}")

    def field(self, field_id: str) -> FieldDefinition:
        for f in self.fields:
            if f.id == field_id:
                return f
        raise ContractError("unknown-reference", f"Unknown field {field_id!r}")

    def transition(self, transition_id: str) -> TransitionDefinition:
        for t in self.transitions:
            if t.id == transition_id:
                return t
        raise ContractError("unknown-reference", f"Unknown transition {transition_id!r}")
