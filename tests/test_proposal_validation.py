import pytest

from agency.proposals import (
    question_option_labels,
    should_execute_decision,
    validate_answers,
    validate_proposal_schema,
)


def proposal(*questions, execution_agent="builder"):
    return {"execution_agent": execution_agent, "questions": list(questions)}


def question(question_id="approve", question_type="boolean", **extra):
    return {"id": question_id, "type": question_type, "prompt": "Proceed?", **extra}


@pytest.mark.parametrize(
    ("meta", "message"),
    [
        (proposal(execution_agent=""), "execution_agent is required"),
        (proposal({"type": "boolean", "prompt": "Proceed?"}), "Question 1 requires a non-empty id"),
        (proposal(question("same"), question("same")), "Question id 'same' is duplicated"),
        (proposal({"id": "x", "type": "boolean", "prompt": ""}), "Question 'x' requires a non-empty prompt"),
        (proposal(question(question_type="unknown")), "Question 'approve' has unsupported type 'unknown'"),
        (proposal(question(question_type="choice")), "Question 'approve' requires at least one option"),
        (proposal(question(question_type="choice", options=["A", {"label": "A"}])), "Question 'approve' has duplicate option 'A'"),
    ],
)
def test_validate_proposal_schema_reports_specific_errors(meta, message):
    assert message in validate_proposal_schema(meta)


def test_validate_proposal_schema_accepts_supported_questions():
    meta = proposal(
        question(),
        question("mode", "choice", options=["Repair", {"label": "Replace"}]),
        question("detail", "free-response", required=False),
        question("superseded", "text"),
    )
    assert validate_proposal_schema(meta) == []


def test_question_option_labels_supports_strings_and_mappings():
    assert question_option_labels({"options": ["A", {"label": "B"}]}) == ["A", "B"]


@pytest.mark.parametrize("value", ["", "deferred", "rejected", "yes", None])
def test_boolean_answers_accept_only_approved_or_declined(value):
    errors = validate_answers([question()], {"approve": value})
    assert errors == ["Question 'approve' requires Approve or Decline"]


def test_validate_answers_enforces_declared_choices():
    questions = [question("mode", "choice", options=["Repair", "Replace"])]
    assert validate_answers(questions, {"mode": "Other"}) == ["Question 'mode' has an invalid selection"]
    assert validate_answers(questions, {"mode": "Repair"}) == []


def test_validate_answers_enforces_multi_choice_shape_and_values():
    questions = [question("modes", "choice", options=["A", "B"], multi=True)]
    assert validate_answers(questions, {"modes": "A"}) == ["Question 'modes' requires a list of selections"]
    assert validate_answers(questions, {"modes": ["A", "C"]}) == ["Question 'modes' has an invalid selection"]
    assert validate_answers(questions, {"modes": ["A", "B"]}) == []


def test_open_answers_default_required_and_may_be_optional():
    required = question("detail", "free-response")
    optional = question("context", "text", required=False)
    assert validate_answers([required], {"detail": "  "}) == ["Question 'detail' requires an answer"]
    assert validate_answers([optional], {"context": ""}) == []


@pytest.mark.parametrize(
    ("questions", "answers", "note", "expected"),
    [
        ([question()], {"approve": "approved"}, "", True),
        ([question()], {"approve": "declined"}, "", False),
        ([question(), question("mode", "choice", options=["Repair"])], {"approve": "declined", "mode": "Repair"}, "", True),
        ([question(), question("detail", "free-response", required=False)], {"approve": "declined", "detail": "Use a dedicated venv"}, "", True),
        ([question()], {"approve": "declined"}, "Explain instead", True),
        ([question("mode", "choice", options=["Repair"])], {"mode": "Repair"}, "", True),
    ],
)
def test_should_execute_decision(questions, answers, note, expected):
    assert should_execute_decision(questions, answers, note) is expected