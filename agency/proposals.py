SUPPORTED_QUESTION_TYPES = {"boolean", "choice", "free-response", "text"}
OPEN_QUESTION_TYPES = {"free-response", "text"}


def question_option_labels(question: dict) -> list[str]:
    labels = []
    for option in question.get("options") or []:
        label = option.get("label") if isinstance(option, dict) else option
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
    return labels


def validate_proposal_schema(meta: dict) -> list[str]:
    errors = []
    if not isinstance(meta.get("execution_agent"), str) or not meta["execution_agent"].strip():
        errors.append("execution_agent is required")

    questions = meta.get("questions")
    if not isinstance(questions, list) or not questions:
        return errors + ["Proposal requires at least one question"]

    seen_ids = set()
    for index, item in enumerate(questions, 1):
        if not isinstance(item, dict):
            errors.append(f"Question {index} must be a mapping")
            continue
        question_id = item.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            errors.append(f"Question {index} requires a non-empty id")
            continue
        question_id = question_id.strip()
        if question_id in seen_ids:
            errors.append(f"Question id '{question_id}' is duplicated")
        seen_ids.add(question_id)
        if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
            errors.append(f"Question '{question_id}' requires a non-empty prompt")
        question_type = item.get("type")
        if question_type not in SUPPORTED_QUESTION_TYPES:
            errors.append(f"Question '{question_id}' has unsupported type '{question_type}'")
            continue
        if question_type == "choice":
            raw_options = item.get("options")
            labels = question_option_labels(item)
            if not isinstance(raw_options, list) or not raw_options:
                errors.append(f"Question '{question_id}' requires at least one option")
            elif len(labels) != len(raw_options):
                errors.append(f"Question '{question_id}' requires at least one option with a non-empty label")
            for label in labels:
                if labels.count(label) > 1:
                    duplicate = f"Question '{question_id}' has duplicate option '{label}'"
                    if duplicate not in errors:
                        errors.append(duplicate)
    return errors


def _is_required(question: dict) -> bool:
    return question.get("required", True) is not False


def validate_answers(questions: list[dict], answers: dict) -> list[str]:
    errors = []
    for item in questions:
        question_id = item["id"]
        question_type = item["type"]
        answer = answers.get(question_id)
        if question_type == "boolean":
            if answer not in {"approved", "declined"}:
                errors.append(f"Question '{question_id}' requires Approve or Decline")
        elif question_type == "choice":
            labels = set(question_option_labels(item))
            if item.get("multi"):
                if not isinstance(answer, list):
                    errors.append(f"Question '{question_id}' requires a list of selections")
                elif any(value not in labels for value in answer):
                    errors.append(f"Question '{question_id}' has an invalid selection")
                elif _is_required(item) and not answer:
                    errors.append(f"Question '{question_id}' requires a selection")
            elif answer not in labels:
                if _is_required(item) or answer not in (None, ""):
                    errors.append(f"Question '{question_id}' has an invalid selection")
        elif question_type in OPEN_QUESTION_TYPES:
            if _is_required(item) and (not isinstance(answer, str) or not answer.strip()):
                errors.append(f"Question '{question_id}' requires an answer")
    return errors


def should_execute_decision(questions: list[dict], answers: dict, decision_note: str = "") -> bool:
    boolean_questions = [item for item in questions if item.get("type") == "boolean"]
    if any(answers.get(item["id"]) == "approved" for item in boolean_questions):
        return True
    for item in questions:
        answer = answers.get(item.get("id"))
        if item.get("type") == "choice" and bool(answer):
            return True
        if item.get("type") in OPEN_QUESTION_TYPES and isinstance(answer, str) and answer.strip():
            return True
    if decision_note.strip():
        return True
    return not boolean_questions