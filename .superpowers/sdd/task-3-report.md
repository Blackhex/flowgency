# Task 3 Report: Validated Web Decision Submission and Immutable Notes

## Commit

SHA: `904c23e`
Subject: `feat(decisions): validate questionnaires before submission`

---

## RED/GREEN Commands and Results

### RED Phase

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_decision_prompts.py tests/test_proposal_questions.py tests/test_execute_decision.py -k "prompt_includes_note or missing_execution_agent or invalid_answers_preserve or all_declined or declined_with_note" -v
```

Result: **5 failed, 24 deselected**

- `test_decision_prompt_includes_note_and_decline_semantics` — FAILED: prompt missing note section and "declined items" text
- `test_missing_execution_agent_blocks_get_and_post` — FAILED: GET returned 200 without error; POST accepted the request
- `test_invalid_answers_preserve_submitted_values_without_side_effects` — FAILED: no answer validation, no preserved note
- `test_all_declined_without_guidance_creates_skipped_decision_without_job` — FAILED: `execution_status` was `pending`, not `skipped`
- `test_declined_with_note_submits_job_and_persists_note` — FAILED: `KeyError: 'decision_note'` in meta

All failures were caused by the correct missing behavior, not test bugs.

### GREEN Phase (focused tests)

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_decision_prompts.py tests/test_proposal_validation.py tests/test_proposal_questions.py tests/test_execute_decision.py -v
```

Result: **53 passed**

### Full Suite

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/ -q
```

Result: **568 passed, 1 skipped** (1 flaky pre-existing failure in `test_job_detached_process.py::test_detached_worker_survives_submitter_exit` — passes when run in isolation, races in full suite due to process timing; confirmed pre-existing, unrelated to this task). Baseline was 563 passed, 1 skipped; this task adds 5 new passing tests.

---

## Atomicity and Rollback Verification

The existing atomic write tests all pass:

- `test_decide_creates_decision_via_atomic_replace` — PASSED: confirms `atomic_write_text` → `os.replace` pattern for new decisions (execution path)
- `test_retry_updates_decision_via_atomic_replace` — PASSED: confirms retry uses atomic replace
- `test_retry_launch_failure_restores_decision_via_atomic_replace` — PASSED: confirms rollback also uses atomic replace
- `test_launch_failure_rolls_back_new_decision` — PASSED: `submit_job` failure unlinks the decision file, proposal stays `proposed`
- `test_retry_launch_failure_restores_original_decision_text` — PASSED: retry rollback restores pre-retry text

For the new **skipped** path (`should_execute_decision` returns False):
- Decision is written atomically via `atomic_write_text` (same call site as execution path)
- No `submit_job` call, so no rollback logic needed
- Proposal status is updated after the write succeeds (same ordering as execution path)
- If `atomic_write_text` raises, the proposal status is never updated — correct behavior

For the new **execution with decision_note** path:
- `decision_note` is included in the shared metadata base before branching
- `build_decision_prompt(proposal_body, answers, decision_note)` embeds the note in the immutable prompt snapshot
- On `JobSubmissionError`, the decision file is unlinked (rollback), same as before

---

## Files Changed

| File | Change |
|------|--------|
| `agency/jobs/prompts.py` | `build_decision_prompt` gains optional `decision_note` param; replaces deferred/rejected semantics with "declined items" phrasing; appends `Decision note:` section when non-empty |
| `agency/app.py` | Imports `validate_proposal_schema`, `validate_answers`, `should_execute_decision` from `agency.proposals`; `render_proposal_detail` gains `submitted_answers`, `decision_note` params, computes `proposal_errors` from schema + eligibility check, removes `origin_agent` fallback; `proposal_decide` fully rewritten with schema→answers→executor validation order, shared metadata base, execution vs skip branching |
| `agency/templates/proposal_detail.html` | Adds `proposal_errors` panel (shown on GET and POST); adds `decision_note` textarea with preserved value |
| `tests/test_decision_prompts.py` | **Created** — `test_decision_prompt_includes_note_and_decline_semantics` |
| `tests/test_proposal_questions.py` | Added `test_missing_execution_agent_blocks_get_and_post`, `test_invalid_answers_preserve_submitted_values_without_side_effects` |
| `tests/test_execute_decision.py` | Added `test_all_declined_without_guidance_creates_skipped_decision_without_job`, `test_declined_with_note_submits_job_and_persists_note` |

---

## Review Fix

### Findings addressed

**Finding 1 — Ineligible declared executor not blocked on POST:**
`proposal_decide` validated only the submitted form executor, not the declared `execution_agent` in proposal metadata. A proposal with `execution_agent: "sdk-agent"` (ineligible) could be decided by submitting a valid `"engineer"` executor.

**Finding 2 — `JobSubmissionError` re-render lost user input:**
The `except JobSubmissionError` handler called `render_proposal_detail` without `submitted_answers=answers` or `decision_note=decision_note`. Both were lost on launch failure. The template also never pre-selected boolean radio buttons from `submitted_answers`.

---

### RED Phase

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py::test_ineligible_declared_executor_blocks_post_with_eligible_submitted_executor tests/test_execute_decision.py::test_launch_failure_preserves_submitted_answers_and_note_in_rerender -v
```

