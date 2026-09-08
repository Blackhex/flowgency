"""Pure workflow evaluation – no I/O, no side effects."""

import dataclasses
import math
from typing import Any

from flowgency.workflows.models import (
    ArtifactRef,
    ContractError,
    CriterionAssessment,
    FieldKind,
    FieldValue,
    WorkflowDefinition,
)


def validate_field_value(
    kind: FieldKind, value: object, *, required: bool
) -> FieldValue:
    if value is None:
        if required:
            raise ContractError("required-value", "A value is required")
        return None
    if kind == "text" and type(value) is str:
        if required and not value.strip():
            raise ContractError("required-value", "Text must not be blank")
        return value
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "number" and type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ContractError("invalid-number", "Number must be finite")
        return value
    if kind == "artifact" and isinstance(value, ArtifactRef):
        return value
    raise ContractError("invalid-type", f"Expected {kind}")


@dataclasses.dataclass(frozen=True)
class EvaluatedTransition:
    destination_state_id: str
    effective_inputs: dict[str, Any]
    effective_outputs: dict[str, Any]
    assessments: tuple[CriterionAssessment, ...]
    transition_snapshot: dict[str, Any]


def evaluate_transition(
    definition: WorkflowDefinition,
    transition_id: str,
    state_id: str,
    current_values: dict[str, FieldValue],
    supplied_inputs: dict[str, FieldValue],
    outputs: dict[str, FieldValue],
    assessments: tuple[CriterionAssessment, ...],
) -> EvaluatedTransition:
    transition = definition.transition(transition_id)

    if transition.from_state != state_id:
        raise ContractError(
            "wrong-source-state",
            f"Transition {transition_id!r} cannot be applied from state {state_id!r}",
        )

    field_kinds = {f.id: f.type for f in definition.fields}

    effective: dict[str, Any] = dict(current_values)
    effective.update(supplied_inputs)

    for use in transition.inputs:
        raw = effective.get(use.field_id)
        kind = field_kinds[use.field_id]
        validate_field_value(kind, raw, required=use.required)

    declared_output_ids = {use.field_id for use in transition.outputs}
    for key in outputs:
        if key not in declared_output_ids:
            raise ContractError(
                "undeclared-output",
                f"Output field {key!r} not declared for transition {transition_id!r}",
            )
    for use in transition.outputs:
        raw = outputs.get(use.field_id)
        kind = field_kinds[use.field_id]
        validate_field_value(kind, raw, required=use.required)

    for pre in transition.preconditions:
        val = effective.get(pre.field_id)
        if pre.operator == "equals":
            if val != pre.value:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: expected {pre.value!r},"
                    f" got {val!r}",
                )
        elif pre.operator == "not_equals":
            if val is None or val == pre.value:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: value must not equal"
                    f" {pre.value!r}",
                )
        elif pre.operator == "is_present":
            if val is None:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: field must be present",
                )

    criterion_ids = {c.id for c in transition.criteria}
    assessed_ids: set[str] = set()

    for assessment in assessments:
        if assessment.criterion_id not in criterion_ids:
            raise ContractError(
                "unknown-criterion",
                f"Assessment for unknown criterion {assessment.criterion_id!r}",
            )
        if assessment.criterion_id in assessed_ids:
            raise ContractError(
                "duplicate-assessment",
                f"Duplicate assessment for criterion {assessment.criterion_id!r}",
            )
        assessed_ids.add(assessment.criterion_id)

        if not assessment.satisfied:
            raise ContractError(
                "criterion-not-satisfied",
                f"Criterion {assessment.criterion_id!r} not satisfied:"
                f" {assessment.reasoning}",
            )

        all_context_keys = set(effective) | set(outputs)
        for sf in assessment.supporting_fields:
            if sf not in all_context_keys:
                raise ContractError(
                    "invalid-supporting-field",
                    f"Supporting field {sf!r} not in supplied context",
                )

    missing_criteria = criterion_ids - assessed_ids
    if missing_criteria:
        raise ContractError(
            "missing-assessment",
            f"Missing assessments for criteria: {sorted(missing_criteria)}",
        )

    effective_inputs = {
        use.field_id: effective.get(use.field_id) for use in transition.inputs
    }

    return EvaluatedTransition(
        destination_state_id=transition.to_state,
        effective_inputs=effective_inputs,
        effective_outputs=dict(outputs),
        assessments=tuple(assessments),
        transition_snapshot=transition.model_dump(),
    )
