# Pre-Runtime Launch Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover scheduled occurrences that failed before the AI integration was invoked, without replaying work with possible agent side effects.

**Architecture:** Record explicit worker-owned failure evidence in the existing mutable job metadata. Extend the existing dispatcher lost-occurrence predicate to consume that evidence conservatively, retaining normal catch-up and duplicate suppression.

**Tech Stack:** Python, pytest, filesystem-backed YAML jobs, existing job locks and scheduler fixtures.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-09-11-pre-runtime-recovery-design.md`.
- Work only in `.worktrees/fix-pre-runtime-recovery`, branch `fix/pre-runtime-recovery`, based on `ce6226a`.
- Do not change agent prompts, permissions, routine schedules, dependency versions, cleanup tasks, or the UI.
- Do not clear markers, delete job history, or manually start other agents.
- Do not replay older records whose safety cannot be established from explicit evidence.
- Keep worker identity, launch timestamps, terminal status, and existing queue occupancy semantics unchanged.
- Use `result_metadata.execution_failure.phase` with exact value `before_runtime`; do not change the immutable job authority or record schema.
- Cancellation, authority failures, retired triggers, nonzero runtime exits, publication failures, and uncertain outcomes do not acquire retry eligibility.
- Do not authorize replay while ticket cleanup remains pending or unconfirmed.
- Preserve main's staged and unstaged task-file edits and all live configuration/runtime data.
- Manual text edits use apply_patch; no nested agents, simultaneous terminal commands, blanket clean, forced cleanup, or unapproved live runs.
- Capture red and green results separately. One-shot commands use sync mode without timeout; never poll or start a duplicate if moved to background.

## Verification Setup

Before implementation, create a worktree-local Python environment with pip and install `.[test]` into that environment only. Do not repoint the global editable installation. Confirm imports resolve this worktree. Run the complete baseline once from its root:

```powershell
./.venv/Scripts/python.exe -m pytest tests/ -q --tb=short --junitxml=.superpowers/sdd/2026-09-11-pre-runtime-recovery/baseline.xml
```

The existing live tests use isolated fixtures; they are not permission to rerun the user's agents. Preserve any baseline failure and resolve environment blockers before editing production code.

### Task 1: Recover Explicit Pre-Runtime Failures

**Files:**
- Modify: `flowgency/jobs/execution.py`
- Modify: `flowgency/dispatch/run.py`
- Test: `tests/test_job_execution.py`
- Test: `tests/test_dispatch_launch_recovery.py`

**Interfaces:**
- Consume existing `execute_job`, `_terminalize_failure`, `_merge_result_metadata`, `lost_occurrences`, `_due_occurrence`, and `run_dispatch_cycle`.
- Produce persisted `{"execution_failure": {"phase": "before_runtime"}}` only for confirmed failures before invocation.
- No new public API, schema, module, or test file is needed.

- [ ] **Step 1: Add and run the smallest failing worker regression.**

Use `make_ticket_job_environment`, `_workflow_team_manual_authority`, and `_patch_workflow_execution_context` in the existing execution test file. Inject an `ImportError` at `TicketBroker.start`, keep the real worker, authority, job store, and terminal writes, and give the integration a `run` that fails the test if invoked. Execute the job and assert:

```python
assert result.status == "failed"
assert stored.worker_pid is not None
assert stored.result_metadata["execution_failure"] == {"phase": "before_runtime"}
assert stored.authority_digest == authority.immutable_digest
```

Run only this new test. The intended red result is absent failure metadata, not fixture construction or unrelated validation failure.

- [ ] **Step 2: Record failure evidence at the existing worker boundary.**

Initialize `runtime_invoked = False` before the outer execution try. Set it immediately before the actual integration call:

```python
runtime_invoked = True
result = integration.run(request)
```

After the existing ordinary-exception handler terminalizes the failure, merge evidence using the existing locked metadata helper only when invocation did not occur and the returned record is failed:

```python
if not runtime_invoked and final.status == "failed":
    final = _merge_result_metadata(
        job_path,
        {"execution_failure": {"phase": "before_runtime"}},
    )
