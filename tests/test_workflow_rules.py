"""TDD tests for workflow transition rules and field-value validation."""

from decimal import Decimal
import pytest

from flowgency.workflows.models import (
    ContractError,
    WorkflowDefinition,
)
from flowgency.workflows.rules import evaluate_transition, validate_field_value


def sample_definition():
    return WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "delivery",
        "name": "Delivery",
        "description": "Deliver verified work",
        "initial_state": "review",
        "states": [
            {"id": "review", "name": "Review", "color": "#ebc77c"},
            {"id": "done", "name": "Done", "color": "#7ad7bf"},
        ],
        "fields": [
            {"id": "verdict", "label": "Review verdict", "type": "boolean"},
            {"id": "summary", "label": "Review summary", "type": "text"},
        ],
        "transitions": [{
            "id": "complete", "name": "Complete", "from_state": "review",
            "to_state": "done", "inputs": [{"field_id": "verdict", "required": True}],
            "outputs": [{"field_id": "summary", "required": True}],
            "preconditions": [{"field_id": "verdict", "operator": "equals", "value": True}],
            "criteria": [],
        }],
    })


# ---------------------------------------------------------------------------
# validate_field_value – Step 1 tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,value", [("boolean", False), ("number", 0)])
def test_required_false_and_zero_are_values(kind, value):
    assert validate_field_value(kind, value, required=True) == value


@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_text_cannot_be_blank(value):
    with pytest.raises(ContractError):
        validate_field_value("text", value, required=True)


@pytest.mark.parametrize("value", [True, "1", float("nan"), Decimal("1")])
def test_number_has_no_implicit_coercion(value):
    with pytest.raises(ContractError):
        validate_field_value("number", value, required=True)


def test_optional_none_is_accepted():
    assert validate_field_value("text", None, required=False) is None


def test_optional_blank_text_is_accepted():
    assert validate_field_value("text", "", required=False) == ""


def test_finite_int_is_accepted():
    assert validate_field_value("number", 42, required=True) == 42


def test_finite_float_is_accepted():
    assert validate_field_value("number", 3.14, required=True) == 3.14


def test_inf_is_rejected():
    with pytest.raises(ContractError):
        validate_field_value("number", float("inf"), required=False)


# ---------------------------------------------------------------------------
# evaluate_transition – Step 4 tests
# ---------------------------------------------------------------------------

def test_negative_verdict_cannot_transition():
    with pytest.raises(ContractError, match="precondition"):
        evaluate_transition(
            sample_definition(), "complete", "review", {"verdict": False},
            {}, {"summary": "Reviewed"}, (),
        )


def test_positive_verdict_transitions():
    result = evaluate_transition(
        sample_definition(), "complete", "review", {"verdict": True},
        {}, {"summary": "Done it"}, (),
    )
    assert result.destination_state_id == "done"


def test_missing_required_input_raises():
    with pytest.raises(ContractError):
        evaluate_transition(
            sample_definition(), "complete", "review", {},
            {}, {"summary": "Done it"}, (),
        )


def test_explicit_null_for_required_input_raises():
    with pytest.raises(ContractError):
        evaluate_transition(
            sample_definition(), "complete", "review", {"verdict": None},
            {}, {"summary": "Done it"}, (),
        )


def test_missing_required_output_raises():
    with pytest.raises(ContractError):
        evaluate_transition(
            sample_definition(), "complete", "review", {"verdict": True},
            {}, {}, (),
        )


def test_wrong_source_state_raises():
    with pytest.raises(ContractError):
        evaluate_transition(
            sample_definition(), "complete", "done", {"verdict": True},
            {}, {"summary": "Done it"}, (),
        )


def test_undeclared_output_field_raises():
    with pytest.raises(ContractError):
        evaluate_transition(
            sample_definition(), "complete", "review", {"verdict": True},
            {}, {"summary": "Done it", "ghost": "extra"}, (),
        )


def test_unknown_transition_raises():
    with pytest.raises(ContractError):
        evaluate_transition(
            sample_definition(), "nonexistent", "review", {"verdict": True},
            {}, {"summary": "Done it"}, (),
        )


