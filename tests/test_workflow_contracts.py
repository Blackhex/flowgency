"""TDD tests for workflow model contracts and structural validation."""

import pytest
from pydantic import ValidationError

from flowgency.workflows.models import (
    AgentCriterion,
    ArtifactRef,
    ContractError,
    MAX_BLUEPRINT_SOURCE_BYTES,
    WorkflowDefinition,
    check_source_size,
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


# ---------------------------------------------------------------------------
# Source size guard (Finding 1)
# ---------------------------------------------------------------------------

def test_source_at_exact_limit_is_accepted():
    check_source_size(bytes(MAX_BLUEPRINT_SOURCE_BYTES))


def test_source_one_byte_over_limit_raises():
    with pytest.raises(ContractError, match="source-too-large"):
        check_source_size(bytes(MAX_BLUEPRINT_SOURCE_BYTES + 1))


def test_source_multibyte_over_limit_raises():
    with pytest.raises(ContractError, match="source-too-large"):
        check_source_size(bytes(MAX_BLUEPRINT_SOURCE_BYTES + 100))


# ---------------------------------------------------------------------------
# AgentCriterion – unbound (Finding 2)
# ---------------------------------------------------------------------------

def test_criterion_accepts_unbound_criterion():
    c = AgentCriterion(id="c1", description="Quality check")
    assert c.id == "c1"
    assert c.description == "Quality check"


def test_criterion_rejects_agent_id_under_extra_forbid():
    with pytest.raises(ValidationError):
        AgentCriterion(id="c1", agent_id="ag1", description="Quality check")


# ---------------------------------------------------------------------------
# ArtifactRef path / URL boundaries (Finding 5)
# ---------------------------------------------------------------------------

def test_artifact_id_accepts_opaque_id():
    ref = ArtifactRef(kind="id", value="abc-123-xyz")
    assert ref.value == "abc-123-xyz"


def test_artifact_id_rejects_forward_slash_path():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="id", value="/absolute/path")


def test_artifact_id_rejects_relative_path():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="id", value="../relative/path")


def test_artifact_id_rejects_server_relative_path():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="id", value="some/nested/id")


def test_artifact_id_rejects_backslash_path():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="id", value="C:\\Windows\\path")


def test_artifact_url_accepts_valid_https():
    ref = ArtifactRef(kind="url", value="https://example.com/artifact/123")
    assert ref.kind == "url"


def test_artifact_url_rejects_missing_host():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="url", value="https://")


def test_artifact_url_rejects_credentials():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="url", value="https://user:pass@example.com/artifact")


def test_artifact_url_rejects_http():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="url", value="http://example.com/artifact")


def test_artifact_url_rejects_plain_string():
    with pytest.raises(ValidationError):
        ArtifactRef(kind="url", value="not-a-url-at-all")


# ---------------------------------------------------------------------------
# Duplicate constraint IDs and field uses (Finding 3)
# ---------------------------------------------------------------------------

def _two_state_base():
    return {
        **_minimal(),
        "states": [
            {"id": "open", "name": "Open", "color": "#aaaaaa"},
            {"id": "closed", "name": "Closed", "color": "#bbbbbb"},
        ],
        "fields": [
            {"id": "f1", "label": "F1", "type": "text"},
            {"id": "f2", "label": "F2", "type": "text"},
        ],
    }


def test_duplicate_criterion_ids_in_transition_raise():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_two_state_base(),
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open", "to_state": "closed",
                "inputs": [], "outputs": [], "preconditions": [],
                "criteria": [
                    {"id": "c1", "description": "First"},
                    {"id": "c1", "description": "Duplicate"},
                ],
            }],
        })


def test_duplicate_input_field_uses_raise():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_two_state_base(),
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open", "to_state": "closed",
                "inputs": [
                    {"field_id": "f1", "required": True},
                    {"field_id": "f1", "required": False},
                ],
                "outputs": [], "preconditions": [], "criteria": [],
            }],
        })


def test_duplicate_output_field_uses_raise():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_two_state_base(),
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open", "to_state": "closed",
                "inputs": [],
                "outputs": [
                    {"field_id": "f1", "required": True},
                    {"field_id": "f1", "required": False},
                ],
                "preconditions": [], "criteria": [],
            }],
        })


def test_precondition_value_type_must_match_field_type():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_two_state_base(),
            "fields": [{"id": "f1", "label": "F1", "type": "boolean"}],
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open", "to_state": "closed",
                "inputs": [], "outputs": [],
                "preconditions": [
                    {"field_id": "f1", "operator": "equals", "value": 1}
                ],
                "criteria": [],
            }],
        })


def test_precondition_bool_value_rejected_for_number_field():
    with pytest.raises((ValidationError, ContractError, ValueError)):
        WorkflowDefinition.model_validate({
            **_two_state_base(),
            "fields": [{"id": "f1", "label": "F1", "type": "number"}],
            "transitions": [{
                "id": "t1", "name": "T1", "from_state": "open", "to_state": "closed",
                "inputs": [], "outputs": [],
                "preconditions": [
                    {"field_id": "f1", "operator": "not_equals", "value": True}
                ],
                "criteria": [],
            }],
        })