```

Keep the separate authority/cancellation/retired paths ineligible. A missing metadata write remains conservatively non-retryable. Preserve unrelated metadata rather than replacing the whole mapping. Immediately rerun the same focused regression.

- [ ] **Step 3: Add and run the scheduler regression.**

Extend the existing `_record` fixture with optional metadata, retaining its current callers. A launched failed record with exact evidence must reopen the original occurrence:

```python
record = _record(
    "audit", "2026-07-29T08:00:00", "failed",
    launched_at="2026-07-29T08:00:01", worker_pid=12345,
)
record.result_metadata = {"execution_failure": {"phase": "before_runtime"}}
assert lost_occurrences([record]) == {
    ("builder", "audit"): datetime(2026, 7, 29, 8, 0),
}
```

Run this test before changing dispatch; it must fail against the current worker-only predicate.

- [ ] **Step 4: Extend the existing lost-occurrence predicate conservatively.**

Retain the failed-status and original unlaunched-worker checks. For a launched worker, require a dictionary metadata mapping and an `execution_failure` mapping with exact phase `before_runtime`. Unknown, missing, or malformed evidence returns false. If `ticket_cleanup` exists, require a dictionary with confirmed termination, no pending cleanup, no retry requirement, and a settled `idle` or `cleared` status. An error or ambiguous cleanup result must not authorize replay. Use exact Boolean checks, not truthiness of untrusted/malformed metadata.

Keep `lost_occurrences` grouping, scheduled-only filtering, `_due_occurrence` anchor handling, and normal catch-up controls unchanged. Update existing docstrings that currently equate worker startup with execution, without adding unrelated comments. Immediately rerun the scheduler regression.

- [ ] **Step 5: Cover positive recovery and negative replay boundaries.**

Use the existing `_Bench` fixtures to submit a scheduled job, run the real worker with a controlled pre-runtime setup failure, and call the real dispatcher again. Parameterize `at` and `every` cases. Assert two durable jobs, retained original failure, only one recovered launch, no third submission after another cycle, and the original schedule anchor. Keep real submission, queue, job serialization, and marker operations; replace only the external launcher/runtime or injected setup failure.

Add focused cases for catch-up expiration, a later pending/launched occurrence suppressing an old loss, and repeated setup failure remaining recoverable. Worker tests must also prove an integration exception, a nonzero exit, a memory-publication error, cancellation, and an authority failure do not acquire the new evidence. Extend existing cases where practical.

Scheduler negative cases must cover missing metadata on historical launched failures, metadata of the wrong type, unknown phases, manual triggers, cancelled/complete statuses, pending cleanup, unconfirmed cleanup, cleanup errors, and malformed cleanup fields. Assert existing metadata is preserved when the worker adds evidence.

Run the touched slice:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_job_execution.py tests/test_dispatch_launch_recovery.py tests/test_dispatch_run.py tests/test_dispatch_schedule.py tests/test_job_store_terminal.py tests/test_job_queue.py -q --tb=short
```

- [ ] **Step 6: Verify and commit the complete task.**

Run the complete suite once on the final application code, capturing raw output and JUnit under this plan's ignored workspace. Confirm failures/errors are zero, and confirm installed-runtime cases were actually run rather than silently omitted. Run `git diff --check` and editor diagnostics for changed files. Commit only the four implementation/test files with a Conventional Commit such as `fix(dispatch): recover pre-runtime setup failures`.

Write the task report beside its generated brief: base/tip, exact red/green/full commands and counts, fixture boundaries, safety decisions, and any concerns. Return concise status, commit, test summary, and report path. Do not merge, push, or edit main.

## Review and Integration

The controller performs task review, any scoped fix/rereview, and a whole-branch review. Only after the full worktree gate and review pass, back up and stash main's local changes while preserving index state, fast-forward master, restore the changes, and run the complete main suite. Publish master and `fix/pre-runtime-recovery` atomically once green. Preserve test/review evidence outside the disposable worktree, remove it with ordinary `git worktree remove`, and retain the branch. Never reinterpret or automatically replay the user's older failed jobs as part of this integration.