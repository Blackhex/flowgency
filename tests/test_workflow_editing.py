"""Stable editing commands over immutable workflow definitions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flowgency.workflows.editing import (
    DefinitionEdit,
    add_field,
    add_state,
    add_transition,
    move_state,
    new_definition,
    rename_field,
    rename_state,
    use_field,
)
from flowgency.workflows.models import ContractError, WorkflowDefinition

from tests._ticket_helpers import delivery_definition


def sample_definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(delivery_definition())


def test_rename_keeps_field_references():
    definition = sample_definition()
    edited = rename_field(definition, "verdict", "Review result").definition
    assert edited.field("verdict").label == "Review result"
    assert edited.transition("complete").preconditions[0].field_id == "verdict"


def test_new_definition_is_valid_and_reports_created_id():
    edit = new_definition("Fresh")
    assert isinstance(edit, DefinitionEdit)
    assert edit.created_id == edit.definition.id
    assert edit.definition.initial_state in {s.id for s in edit.definition.states}


def test_new_definition_rejects_blank_name():
    with pytest.raises(ContractError):
        new_definition("   ")


def test_add_field_rejects_blank_label():
    with pytest.raises(ContractError):
        add_field(sample_definition(), "  ", "text")


def test_add_field_uses_prefixed_generated_id():
    edit = add_field(sample_definition(), "Notes", "text")
    assert edit.created_id is not None
    assert edit.created_id.startswith("fl-")
    assert edit.definition.field(edit.created_id).label == "Notes"


def test_add_field_does_not_merge_equal_labels():
    first = add_field(sample_definition(), "Review verdict", "text")
    assert first.created_id != "verdict"
    assert len(first.definition.fields) == len(sample_definition().fields) + 1


def test_use_field_rejects_duplicate_use():
    with pytest.raises(ContractError):
        use_field(sample_definition(), "complete", "input", "verdict", True)


def test_use_field_adds_new_output():
    edit = use_field(sample_definition(), "complete", "input", "summary", False)
    assert edit.created_id is None
    inputs = edit.definition.transition("complete").inputs
    assert any(use.field_id == "summary" for use in inputs)


def test_removing_referenced_field_fails_validation():
    definition = sample_definition()
    stripped = definition.model_copy(
        update={"fields": tuple(f for f in definition.fields if f.id != "verdict")}
    )
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(stripped.model_dump())


def test_generated_ids_are_fresh_after_delete_and_recreate():
    definition = sample_definition()
    first = add_field(definition, "Extra", "text")
    first_id = first.created_id
    without = WorkflowDefinition.model_validate(
        first.definition.model_copy(
            update={
                "fields": tuple(
                    f for f in first.definition.fields if f.id != first_id
                )
            }
        ).model_dump()
    )
    again = add_field(without, "Extra", "text")
    assert again.created_id != first_id


def test_rename_state_keeps_initial_state_reference():
    definition = sample_definition()
    edited = rename_state(definition, "review", "In review").definition
    assert edited.initial_state == "review"
    assert edited.state("review").name == "In review"


def test_move_state_preserves_initial_state_reference():
    definition = sample_definition()
    edited = move_state(definition, "done", 0).definition
    assert edited.states[0].id == "done"
    assert edited.initial_state == "review"


def test_add_state_and_transition_report_created_ids():
    definition = sample_definition()
    state_edit = add_state(definition, "Blocked", "#cccccc")
    assert state_edit.created_id.startswith("st-")
    transition_edit = add_transition(
        state_edit.definition, "Block", "review", state_edit.created_id
    )
    assert transition_edit.created_id.startswith("tr-")
    assert transition_edit.definition.transition(transition_edit.created_id).to_state == (
        state_edit.created_id
    )
