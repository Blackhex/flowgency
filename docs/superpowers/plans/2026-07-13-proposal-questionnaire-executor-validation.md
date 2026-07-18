# Proposal Questionnaire and Executor Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make proposal decisions validate questionnaire data and dispatch only explicitly writable, human-selected executor agents while supporting open-ended guidance and decision notes.

**Architecture:** Add pure proposal-policy helpers in `agency/proposals.py` and a fail-closed capability lookup in `agency/config.py`. FastAPI routes and the CLI consume those helpers, while existing route code continues to own atomic decision writes and durable job submission. Jinja templates render validated controls and historical-safe answer displays without mutating source proposal files.

**Tech Stack:** Python 3.11+, FastAPI, Starlette form data, Jinja2, PyYAML, pytest

## Global Constraints

- `capabilities.write` defaults to `false`; only the literal boolean `true` grants decision implementation authority.
- New decisions require proposal `execution_agent`; never fall back to `origin_agent` for creation.
- Executor overrides may select only configured, available agents whose integration supports execution and whose config explicitly grants write capability.
- Boolean answers are exactly `approved` or `declined`; `deferred` and `rejected` remain display-only historical values.
- `free-response` and historical `text` questions default to `required: true`; `required: false` permits an empty answer.
- `choice` questions require a non-empty, unique option set and default to `required: true`.
- Validation failures create no decision, mutate no proposal, and submit no job.
- Skip execution only when boolean questions exist, all are declined, and choice answers, open-ended answers, and the decision note contain no substantive guidance.
- Preserve existing atomic decision writes, job rollback behavior, and scheduled/manual prompt behavior.
- Do not infer permissions or executor routing from agent/proposal prose and do not rewrite proposals during reads.

---

### Task 1: Fail-Closed Agent Write Authority

**Files:**
- Modify: `agency/config.py`
- Modify: `agency/app.py`
- Modify: `tests/test_config_normalization.py`
- Modify: `tests/test_proposal_questions.py`

**Interfaces:**
- Produces: `agent_can_write(agents: list[dict], agent_name: str) -> bool`
- Produces: `execution_agent_options(g: dict) -> list[str]` filtered by explicit write authority
- Consumes: normalized agents from `g["_agents_normalized"]`

- [ ] **Step 1: Write failing capability and executor-filter tests**

Add to `tests/test_config_normalization.py`:

```python
from agency.config import agent_can_write


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ({"name": "missing"}, False),
        ({"name": "empty", "capabilities": {}}, False),
        ({"name": "false", "capabilities": {"write": False}}, False),
        ({"name": "string", "capabilities": {"write": "true"}}, False),
        ({"name": "writer", "capabilities": {"write": True}}, True),
    ],
)
def test_agent_can_write_is_explicit_and_fail_closed(agent, expected):
    assert agent_can_write([agent], agent["name"]) is expected


def test_agent_can_write_returns_false_for_unknown_agent():
    assert agent_can_write([{"name": "builder", "capabilities": {"write": True}}], "missing") is False
```

Update `_setup_decision_group()` in `tests/test_proposal_questions.py` so `engineer` explicitly has `capabilities: {write: True}` and add:

```python
def test_executor_options_exclude_agents_without_explicit_write_capability(tmp_path, monkeypatch):
    _setup_decision_group(tmp_path, monkeypatch)
    assert app_mod.execution_agent_options(app_mod.GROUPS["test"]) == ["engineer"]
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m pytest tests/test_config_normalization.py tests/test_proposal_questions.py -k "agent_can_write or executor_options" -v
```

Expected: collection fails because `agent_can_write` does not exist, or the option test includes non-writable agents.

- [ ] **Step 3: Implement the capability helper and filter**

Add to `agency/config.py`:

```python
def agent_can_write(agents: list[dict], agent_name: str) -> bool:
    """Return whether an agent explicitly grants decision write authority."""
    for agent in agents:
        if agent.get("name") == agent_name:
            capabilities = agent.get("capabilities")
            return isinstance(capabilities, dict) and capabilities.get("write") is True
    return False
```

Import it in `agency/app.py`, then update `execution_agent_options()`:

