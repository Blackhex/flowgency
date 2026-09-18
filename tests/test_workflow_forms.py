from __future__ import annotations

import pytest

from flowgency.workflows.forms import editor_payload, parse_editor_draft

from tests._ticket_helpers import delivery_definition


def test_editor_rename_keeps_ids(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")

    draft = editor_payload(source)
    draft["name"] = "Renamed delivery"
    draft["states"][0]["name"] = "Inspection"
    draft["transitions"][0]["name"] = "Finish"

    parsed = parse_editor_draft(source, draft)

    assert parsed.id == source.definition.id
    assert parsed.states[0].id == source.definition.states[0].id
    assert parsed.transitions[0].id == source.definition.transitions[0].id
    assert (
        parsed.transitions[0].from_state
        == source.definition.transitions[0].from_state
    )


def test_editor_payload_round_trip_preserves_git_change_artifact_format(workflow_env):
    env = workflow_env
    definition = delivery_definition()
    definition["fields"].append(
        {"id": "diff", "label": "Change diff", "type": "artifact", "artifact_format": "git-change"}
    )
    env.write_blueprint("delivery", definition)
    source = env.library.inspect("delivery")

    draft = editor_payload(source)
    diff_field = next(field for field in draft["fields"] if field["existing_field_id"] == "diff")
    assert diff_field["artifact_format"] == "git-change"
    other_field = next(field for field in draft["fields"] if field["existing_field_id"] == "verdict")
    assert other_field["artifact_format"] is None

    parsed = parse_editor_draft(source, draft)

    assert parsed.field("diff").artifact_format == "git-change"
    assert parsed.field("verdict").artifact_format is None


def test_parse_editor_draft_rejects_forged_existing_field_id(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")

    draft = editor_payload(source)
    draft["transitions"][0]["inputs"][0]["existing_field_id"] = "forged-field"

    try:
        parse_editor_draft(source, draft)
    except ValueError as error:
        assert "forged-field" in str(error)
    else:
        raise AssertionError("expected parse_editor_draft() to reject a forged field id")


def test_parse_editor_draft_preserves_false_and_zero_values(workflow_env):
    env = workflow_env
    env.write_blueprint(
        "typed-values",
        {
            "schema_version": 1,
            "id": "typed-values",
            "name": "Typed values",
            "description": "Preserve typed preconditions",
            "initial_state": "todo",
            "states": [
                {"id": "todo", "name": "Todo", "color": "#9ca3af"},
                {"id": "done", "name": "Done", "color": "#7ad7bf"},
            ],
            "fields": [
                {"id": "approved", "label": "Approved", "type": "boolean"},
                {"id": "score", "label": "Score", "type": "number"},
            ],
            "transitions": [
                {
                    "id": "finish",
                    "name": "Finish",
                    "from_state": "todo",
                    "to_state": "done",
                    "inputs": [
                        {"field_id": "approved", "required": True},
                        {"field_id": "score", "required": False},
                    ],
                    "outputs": [{"field_id": "score", "required": False}],
                    "preconditions": [
                        {"field_id": "approved", "operator": "equals", "value": False},
                        {"field_id": "score", "operator": "equals", "value": 0},
                    ],
                    "criteria": [],
                }
            ],
        },
    )
    source = env.library.inspect("typed-values")

    parsed = parse_editor_draft(source, editor_payload(source))

    assert parsed.transitions[0].preconditions[0].value is False
    assert parsed.transitions[0].preconditions[1].value == 0


def test_parse_editor_draft_rejects_removed_field_still_referenced(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")

    draft = editor_payload(source)
    draft["fields"] = [field for field in draft["fields"] if field["existing_field_id"] != "verdict"]

    try:
        parse_editor_draft(source, draft)
    except ValueError as error:
        assert "Removed field" in str(error)
    else:
        raise AssertionError("expected parse_editor_draft() to reject a dangling field reference")


def test_parse_editor_draft_rejects_precondition_on_output_only_field(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")

    draft = editor_payload(source)
    draft["transitions"][0]["preconditions"][0]["existing_field_id"] = "summary"
    draft["transitions"][0]["preconditions"][0]["value"] = "approved"

    with pytest.raises(ValueError) as exc_info:
        parse_editor_draft(source, draft)

    assert "precondition" in str(exc_info.value).lower()
    assert "input" in str(exc_info.value).lower()


def test_parse_editor_draft_renaming_reused_field_updates_all_uses(workflow_env):
    env = workflow_env
    env.write_blueprint(
        "shared-fields",
        {
            "schema_version": 1,
            "id": "shared-fields",
            "name": "Shared fields",
            "description": "Reuse a field across transitions",
            "initial_state": "backlog",
            "states": [
                {"id": "backlog", "name": "Backlog", "color": "#9ca3af"},
                {"id": "review", "name": "Review", "color": "#ebc77c"},
                {"id": "done", "name": "Done", "color": "#7ad7bf"},
            ],
            "fields": [
                {"id": "summary", "label": "Summary", "type": "text"},
            ],
            "transitions": [
                {
                    "id": "submit",
                    "name": "Submit",
                    "from_state": "backlog",
                    "to_state": "review",
                    "inputs": [{"field_id": "summary", "required": True}],
                    "outputs": [],
                    "preconditions": [],
                    "criteria": [],
                },
                {
                    "id": "finish",
                    "name": "Finish",
                    "from_state": "review",
                    "to_state": "done",
                    "inputs": [{"field_id": "summary", "required": True}],
                    "outputs": [],
                    "preconditions": [],
                    "criteria": [],
                },
            ],
        },
    )
    source = env.library.inspect("shared-fields")
    draft = editor_payload(source)

    draft["fields"][0]["label"] = "Acceptance summary"
    parsed = parse_editor_draft(source, draft)

    assert parsed.fields[0].id == "summary"
    assert parsed.fields[0].label == "Acceptance summary"
    assert parsed.transitions[0].inputs[0].field_id == "summary"
    assert parsed.transitions[1].inputs[0].field_id == "summary"


def test_parse_editor_draft_criterion_rename_keeps_id(workflow_env):
    env = workflow_env
    env.write_blueprint(
        "criteria-flow",
        {
            "schema_version": 1,
            "id": "criteria-flow",
            "name": "Criteria flow",
            "description": "Keep criterion ids stable",
            "initial_state": "review",
            "states": [
                {"id": "review", "name": "Review", "color": "#ebc77c"},
                {"id": "done", "name": "Done", "color": "#7ad7bf"},
            ],
            "fields": [],
            "transitions": [
                {
                    "id": "approve",
                    "name": "Approve",
                    "from_state": "review",
                    "to_state": "done",
                    "inputs": [],
                    "outputs": [],
                    "preconditions": [],
                    "criteria": [
                        {"id": "criterion-a", "description": "Old wording"}
                    ],
                }
            ],
        },
    )
    source = env.library.inspect("criteria-flow")
    draft = editor_payload(source)

    draft["transitions"][0]["criteria"][0]["description"] = "New wording"
    parsed = parse_editor_draft(source, draft)

    assert parsed.transitions[0].criteria[0].id == "criterion-a"
    assert parsed.transitions[0].criteria[0].description == "New wording"


def test_parse_editor_draft_reuses_one_new_field_id_across_transitions(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")
    draft = editor_payload(source)

    draft["fields"].append(
        {
            "key": "df-shared",
            "existing_field_id": None,
            "label": "Shared draft field",
            "type": "text",
        }
    )
    draft["transitions"].append(
        {
            "key": "dt-second",
            "existing_transition_id": None,
            "name": "Recheck",
            "from_state_id": "done",
            "to_state_id": "review",
            "inputs": [
                {
                    "existing_field_id": None,
                    "draft_field_key": "df-shared",
                    "required": True,
                }
            ],
            "outputs": [],
            "preconditions": [],
            "criteria": [],
        }
    )
    draft["transitions"][0]["outputs"].append(
        {
            "existing_field_id": None,
            "draft_field_key": "df-shared",
            "required": False,
        }
    )

    parsed = parse_editor_draft(source, draft)

    created = [field for field in parsed.fields if field.label == "Shared draft field"]
    assert len(created) == 1
    shared_id = created[0].id
    assert parsed.transitions[0].outputs[-1].field_id == shared_id
    assert parsed.transitions[-1].inputs[0].field_id == shared_id


def test_parse_editor_draft_rejects_non_boolean_required_flag(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")
    draft = editor_payload(source)
    draft["transitions"][0]["inputs"][0]["required"] = 1

    with pytest.raises(Exception) as exc_info:
        parse_editor_draft(source, draft)

    assert "required" in str(exc_info.value).lower()