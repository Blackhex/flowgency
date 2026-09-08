"""TDD tests for workflow model contracts and structural validation."""

import pytest
from pydantic import ValidationError

from flowgency.workflows.models import (
    ContractError,
    WorkflowDefinition,
)


def _minimal(overrides=None):
    base = {
        "schema_version": 1,
        "id": "basic",
        "name": "Basic",
        "description": "A basic workflow",
        "initial_state": "open",
        "states": [{"id": "open", "name": "Open", "color": "#aaaaaa"}],
        "fields": [],
        "transitions": [],
    }
    if overrides:
        base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema / structural validation
# ---------------------------------------------------------------------------

def test_minimal_definition_is_valid():
    defn = WorkflowDefinition.model_validate(_minimal())
    assert defn.id == "basic"
    assert defn.initial_state == "open"


def test_frozen_model_rejects_mutation():
    defn = WorkflowDefinition.model_validate(_minimal())
    with pytest.raises((AttributeError, ValidationError, TypeError)):
        defn.id = "mutated"  # type: ignore[misc]


def test_state_lookup_finds_match():
    defn = WorkflowDefinition.model_validate(_minimal())
    assert defn.state("open").id == "open"


def test_state_lookup_raises_on_unknown():
    defn = WorkflowDefinition.model_validate(_minimal())
    with pytest.raises(ContractError):
        defn.state("nope")


def test_field_lookup_finds_match():
    defn = WorkflowDefinition.model_validate({
        **_minimal(),
        "fields": [{"id": "notes", "label": "Notes", "type": "text"}],
    })
    assert defn.field("notes").id == "notes"


def test_field_lookup_raises_on_unknown():
    defn = WorkflowDefinition.model_validate(_minimal())
    with pytest.raises(ContractError):
        defn.field("ghost")


def test_transition_lookup_finds_match():
    defn = WorkflowDefinition.model_validate({
        **_minimal(),
        "states": [
            {"id": "open", "name": "Open", "color": "#aaaaaa"},
            {"id": "closed", "name": "Closed", "color": "#bbbbbb"},
        ],
        "transitions": [{
            "id": "close", "name": "Close", "from_state": "open",
            "to_state": "closed", "inputs": [], "outputs": [],
            "preconditions": [], "criteria": [],
        }],
    })
    assert defn.transition("close").id == "close"


def test_transition_lookup_raises_on_unknown():
    defn = WorkflowDefinition.model_validate(_minimal())
    with pytest.raises(ContractError):
        defn.transition("ghost")


def test_duplicate_state_ids_raise():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [
                {"id": "open", "name": "Open", "color": "#aaaaaa"},
                {"id": "open", "name": "Open2", "color": "#bbbbbb"},
            ],
        })


def test_duplicate_field_ids_raise():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "fields": [
                {"id": "f1", "label": "F1", "type": "text"},
                {"id": "f1", "label": "F2", "type": "boolean"},
            ],
        })


def test_duplicate_transition_ids_raise():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [
                {"id": "open", "name": "Open", "color": "#aaaaaa"},
                {"id": "closed", "name": "Closed", "color": "#bbbbbb"},
            ],
            "transitions": [
                {
                    "id": "t1", "name": "T1", "from_state": "open",
                    "to_state": "closed", "inputs": [], "outputs": [],
                    "preconditions": [], "criteria": [],
                },
                {
                    "id": "t1", "name": "T1b", "from_state": "open",
                    "to_state": "closed", "inputs": [], "outputs": [],
                    "preconditions": [], "criteria": [],
                },
            ],
        })


def test_missing_initial_state_raises():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "initial_state": "nonexistent",
        })


def test_invalid_color_hex_raises():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [{"id": "open", "name": "Open", "color": "notacolor"}],
        })


def test_undeclared_field_in_transition_input_raises():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [
                {"id": "open", "name": "Open", "color": "#aaaaaa"},
                {"id": "closed", "name": "Closed", "color": "#bbbbbb"},
            ],
            "fields": [],
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open",
                "to_state": "closed",
                "inputs": [{"field_id": "undeclared", "required": True}],
                "outputs": [], "preconditions": [], "criteria": [],
            }],
        })


def test_undeclared_field_in_transition_output_raises():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [
                {"id": "open", "name": "Open", "color": "#aaaaaa"},
                {"id": "closed", "name": "Closed", "color": "#bbbbbb"},
            ],
            "fields": [],
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open",
                "to_state": "closed", "inputs": [],
                "outputs": [{"field_id": "undeclared", "required": True}],
                "preconditions": [], "criteria": [],
            }],
        })


def test_undeclared_from_state_raises():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [
                {"id": "open", "name": "Open", "color": "#aaaaaa"},
                {"id": "closed", "name": "Closed", "color": "#bbbbbb"},
            ],
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "ghost",
                "to_state": "closed", "inputs": [], "outputs": [],
                "preconditions": [], "criteria": [],
            }],
        })


def test_undeclared_to_state_raises():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_minimal(),
            "states": [
                {"id": "open", "name": "Open", "color": "#aaaaaa"},
            ],
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open",
                "to_state": "ghost", "inputs": [], "outputs": [],
                "preconditions": [], "criteria": [],
            }],
        })


def test_definition_sequences_are_tuples():
    defn = WorkflowDefinition.model_validate(_minimal())
    assert isinstance(defn.states, tuple)
    assert isinstance(defn.fields, tuple)
    assert isinstance(defn.transitions, tuple)


def test_extra_fields_on_definition_rejected():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({**_minimal(), "extra_key": "bad"})