def test_result_snapshot_is_immutable():
    result = evaluate_transition(
        sample_definition(), "complete", "review", {"verdict": True},
        {}, {"summary": "Done it"}, (),
    )
    # EvaluatedTransition is frozen; attribute assignment must fail
    with pytest.raises((AttributeError, TypeError)):
        result.destination_state_id = "hacked"  # type: ignore[misc]


def test_result_contains_effective_values():
    result = evaluate_transition(
        sample_definition(), "complete", "review", {"verdict": True},
        {}, {"summary": "Done it"}, (),
    )
    assert result.effective_inputs == {"verdict": True}
    assert result.effective_outputs == {"summary": "Done it"}


def _make_criterion_def():
    """Return a definition with one agent criterion on the transition."""
    return WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "w", "name": "W", "description": "D",
        "initial_state": "s1",
        "states": [
            {"id": "s1", "name": "S1", "color": "#aaaaaa"},
            {"id": "s2", "name": "S2", "color": "#bbbbbb"},
        ],
        "fields": [
            {"id": "f1", "label": "F1", "type": "boolean"},
        ],
        "transitions": [{
            "id": "t1", "name": "T1", "from_state": "s1", "to_state": "s2",
            "inputs": [{"field_id": "f1", "required": True}],
            "outputs": [],
            "preconditions": [],
            "criteria": [{"id": "c1", "agent_id": "ag1", "description": "Quality check"}],
        }],
    })


def test_criterion_assessment_required():
    defn = _make_criterion_def()
    with pytest.raises(ContractError):
        evaluate_transition(defn, "t1", "s1", {"f1": True}, {}, {}, ())


def test_criterion_satisfied_passes():
    from flowgency.workflows.models import CriterionAssessment
    defn = _make_criterion_def()
    result = evaluate_transition(
        defn, "t1", "s1", {"f1": True}, {}, {},
        (CriterionAssessment(
            criterion_id="c1", satisfied=True,
            reasoning="Looks good", supporting_fields=(),
        ),),
    )
    assert result.destination_state_id == "s2"


def test_criterion_unsatisfied_fails():
    from flowgency.workflows.models import CriterionAssessment
    defn = _make_criterion_def()
    with pytest.raises(ContractError):
        evaluate_transition(
            defn, "t1", "s1", {"f1": True}, {}, {},
            (CriterionAssessment(
                criterion_id="c1", satisfied=False,
                reasoning="Not good enough", supporting_fields=(),
            ),),
        )


def test_duplicate_criterion_assessment_raises():
    from flowgency.workflows.models import CriterionAssessment
    defn = _make_criterion_def()
    with pytest.raises(ContractError):
        evaluate_transition(
            defn, "t1", "s1", {"f1": True}, {}, {},
            (
                CriterionAssessment(
                    criterion_id="c1", satisfied=True,
                    reasoning="First", supporting_fields=(),
                ),
                CriterionAssessment(
                    criterion_id="c1", satisfied=True,
                    reasoning="Duplicate", supporting_fields=(),
                ),
            ),
        )


def test_criterion_for_unknown_id_raises():
    from flowgency.workflows.models import CriterionAssessment
    defn = _make_criterion_def()
    with pytest.raises(ContractError):
        evaluate_transition(
            defn, "t1", "s1", {"f1": True}, {}, {},
            (CriterionAssessment(
                criterion_id="unknown", satisfied=True,
                reasoning="Stray", supporting_fields=(),
            ),),
        )


def test_empty_criterion_reasoning_raises():
    from flowgency.workflows.models import CriterionAssessment
    with pytest.raises((ContractError, Exception)):
        CriterionAssessment(
            criterion_id="c1", satisfied=True,
            reasoning="   ", supporting_fields=(),
        )


def test_not_equals_precondition():
    defn = WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "w", "name": "W", "description": "D",
        "initial_state": "s1",
        "states": [
            {"id": "s1", "name": "S1", "color": "#aaaaaa"},
            {"id": "s2", "name": "S2", "color": "#bbbbbb"},
        ],
        "fields": [{"id": "f1", "label": "F1", "type": "text"}],
        "transitions": [{
            "id": "t1", "name": "T1", "from_state": "s1", "to_state": "s2",
            "inputs": [{"field_id": "f1", "required": True}],
            "outputs": [],
            "preconditions": [
                {"field_id": "f1", "operator": "not_equals", "value": "blocked"}
            ],
            "criteria": [],
        }],
    })
    result = evaluate_transition(
        defn, "t1", "s1", {"f1": "approved"}, {}, {}, (),
    )
    assert result.destination_state_id == "s2"