```python
def execution_agent_options(g: dict) -> list[str]:
    options = []
    agents = g.get("_agents_normalized", [])
    for name in g["agents"]:
        try:
            resolve_agent_dir(g, name)
            integration = get_agent_integration(g, name)
            if integration.supports_execution and agent_can_write(agents, name):
                options.append(name)
        except (HTTPException, KeyError):
            continue
    return options
```

- [ ] **Step 4: Run focused and neighboring tests**

Run:

```powershell
python -m pytest tests/test_config_normalization.py tests/test_proposal_questions.py tests/test_execute_decision.py -v
```

Expected: PASS. Existing fixtures that submit decisions must explicitly mark their executor writable.

- [ ] **Step 5: Commit the authority boundary**

```powershell
git add agency/config.py agency/app.py tests/test_config_normalization.py tests/test_proposal_questions.py tests/test_execute_decision.py
git commit -m "feat(config): require explicit agent write capability"
```

---

### Task 2: Pure Proposal Schema and Answer Policy

**Files:**
- Create: `agency/proposals.py`
- Create: `tests/test_proposal_validation.py`

**Interfaces:**
- Produces: `validate_proposal_schema(meta: dict) -> list[str]`
- Produces: `question_option_labels(question: dict) -> list[str]`
- Produces: `validate_answers(questions: list[dict], answers: dict) -> list[str]`
- Produces: `should_execute_decision(questions: list[dict], answers: dict, decision_note: str = "") -> bool`
- All functions are pure and perform no filesystem, HTTP, or job operations.

- [ ] **Step 1: Write failing schema-validation tests**

Create `tests/test_proposal_validation.py` with table-driven tests:

```python
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
        question("historical", "text"),
    )
    assert validate_proposal_schema(meta) == []


def test_question_option_labels_supports_strings_and_mappings():
    assert question_option_labels({"options": ["A", {"label": "B"}]}) == ["A", "B"]
```

- [ ] **Step 2: Run schema tests and verify import failure**

Run:

```powershell
python -m pytest tests/test_proposal_validation.py -k "schema or option_labels" -v
```

Expected: FAIL because `agency.proposals` does not exist.

- [ ] **Step 3: Implement schema validation**

Create `agency/proposals.py` with constants and the complete schema rules:

```python
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
            if not isinstance(raw_options, list) or not raw_options or len(labels) != len(raw_options):
                errors.append(f"Question '{question_id}' requires at least one option with a non-empty label")
            for label in labels:
                if labels.count(label) > 1:
                    duplicate = f"Question '{question_id}' has duplicate option '{label}'"
                    if duplicate not in errors:
                        errors.append(duplicate)
    return errors
```

- [ ] **Step 4: Write failing answer and execution-intent tests**

Append to `tests/test_proposal_validation.py`:

```python
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
```

- [ ] **Step 5: Implement answer and execution-intent validation**

Add to `agency/proposals.py`:

```python
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
```

- [ ] **Step 6: Run all pure policy tests**

Run:

```powershell
python -m pytest tests/test_proposal_validation.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit the pure policy module**

```powershell
git add agency/proposals.py tests/test_proposal_validation.py
git commit -m "feat(proposals): validate questionnaire schema and answers"
```

---

### Task 3: Validated Web Decision Submission and Immutable Notes

**Files:**
- Modify: `agency/app.py`
- Modify: `agency/jobs/prompts.py`
- Modify: `tests/test_proposal_questions.py`
- Modify: `tests/test_execute_decision.py`
- Create: `tests/test_decision_prompts.py`

**Interfaces:**
- Consumes: all functions from `agency.proposals`
- Changes: `build_decision_prompt(proposal_body: str, answers: dict, decision_note: str = "") -> str`
- Adds render context: `proposal_errors: list[str]`, `submitted_answers: dict`, `decision_note: str`
- Persists: `decision_note`, `execution_status: skipped`, and skipped `execution_summary`

- [ ] **Step 1: Write failing prompt and route tests**

Create `tests/test_decision_prompts.py`:

```python
from agency.jobs.prompts import build_decision_prompt