Result: **2 failed**

- `test_ineligible_declared_executor_blocks_post_with_eligible_submitted_executor` — FAILED: POST returned 200 (decision created and redirect followed); expected 400
- `test_launch_failure_preserves_submitted_answers_and_note_in_rerender` — FAILED: "Important guidance" not in response.text (decision_note not passed to re-render)

---

### GREEN Phase (focused)

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py tests/test_execute_decision.py tests/test_decision_prompts.py tests/test_proposal_validation.py -v
```

Result: **55 passed**

---

### Full Suite

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/ -q
```

Result: **571 passed, 1 skipped** (same pre-existing flaky skip as before; baseline was 568 passed, 1 skipped; this fix adds 2 new passing tests)

---

### Files Changed

| File | Change |
|------|--------|
| `agency/app.py` | Added declared executor eligibility check (step 1b) in `proposal_decide` after schema validation; passed `submitted_answers=answers` and `decision_note=decision_note` in `JobSubmissionError` handler |
| `agency/templates/proposal_detail.html` | Boolean radio buttons pre-select from `submitted_answers` via `{% if submitted_answers.get(q.id) == 'value' %}checked {% endif %}` |
| `tests/test_proposal_questions.py` | Added `test_ineligible_declared_executor_blocks_post_with_eligible_submitted_executor` |
| `tests/test_execute_decision.py` | Added `test_launch_failure_preserves_submitted_answers_and_note_in_rerender` |

### Commit

SHA: `cded71e`
Subject: `fix(decisions): enforce declared executor eligibility`

---

### Self-Review

- Declared executor check mirrors the same condition already used in `render_proposal_detail` — no duplication, consistent UX between GET and POST
- Check is positioned after schema validation (step 1b) and before answer/executor side effects — satisfies implementation constraint
- `submitted_answers` and `decision_note` now passed in all three 400-return paths in `proposal_decide` (schema error, declared executor ineligible, answer/submitted-executor errors) plus the `JobSubmissionError` handler
- Template change is minimal: only boolean radio buttons, matching the single question type used in all existing/new tests; choice and free-response could be extended separately if needed

### Correctness
- Schema validation runs before trusting form answers — protects against invalid proposal metadata being used as a trust boundary
- Answer validation uses `validate_answers` from `agency.proposals` (Task 2) — correct per brief
- `should_execute_decision` determines execution vs skip — correct per brief
- Skipped decisions have `execution_status: skipped`, `execution_summary`, no `execution_job_id`, no `submit_job` call — all verified by test
- `decision_note` is in the shared metadata base → persisted for both execution and skip paths
- `build_decision_prompt` passes `decision_note` → note appears in immutable job prompt
- No side effects on validation failure: proposal status stays `proposed`, no decision file created

### Backward Compatibility
- `build_decision_prompt` adds an optional `decision_note=""` parameter — the retry route's call `build_decision_prompt(proposal_body, meta.get("answers", {}))` still works without the note (note not included in retry prompts, which is acceptable)
- All existing `test_execute_decision` tests pass unchanged
- All existing `test_proposal_questions` tests pass unchanged

### Policy Enforcement
- Invalid declared executor in proposal metadata (even if another eligible agent exists) is a blocking schema error — verified by `test_missing_execution_agent_blocks_get_and_post`
- `execution_agent_options` only returns agents with explicit `write` capability — unchanged from Task 1

### Template
- Minimal changes only — `proposal_errors` section and `decision_note` textarea added
- `submitted_answers` is rendered for boolean controls; choice and open-answer preservation remain Task 4 scope
- The `decision_error` existing panel is preserved for form-level errors

---

## Concerns

1. **`test_job_detached_process.py` flakiness**: This test races in the full suite on Windows due to process scheduling. It was already present in the baseline and passes in isolation. Not caused by Task 3 changes.

