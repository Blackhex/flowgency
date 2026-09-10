"""Pure workflow evaluation – no I/O, no side effects."""

import dataclasses
import math
import types
from typing import Any

from flowgency.workflows.models import (
    ArtifactRef,
    ContractError,
    CriterionAssessment,
    FieldKind,
    FieldValue,
    WorkflowDefinition,
    _kind_matches_value,
)


def _deep_freeze(obj: Any) -> Any:
    """Recursively convert dicts to MappingProxyType and sequences to tuples."""
    if isinstance(obj, dict):
        return types.MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(i) for i in obj)
    return obj


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
    effective_inputs: types.MappingProxyType
    effective_outputs: types.MappingProxyType
    assessments: tuple[CriterionAssessment, ...]
    transition_snapshot: types.MappingProxyType

    def to_json(self) -> dict[str, Any]:
        def _cv(obj: Any) -> Any:
            if isinstance(obj, (types.MappingProxyType, dict)):
                return {k: _cv(v) for k, v in obj.items()}
            if isinstance(obj, (tuple, list)):
                return [_cv(i) for i in obj]
            if isinstance(obj, ArtifactRef):
                return obj.model_dump()
            return obj

        return {
            "destination_state_id": self.destination_state_id,
            "effective_inputs": _cv(self.effective_inputs),
            "effective_outputs": _cv(self.effective_outputs),
            "transition_snapshot": _cv(self.transition_snapshot),
            "assessments": [
                {
                    "criterion_id": a.criterion_id,
                    "satisfied": a.satisfied,
                    "reasoning": a.reasoning,
                    "supporting_fields": list(a.supporting_fields),
                }
                for a in self.assessments
            ],
        }


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
        try:
            validate_field_value(kind, raw, required=use.required)
        except ContractError as err:
            raise ContractError(err.code, err.message, field_id=use.field_id) from err

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
        try:
            validate_field_value(kind, raw, required=use.required)
        except ContractError as err:
            raise ContractError(err.code, err.message, field_id=use.field_id) from err

    input_field_ids = {use.field_id for use in transition.inputs}
    for pre in transition.preconditions:
        # Validate effective value type for fields not already validated as inputs.
        if pre.field_id not in input_field_ids:
            val_raw = effective.get(pre.field_id)
            if val_raw is not None:
                kind = field_kinds[pre.field_id]
                if not _kind_matches_value(kind, val_raw):
                    raise ContractError(
                        "invalid-type",
                        f"Field {pre.field_id!r}: expected {kind}",
                        field_id=pre.field_id,
                    )

        val = effective.get(pre.field_id)
        if pre.operator == "equals":
            if type(val) is not type(pre.value) or val != pre.value:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: expected {pre.value!r},"
                    f" got {val!r}",
                    field_id=pre.field_id,
                )
        elif pre.operator == "not_equals":
            if val is None:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: value must not equal"
                    f" {pre.value!r}",
                    field_id=pre.field_id,
                )
            if type(val) is type(pre.value) and val == pre.value:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: value must not equal"
                    f" {pre.value!r}",
                    field_id=pre.field_id,
                )
        elif pre.operator == "is_present":
            if val is None:
                raise ContractError(
                    "precondition-failed",
                    f"Precondition on {pre.field_id!r}: field must be present",
                    field_id=pre.field_id,
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

    # Distinguish absent fields from explicit-null fields.
    effective_inputs = {
        use.field_id: effective[use.field_id]
        for use in transition.inputs
        if use.field_id in effective
    }

    referenced_field_ids = (
        {u.field_id for u in transition.inputs}
        | {u.field_id for u in transition.outputs}
        | {p.field_id for p in transition.preconditions}
    )
    snapshot = {
        "id": transition.id,
        "name": transition.name,
        "from_state": transition.from_state,
        "to_state": transition.to_state,
        "inputs": tuple(
            {"field_id": u.field_id, "required": u.required} for u in transition.inputs
        ),
        "outputs": tuple(
            {"field_id": u.field_id, "required": u.required} for u in transition.outputs
        ),
        "preconditions": tuple(p.model_dump() for p in transition.preconditions),
        "criteria": tuple(c.model_dump() for c in transition.criteria),
        "field_defs": {
            fid: definition.field(fid).model_dump()
            for fid in sorted(referenced_field_ids)
        },
    }

    return EvaluatedTransition(
        destination_state_id=transition.to_state,
        effective_inputs=types.MappingProxyType(effective_inputs),
        effective_outputs=types.MappingProxyType(dict(outputs)),
        assessments=tuple(assessments),
        transition_snapshot=_deep_freeze(snapshot),
    )