def test_decision_prompt_includes_note_and_decline_semantics():
    prompt = build_decision_prompt("Proposal body", {"approve": "declined"}, "Use the alternate path")
    assert "Decision note:\nUse the alternate path" in prompt
    assert "declined items" in prompt
    assert "deferred" not in prompt.lower()
```

Add route tests using `_setup_decision_group()`:

```python
def test_missing_execution_agent_blocks_get_and_post(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch, explicit_executor=False)
    get_response = client.get("/test/proposals/change")
    post_response = client.post("/test/proposals/change/decide", data={"answer_approve": "approved", "execution_agent": "engineer"})
    assert get_response.status_code == 200
    assert "execution_agent is required" in get_response.text
    assert post_response.status_code == 400
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()


def test_invalid_answers_preserve_submitted_values_without_side_effects(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "deferred", "decision_note": "Keep this note", "execution_agent": "engineer"},
    )
    assert response.status_code == 400
    assert "requires Approve or Decline" in response.text
    assert "Keep this note" in response.text
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()


def test_all_declined_without_guidance_creates_skipped_decision_without_job(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    submitted = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: submitted.append(spec))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "declined", "execution_agent": "engineer"},
        follow_redirects=False,
    )
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert response.status_code == 303
    assert meta["execution_status"] == "skipped"
    assert meta["execution_summary"] == "Execution skipped because all boolean questions were declined and no other guidance was provided."
    assert "execution_job_id" not in meta
    assert submitted == []


def test_declined_with_note_submits_job_and_persists_note(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: captured.append(spec))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "declined", "decision_note": "Implement the alternate path", "execution_agent": "engineer"},
        follow_redirects=False,
    )
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert response.status_code == 303
    assert meta["decision_note"] == "Implement the alternate path"
    assert "Implement the alternate path" in captured[0].prompt_content
```

- [ ] **Step 2: Run tests and verify current behavior fails**

Run:

```powershell
python -m pytest tests/test_decision_prompts.py tests/test_proposal_questions.py tests/test_execute_decision.py -k "prompt_includes_note or missing_execution_agent or invalid_answers_preserve or all_declined or declined_with_note" -v
```

Expected: FAIL because notes, schema/answer validation, and skipped execution are not wired.

- [ ] **Step 3: Extend immutable prompt construction**

Change `build_decision_prompt()` to accept `decision_note=""`, append a `Decision note` section only when non-empty, and replace the historical Defer/Reject sentence with:

```python
note_section = f"\n\nDecision note:\n{decision_note.strip()}" if decision_note.strip() else ""
return (
    "A human has decided this proposal. Act on the decision below.\n\n"
    "Proposal:\n"
    f"{proposal_body.strip()}\n\n"
    "Answers:\n"
    f"{rendered_answers}"
    f"{note_section}\n\n"
    "Execute approved items. Do not implement declined items. Use choice and "
    "open-ended answers plus the decision note as binding implementation guidance. "
    "Do not modify the Agency decision file."
)
```

- [ ] **Step 4: Wire schema, answer, and executor validation into GET/POST**

In `render_proposal_detail()`, compute `proposal_errors = validate_proposal_schema(meta)`, append an executor eligibility error when the declared agent is not in `execution_agent_options(g)`, remove the `origin_agent` fallback, and accept preservation parameters:

```python
def render_proposal_detail(
    request, g, group, slug, *, selected_execution_agent=None,
    submitted_answers=None, decision_note="", decision_error="", status_code=200,
):
    ...
