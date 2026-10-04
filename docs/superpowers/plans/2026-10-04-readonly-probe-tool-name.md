# Read-Only Ticket Probe Tool Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this approved prerequisite task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Request the measured Copilot-visible read tool explicitly without weakening the actual restricted-ticket acceptance check.

**Architecture:** Modify only the Copilot-specific live task generator and add its neighboring offline regression. Production launch, model, grants, transport, and all live assertions remain untouched; the controller validates the naming hypothesis through the existing real broker-read test.

**Tech Stack:** Existing Python/pytest test module and installed Copilot CLI.

## Global Constraints

- Work from `C:/Projekty/Flowgency/.worktrees/workflow-live-reconciliation` on `feature/workflow-live-reconciliation`, never the primary checkout.
- Modify only `tests/test_ticket_runtime_live.py` for the task; no application/configuration/SDK/dependency/model/permission/transport changes.
- Preserve every existing live successful-read, scoped-reference, MCP inventory, protected-file, no-write, and no-active-work assertion and every existing test marker.
- Do not add skips, retries, mocks of live success, weakened assertions, or alternative acceptance.
- Keep the generated task's reference and prohibitions on other ticket/shell/git/write operations.
- Use `.venv/Scripts/python.exe` explicitly from the worktree root; the delegate runs offline checks only, never a paid live probe or the full suite.
- First substantive edit is a failing offline regression; the very next action is its narrow execution. Then make the smallest helper-wording change and rerun the same check.
- Commit this specification and plan separately before implementation; commit the test correction atomically with a Conventional Commit message.
- Do not read or write a sibling SDD plan's artifacts. This prerequisite has its own ledger, brief, report, and review package.

## Task 1: Qualify The Read-Only Tool In The Existing Task Generator

**Files:** Modify `tests/test_ticket_runtime_live.py` only. No new test file.

**Interfaces:** Preserve `_read_only_agent_task(ref_json: str) -> str`; its output names `flowgency-tickets-ticket_get` in the declaration and the call step, with the exact supplied reference and existing read-only constraints.

- [ ] **Step 1: Add a neighboring failing offline task-generator regression.**

Place it after the helper and before the next task-generator helper, outside any live-runtime conditional. Use the module's existing json import:

```python
def test_read_only_agent_task_names_the_copilot_visible_tool():
    ref_json = json.dumps({
        "binding_id": "fixture-binding",
        "team_id": "newsletter",
        "workflow_id": "board-a",
        "ticket_id": "ticket-name-probe",
    })
    task = _read_only_agent_task(ref_json)

    assert "flowgency-tickets-ticket_get tool" in task
    assert "1. Call the flowgency-tickets-ticket_get tool" in task
    assert ref_json in task
    assert "do not call any other ticket" in task
    assert "tool or any shell, git, or write tool at any point" in task
    assert "Do not attempt any workspace write" in task
    assert "do not call any mutating ticket tool" in task
```

The regression must fail when the qualified call name is reverted, not depend
on a live CLI result or a mocked tool call. Preserve unrelated module content.

- [ ] **Step 2: Run the new offline regression red.**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_ticket_runtime_live.py -q -k read_only_agent_task
```

Expected: one failed offline regression because the qualified tool name is
absent. If it fails because of fixture/import/setup errors, fix the test setup
before changing the helper. Do not execute the live target from a delegate.

- [ ] **Step 3: Make the smallest approved generator edit.**

Replace only the generic name in the declaration and call step:

```python
        'flowgency-tickets-ticket_get tool exposed by the "flowgency-tickets" MCP server. Do not '
```

```python
        "1. Call the flowgency-tickets-ticket_get tool with that exact ref to load the ticket and its "
```

Preserve the rest of the generated task verbatim. Do not add a discovery ban,
fallback, retry, extra grant, or unrelated instruction.

- [ ] **Step 4: Rerun the same regression green.**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_ticket_runtime_live.py -q -k read_only_agent_task
```

Expected: one pass; retain actual red/green output and exit codes in the report.
Run the neighboring non-live task-generator test as a focused compatibility
check if it exists, without collecting paid live scenarios as executed tests.
Check editor diagnostics and the scoped diff.

- [ ] **Step 5: Self-review and commit only the corrected test module.**

Verify the actual scope, unchanged live assertions and markers, and no other
generator edits. Commit as `test(copilot): qualify the readonly ticket tool`.
Write the full report with red/green chronology and scope checks in this plan's
ignored workspace; return only status, commit, test result, concerns, and path.

- [ ] **Step 6: Task review before the controller's strict live check.**

The controller records BASE before delegation, generates the BASE..HEAD review
package, and dispatches a read-only reviewer with the task brief, report, and
binding global constraints. Require both spec compliance and quality approval.
Reviewers must not launch paid tests. Use the existing scoped fix loop if needed.

- [ ] **Step 7: Controller runs the unchanged real acceptance test.**

```powershell
& .\.venv\Scripts\python.exe -m pytest 'tests/test_ticket_runtime_live.py::test_restricted_agent_reads_ticket_over_http_without_editing[copilot]' -q --junitxml=.superpowers/evidence/workflow-live-reconciliation/readonly-qualified-live.xml
```

Expected: a genuine successful read through the broker and all existing strict
assertions pass. Inspect exit code and JUnit; do not substitute the offline
generator result. If it fails before broker read, the naming hypothesis is
falsified: stop this prerequisite and report the actual failure, without a
blind rerun, weakened assertion, different model, or additional workaround.

- [ ] **Step 8: Return to the original UI baseline gate only after success.**

Run the complete isolated Python suite, then the complete default-headless
browser suite from this worktree, sequentially, with distinct retained receipts.
These gates are controller-owned. Do not treat focused success as a green full
baseline. Once both are green, record this prerequisite's reviewed commit and
resume Task 1 of the already-approved workflow UI plan without a continuation
question. Do not merge/publish/clean up the feature before the complete UI plan,
whole-branch review, and repository integration gates finish.