def test_not_equals_when_value_matches_raises():
    defn = WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "w", "name": "W", "description": "D",
        "initial_state": "s1",
        "states": [
            {"id": "s1", "name": "S1", "color": "#aaaaaa"},
            {"id": "s2", "name": "S2", "color": "#bbbbbb"},
        ],
        "fields": [{"id": "f1", "label": "F1", "type": "text"}],
        "transitions": [{
            "id": "t1", "name": "T1", "from_state": "s1", "to_state": "s2",
            "inputs": [{"field_id": "f1", "required": True}],
            "outputs": [],
            "preconditions": [
                {"field_id": "f1", "operator": "not_equals", "value": "blocked"}
            ],
            "criteria": [],
        }],
    })
    with pytest.raises(ContractError, match="precondition"):
        evaluate_transition(
            defn, "t1", "s1", {"f1": "blocked"}, {}, {}, (),
        )


def test_missing_value_never_satisfies_not_equals():
    defn = WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "w", "name": "W", "description": "D",
        "initial_state": "s1",
        "states": [
            {"id": "s1", "name": "S1", "color": "#aaaaaa"},
            {"id": "s2", "name": "S2", "color": "#bbbbbb"},
        ],
        "fields": [{"id": "f1", "label": "F1", "type": "text"}],
        "transitions": [{
            "id": "t1", "name": "T1", "from_state": "s1", "to_state": "s2",
            "inputs": [],
            "outputs": [],
            "preconditions": [
                {"field_id": "f1", "operator": "not_equals", "value": "blocked"}
            ],
            "criteria": [],
        }],
    })
    with pytest.raises(ContractError, match="precondition"):
        evaluate_transition(defn, "t1", "s1", {}, {}, {}, ())


def test_is_present_precondition():
    defn = WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "w", "name": "W", "description": "D",
        "initial_state": "s1",
        "states": [
            {"id": "s1", "name": "S1", "color": "#aaaaaa"},
            {"id": "s2", "name": "S2", "color": "#bbbbbb"},
        ],
        "fields": [{"id": "f1", "label": "F1", "type": "text"}],
        "transitions": [{
            "id": "t1", "name": "T1", "from_state": "s1", "to_state": "s2",
            "inputs": [{"field_id": "f1", "required": False}],
            "outputs": [],
            "preconditions": [{"field_id": "f1", "operator": "is_present"}],
            "criteria": [],
        }],
    })
    result = evaluate_transition(
        defn, "t1", "s1", {"f1": "hello"}, {}, {}, (),
    )
    assert result.destination_state_id == "s2"


def test_is_present_when_absent_raises():
    defn = WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "w", "name": "W", "description": "D",
        "initial_state": "s1",
        "states": [
            {"id": "s1", "name": "S1", "color": "#aaaaaa"},
            {"id": "s2", "name": "S2", "color": "#bbbbbb"},
        ],
        "fields": [{"id": "f1", "label": "F1", "type": "text"}],
        "transitions": [{
            "id": "t1", "name": "T1", "from_state": "s1", "to_state": "s2",
            "inputs": [{"field_id": "f1", "required": False}],
            "outputs": [],
            "preconditions": [{"field_id": "f1", "operator": "is_present"}],
            "criteria": [],
        }],
    })
    with pytest.raises(ContractError, match="precondition"):
        evaluate_transition(defn, "t1", "s1", {}, {}, {}, ())


def test_supporting_fields_must_reference_supplied_values():
    from flowgency.workflows.models import CriterionAssessment
    defn = _make_criterion_def()
    with pytest.raises(ContractError):
        evaluate_transition(
            defn, "t1", "s1", {"f1": True}, {}, {},
            (CriterionAssessment(
                criterion_id="c1", satisfied=True,
                reasoning="OK", supporting_fields=("not_a_field",),
            ),),
        )