```

In `proposal_decide()`:

1. Validate schema before collecting trusted answers.
2. Build multi-choice answers with `form.getlist()` and all other answers with `form.get()`.
3. Trim `decision_note = str(form.get("decision_note", "")).strip()`.
4. Validate answers and executor eligibility.
5. Re-render HTTP 400 with joined errors and preserved values on any failure.
6. Call `should_execute_decision()`.
7. For execution, create the `JobSpec`, persist pending metadata, atomically write, submit, and preserve the existing rollback.
8. For skip, omit `execution_job_id`, write skipped metadata atomically, and do not call `submit_job()`.
9. Update proposal status only after either successful launch or successful skipped-decision write.

Use this shared metadata base:

```python
meta = {
    "proposal": f"{slug}.md",
    "decided_by": decided_by,
    "date": today,
    "answers": answers,
    "decision_note": decision_note,
    "execution_agent": execution_agent,
    "execution_job_history": [],
}
```

- [ ] **Step 5: Run route, atomicity, and prompt tests**

Run:

```powershell
python -m pytest tests/test_decision_prompts.py tests/test_proposal_validation.py tests/test_proposal_questions.py tests/test_execute_decision.py -v
```

Expected: PASS, including launch rollback and atomic replacement tests.

- [ ] **Step 6: Commit validated submission behavior**

```powershell
git add agency/app.py agency/jobs/prompts.py tests/test_decision_prompts.py tests/test_proposal_questions.py tests/test_execute_decision.py
git commit -m "feat(decisions): validate questionnaires before submission"
```

---

### Task 4: Questionnaire UI and Historical-Safe Decision Display

**Files:**
- Modify: `agency/templates/proposal_detail.html`
- Modify: `agency/templates/decision_detail.html`
- Modify: `agency/app.py`
- Modify: `tests/test_proposal_questions.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes render context from Task 3.
- Displays `decision_note` from decision metadata.
- Adds explicit `Skipped` execution badge.

- [ ] **Step 1: Write failing rendered-HTML tests**

Add tests that assert:

```python
def test_questionnaire_renders_decline_open_text_note_and_executor_override(tmp_path, monkeypatch):
    client, proposal_path, _ = _setup_decision_group(tmp_path, monkeypatch)
    meta, body = app_mod.parse_frontmatter(proposal_path.read_text())
    meta["questions"].append({"id": "detail", "type": "free-response", "prompt": "Details?", "required": False})
    proposal_path.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + body)
    response = client.get("/test/proposals/change")
    assert 'value="declined"' in response.text
    assert "Defer" not in response.text
    assert 'name="answer_detail"' in response.text
    assert 'name="decision_note"' in response.text
    assert '<select id="execution-agent"' in response.text


def test_invalid_schema_disables_questionnaire_submission(tmp_path, monkeypatch):
    client, proposal_path, _ = _setup_decision_group(tmp_path, monkeypatch)
    meta, body = app_mod.parse_frontmatter(proposal_path.read_text())
    meta["questions"] = [{"id": "mode", "type": "choice", "prompt": "Mode?"}]
    proposal_path.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + body)
    response = client.get("/test/proposals/change")
    assert "requires at least one option" in response.text
    assert "Submit All Answers" not in response.text


def test_historical_blank_answer_displays_no_answer_recorded(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    decision_path.write_text("---\nproposal: change.md\nanswers:\n  approve: ''\nexecution_status: skipped\n---\n")
    response = client.get("/test/decisions/change")
    assert "No answer recorded" in response.text
    assert ">Skipped<" in response.text
```

- [ ] **Step 2: Run HTML tests and verify failure**

Run:

```powershell
python -m pytest tests/test_proposal_questions.py tests/test_dashboard.py -k "questionnaire_renders or invalid_schema_disables or historical_blank" -v
```

Expected: FAIL against current controls and blank badges.

- [ ] **Step 3: Update proposal questionnaire template**

Make these exact behavior changes in `proposal_detail.html`:

- Render every `proposal_errors` item in one blocking error panel and do not render the form while errors exist.
- Bind selected/checked state from `submitted_answers`.
- Replace Defer/Reject boolean controls with required Approve/Decline controls.
- Apply `required` only when `q.get('required', true)` is not false.
- Preserve string and list choice selections.
- Add the optional decision-note textarea before the executor selector.
- Keep the `Implement with` selector visible and eligible-only.
- On read-only answers, render `No answer recorded` when an answer is empty; map `declined` to Declined and preserve Deferred/Rejected historical labels.
- Display the persisted decision note below answers when non-empty.

- [ ] **Step 4: Update decision detail display**

Pass `decision_note = meta.get("decision_note", "")` from `render_decision_detail()` and update `decision_detail.html` to:

- Render blank answers as `No answer recorded` before type-specific badges.
- Render `declined` in the red decline style.
- Preserve historical `deferred` and `rejected` labels.
- Render a `Decision note` block when non-empty.
- Render a neutral `Skipped` execution status badge and its summary.
- Never render a retry form for `skipped`.

- [ ] **Step 5: Run template and route tests**

Run:

```powershell
python -m pytest tests/test_proposal_questions.py tests/test_dashboard.py tests/test_execute_decision.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit the questionnaire UI**

```powershell
git add agency/templates/proposal_detail.html agency/templates/decision_detail.html agency/app.py tests/test_proposal_questions.py tests/test_dashboard.py
git commit -m "feat(ui): render validated proposal questionnaires"
```

---

### Task 5: Retry and CLI Policy Parity

**Files:**
- Modify: `agency/app.py`
- Modify: `agency/cli.py`
- Modify: `tests/test_execute_decision.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Retry consumes fail-closed `execution_agent_options()` and includes persisted `decision_note` in rebuilt prompts.
- CLI consumes `validate_proposal_schema()`, `question_option_labels()`, `validate_answers()`, and `should_execute_decision()`.
- CLI consumes the existing `JobSpec`, `build_decision_prompt()`, `atomic_write_text()`, and `submit_job()` APIs so executable decisions create the same durable jobs as browser decisions.

- [ ] **Step 1: Write failing retry tests**

Add to `tests/test_execute_decision.py`:

```python
def test_retry_rejects_read_only_executor(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    decision_path.write_text("---\nproposal: change.md\nexecution_status: failed\nexecution_agent: engineer\n---\n")
    response = client.post("/test/decisions/change/retry", data={"execution_agent": "product"})
    assert response.status_code == 400
    assert "not writable" in response.text


def test_retry_prompt_keeps_decision_note(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    decision_path.write_text("---\nproposal: change.md\nanswers:\n  approve: approved\ndecision_note: Keep rollback\nexecution_status: failed\nexecution_agent: engineer\n---\n")
    captured = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: captured.append(spec))
    response = client.post("/test/decisions/change/retry", data={"execution_agent": "engineer"}, follow_redirects=False)
    assert response.status_code == 303
    assert "Keep rollback" in captured[0].prompt_content
```

- [ ] **Step 2: Implement retry parity and verify it**

Remove retry fallback to proposal `origin_agent`; use persisted decision executor or proposal `execution_agent`. Keep the inline HTML error response, change its message to distinguish unavailable/non-executable/non-writable agents, and pass `meta.get("decision_note", "")` to `build_decision_prompt()`.

Run:

```powershell
python -m pytest tests/test_execute_decision.py -k "retry" -v
```

Expected: PASS.

- [ ] **Step 3: Write CLI policy tests**

Import `JobSubmissionError` from `agency.jobs`, then add this fixture and tests to `tests/test_cli.py`:

```python
def setup_cli_proposal(tmp_path, monkeypatch, *, execution_agent="builder", questions=None):
    group = tmp_path / "group"
    shared = group / "shared"
    for directory in ("proposals", "decisions", "jobs", "logs"):
        (shared / directory).mkdir(parents=True, exist_ok=True)
    (group / "builder").mkdir()
    proposal_path = shared / "proposals" / "change.md"
    proposal_meta = {
        "origin_agent": "observer",
        "execution_agent": execution_agent,
        "status": "proposed",
        "questions": questions or [
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
        ],
    }
    proposal_path.write_text(
        "---\n" + yaml.safe_dump(proposal_meta, sort_keys=False) + "---\n\nProposal body\n",
        encoding="utf-8",
    )
    agents = [
        {
            "name": "builder",
            "integration": "script",
            "integration_config": {"command": "echo ok"},
            "capabilities": {"write": True},
        },
    ]
    runtime_group = {
        "key": "test",
        "name": "Test",
        "path": group,
        "shared": shared,
        "agents": ["builder"],
        "_agents_normalized": agents,
    }
    monkeypatch.setattr(cli, "_resolve_group", lambda args: runtime_group)
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.yaml")
    return Namespace(group="test", slug="change"), shared / "decisions" / "change.md", proposal_path


def test_cmd_decide_rejects_invalid_proposal_schema(tmp_path, monkeypatch, capsys):
    args, _, _ = setup_cli_proposal(tmp_path, monkeypatch, execution_agent="")
    with pytest.raises(SystemExit) as error:
        cli.cmd_decide(args)
    assert error.value.code == 1
    assert "execution_agent is required" in capsys.readouterr().err


def test_cmd_decide_collects_decline_open_answer_and_note(tmp_path, monkeypatch):
    args, decision_path, _ = setup_cli_proposal(
        tmp_path,
        monkeypatch,
        questions=[
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
            {"id": "detail", "type": "free-response", "prompt": "Direction?"},
        ],
    )
    responses = iter(["", "d", "Use the alternate", "Overall note"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job", lambda spec: submitted.append(spec))
    cli.cmd_decide(args)
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["answers"] == {"approve": "declined", "detail": "Use the alternate"}
    assert meta["decision_note"] == "Overall note"
    assert meta["execution_agent"] == "builder"
    assert meta["execution_status"] == "pending"
    assert meta["execution_job_id"] == submitted[0].job_id
    assert "Overall note" in submitted[0].prompt_content


def test_cmd_decide_all_declined_without_guidance_skips_job(tmp_path, monkeypatch):
    args, decision_path, _ = setup_cli_proposal(tmp_path, monkeypatch)
    responses = iter(["", "d", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job", lambda spec: submitted.append(spec))
    cli.cmd_decide(args)
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["execution_status"] == "skipped"
    assert "execution_job_id" not in meta
    assert submitted == []


def test_cmd_decide_submission_failure_removes_decision_and_preserves_proposal(tmp_path, monkeypatch):
    args, decision_path, proposal_path = setup_cli_proposal(tmp_path, monkeypatch)
    responses = iter(["", "a", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    monkeypatch.setattr(cli, "submit_job", lambda spec: (_ for _ in ()).throw(JobSubmissionError("spawn denied", decision_path)))
    with pytest.raises(SystemExit) as error:
        cli.cmd_decide(args)
    assert error.value.code == 1
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()
```

- [ ] **Step 4: Update CLI questionnaire handling**

In `cmd_decide()`:

- Validate proposal schema before prompting and print every error to stderr before exit 1.
- Build eligible choices with `execution_agent_options(g)`, print numbered choices, and prompt for the executor first. An empty response accepts the proposal executor; a number selects another eligible executor. Re-prompt other values and do not offer an implicit origin fallback.
- Prompt booleans as `[a]pprove / [d]ecline`, storing `approved`/`declined`.
- Use `question_option_labels()` so string and mapping options both work.
- Support `free-response` and `text`; re-prompt required questions until non-empty and accept blank optional answers.
- Prompt once for optional `Decision note`.
- Validate collected answers before writing.
- Persist `decision_note` and `execution_agent`.
- For executable decisions, build the same immutable prompt and `JobSpec` used by the web route, atomically write pending decision metadata with `execution_job_id`, call `submit_job()`, delete the decision on `JobSubmissionError`, and leave the proposal status unchanged on failure.
- For skipped decisions, atomically write skipped metadata with the shared summary and no job ID.
- Mark the proposal decided only after successful job submission or successful skipped-decision creation.

- [ ] **Step 5: Run CLI and retry suites**

Run:

```powershell
python -m pytest tests/test_cli.py tests/test_execute_decision.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit cross-entry-point parity**

```powershell
git add agency/app.py agency/cli.py tests/test_execute_decision.py tests/test_cli.py
git commit -m "fix(decisions): enforce executor policy across entry points"
```

---

### Task 6: Configuration Migration, Documentation, and Full Verification

**Files:**
- Modify: `config.yaml`
- Modify: `config.yaml.example`
- Modify: `kb/configuration.md`
- Modify: `kb/data-formats.md`
- Modify: `CLAUDE.md`
- Modify: `examples/code-review-team/README.md`
- Modify: `examples/content-team/README.md`
- Modify: `skills/agency-setup/SKILL.md`
- Modify: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Documents and generates the exact `capabilities.write` and proposal schema consumed by Tasks 1-5.
- Migrates the current Agents group: Builder writable; Advisor and Sentinel read-only.

- [ ] **Step 1: Write failing setup-skill assertions**

Add this contract test to `tests/test_agency_setup_skill.py`:

```python
def test_registration_writes_explicit_fail_closed_agent_capabilities():
        skill = SKILL_PATH.read_text(encoding="utf-8")
        registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
                "### 4.8 Singleton Scheduler Setup", maxsplit=1
        )[0]
        normalized = " ".join(registration.split())

        assert "capabilities.write: true" in normalized
        assert "capabilities.write: false" in normalized
        assert "Never infer write authority for an existing agent" in normalized
        assert "ask the user when a newly generated role is ambiguous" in normalized