2. **Retry prompt lacks decision_note**: The retry route calls `build_decision_prompt(proposal_body, meta.get("answers", {}))` without the stored `decision_note`. The note is in the decision meta but not re-injected into the retry prompt. This is acceptable for now (retry re-runs same decision), but Task 4 or a future task may want to pass `meta.get("decision_note", "")` to the retry prompt for completeness.

3. **Partial `submitted_answers` preservation**: Boolean preservation is now rendered for both `approved` and `declined`. Choice and open-answer preservation remain Task 4 scope.

4. **`decision_note` not preserved on schema error re-render**: When the schema check blocks the POST (invalid proposal metadata), `render_proposal_detail` computes `proposal_errors` fresh from the proposal file — which is correct. The `decision_note` from the form is passed through to `render_proposal_detail` and preserved in the textarea.

---

## Review Fix 2: Align Boolean Decision Controls

### Finding addressed

The unanswered proposal form emitted `deferred` and `rejected`, while server validation accepts only `approved` and `declined`. The form now renders exactly Approve (`approved`) and Decline (`declined`), preserves either submitted boolean value, and keeps one required radio in each group. Dead deferred/rejected preselection branches were removed.

### RED Phase

Command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py::test_unanswered_boolean_form_only_offers_approve_and_decline -v
```

Exact result:
```text
tests/test_proposal_questions.py::test_unanswered_boolean_form_only_offers_approve_and_decline FAILED [100%]
E       assert 'value="declined"' in response.text
============================== 1 failed in 0.85s ==============================
```

The test failed for the intended missing behavior: the unanswered form did not contain the server-supported `declined` value.

### GREEN Phase

Focused command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py::test_unanswered_boolean_form_only_offers_approve_and_decline -v
```

Exact focused result:
```text
tests/test_proposal_questions.py::test_unanswered_boolean_form_only_offers_approve_and_decline PASSED [100%]
============================== 1 passed in 0.80s ==============================
```

Integration command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py tests/test_execute_decision.py tests/test_decision_prompts.py tests/test_proposal_validation.py -v
```

Exact integration result:
```text
============================= 56 passed in 1.98s ==============================
```

Whitespace command:
```powershell
git diff --check
```

Exact result: exit code 0 with no output.

### Files Changed

| File | Change |
|------|--------|
| `agency/templates/proposal_detail.html` | Replaced Defer/Reject form controls with one Decline control using `value="declined"` and submitted-value preselection |
| `tests/test_proposal_questions.py` | Added an unanswered-form route regression test for approved/declined values and removal of deferred/rejected/Defer |
| `.superpowers/sdd/task-3-report.md` | Added RED/GREEN evidence and corrected the submitted-answer preservation concern |

### Self-Review

- The boolean form has exactly two controls: Approve and Decline.
- Both controls restore checked state from `submitted_answers`; only the Approve radio carries `required`, which makes the shared radio group required.
- No deferred/rejected form values or preselection branches remain.
- Choice and free-response rendering were not changed; their preservation remains Task 4 scope.
- The read-only rendering of historical decision values was left unchanged because this finding concerns active form controls and server-valid submissions.

---

## Review Fix 3

### Finding addressed

`test_missing_execution_agent_blocks_get_and_post` now asserts that the POST response HTML visibly contains the exact schema error `execution_agent is required`, in addition to asserting the 400 status. No production behavior changed; the existing POST error rendering already satisfied the assertion.

### Verification

Focused command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py::test_missing_execution_agent_blocks_get_and_post -v
```

Exact focused result:
```text
tests/test_proposal_questions.py::test_missing_execution_agent_blocks_get_and_post PASSED [100%]
============================== 1 passed in 0.65s ==============================
```

Proposal-question suite command:
```powershell
$env:PYTHONPATH = $PWD.Path; C:\Projects\christag-agency\.venv\Scripts\python.exe -m pytest tests/test_proposal_questions.py -v
```

Exact suite result:
```text
============================= 16 passed in 0.85s ==============================
```

Whitespace command:
```powershell
git diff --check
```

Exact result: exit code 0 with no output.

### Files Changed

| File | Change |
|------|--------|
| `tests/test_proposal_questions.py` | Added the exact visible POST schema-error assertion |
| `.superpowers/sdd/task-3-report.md` | Added Review Fix 3 verification evidence and self-review |

### Self-Review

- The assertion checks the user-visible POST response body, not only the HTTP status.
- The expected message exactly matches the schema validation error already asserted for GET.
- The focused test passed immediately after the assertion was added, confirming this is review-driven coverage of existing behavior rather than a new TDD behavior change.
- No junction or production code was touched.