```

The corresponding skill change must instruct setup to grant `true` only to newly generated implementation roles, set observational/advisory roles to `false`, ask when a new role is ambiguous, and preserve explicit capability values for existing agents instead of inferring them.

- [ ] **Step 2: Run setup and documentation contract tests**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py tests/test_config_normalization.py -v
```

Expected: FAIL until generated examples and docs declare capabilities.

- [ ] **Step 3: Migrate current and shipped configurations**

Change `config.yaml` agents to:

```yaml
agents:
- name: advisor
  capabilities:
    write: false
- name: builder
  capabilities:
    write: true
- name: sentinel
  capabilities:
    write: false
```

Change `config.yaml.example`, team examples, and agency setup output to explicit full-form agents with `capabilities.write`. Preserve existing integration, path, and integration-config fields when converting any dict or shorthand example.

- [ ] **Step 4: Update user and maintainer documentation**

In `kb/configuration.md` and `CLAUDE.md`, document that:

- Omitted `capabilities.write` means false.
- Only explicit boolean true allows decision implementation.
- This permission does not block scheduled observational runs.
- Shorthand agents are valid but cannot implement decisions.

In `kb/data-formats.md`, update the proposal and decision examples and tables:

- `execution_agent` is required.
- Boolean format is `approved` or `declined`.
- Choice options are mandatory and may be strings or `{label}` mappings.
- `free-response`/`text` and choice questions support `required`, default true.
- `decision_note` is optional.
- `execution_status` includes `skipped`.
- Executor override lists writable agents only.
- Explain the execution-intent decision table.
- Remove all origin-agent fallback and “every decision triggers execution” claims.

- [ ] **Step 5: Run focused and full verification**

Run:

```powershell
python -m pytest tests/test_proposal_validation.py tests/test_proposal_questions.py tests/test_execute_decision.py tests/test_decision_prompts.py tests/test_cli.py tests/test_config_normalization.py tests/test_agency_setup_skill.py -v
python -m pytest tests/ -q
```

Expected: both commands PASS with no failures.

- [ ] **Step 6: Verify repository diff and configuration parse**

Run:

```powershell
python -c "import yaml; yaml.safe_load(open('config.yaml')); yaml.safe_load(open('config.yaml.example')); print('yaml ok')"
git diff --check
git status --short
```

Expected: `yaml ok`, no whitespace errors, and only files named by this plan are modified.

- [ ] **Step 7: Commit migration and documentation**

```powershell
git add config.yaml config.yaml.example kb/configuration.md kb/data-formats.md CLAUDE.md examples/code-review-team/README.md examples/content-team/README.md skills/agency-setup/SKILL.md tests/test_agency_setup_skill.py
git commit -m "docs: define proposal executor capabilities"
```

---

## Final Review Checklist

- [ ] Confirm every spec requirement maps to a task and test above.
- [ ] Confirm no new decision path falls back to `origin_agent`.
- [ ] Confirm omitted write capability is false in code, fixtures, current config, examples, and docs.
- [ ] Confirm malformed proposal GET is non-mutating and POST is side-effect free.
- [ ] Confirm all-declined decisions execute when a choice, open answer, or note provides guidance.
- [ ] Confirm skipped decisions have no job ID and cannot be retried.
- [ ] Confirm historical blank/deferred/rejected answers remain readable.
- [ ] Confirm scheduled and manual prompt execution tests remain unchanged and passing.