# Task 12 Report — Routine Scheduler and Trigger Cutover

## Status
- Completed in worktree `C:\Projects\christag-agency\.worktrees\unified-agent-configuration`
- Base revision: `a2403b1`

## RED Evidence
- Command:
  `.venv\Scripts\python.exe -m pytest tests/test_dispatch_run.py tests/test_agent_run.py tests/test_execute_decision.py tests/test_decision_verify.py -v`
- Initial failure signal:
  - web trigger tests failed because `agency.app` did not expose or use `submit_job_request`
  - dispatch/manual/decision paths were still coupled to prompt-file `JobSpec.create(...)` compatibility flow

## GREEN Evidence
- Command:
  `.venv\Scripts\python.exe -m pytest tests/test_dispatch_run.py tests/test_agent_run.py tests/test_execute_decision.py tests/test_decision_verify.py -v`
- Result:
  `55 passed in 3.38s`

## Full-Suite Evidence
- Command:
  `.venv\Scripts\python.exe -m pytest tests/ -q`
- Result:
  `951 passed, 3 skipped in 42.45s`

## Files Changed
- `agency/app.py`
- `agency/cli.py`
- `agency/dispatch/run.py`
- `agency/jobs/prompts.py`
- `agency/templates/agents.html`
- `tests/test_agent_run.py`
- `tests/test_agent_status.py`
- `tests/test_cli.py`
- `tests/test_dispatch_run.py`
- `tests/test_execute_decision.py`
- `tests/test_proposal_questions.py`

## What Changed
- Replaced scheduled/manual/decision trigger submission paths with `JobRequest` + `submit_job_request`
- Switched dispatcher to strict canonical `ConfigStore` snapshots and agent routine iteration
- Added routine task-input construction via immutable routine invocation contracts
- Added `d` interval support and stable marker naming keyed by agent + routine id
- Preserved decision/retry transaction semantics while removing request memory overrides for decision triggers
- Updated focused and broad tests from superseded prompt/dispatch authority to strict canonical routine semantics

## Self-Review
- Confirmed markers are only touched after successful submission
- Confirmed scheduled/manual jobs require existing routines through request resolution
- Confirmed decision and retry requests submit with `routine_id=None`, `memory_override=None`
- Confirmed superseded prompt-based tests were migrated to canonical authority instead of reintroducing compatibility behavior
- Kept UI surface changes limited to existing routine-backed behavior needed for trigger cutover; no broader route UX changes made

## Concerns
- Prompts-page editing still remains partially superseded in broader product design terms; this task only changed ownership where required for the cutover and left larger UI removal for later work
- `tests/test_decision_verify.py` did not require direct edits because the cutover path remained behaviorally compatible there

## Review Fix 1

### Scope
- Fixed the routine argument propagation gap in strict canonical config, request resolution, and manual/scheduled trigger task-input rendering.

### Root Cause
- `Routine` accepted extra fields but did not model `arguments`, so parsed config kept them as untyped extras instead of part of the immutable routine contract.
- `resolve_job_request()` always snapshotted `skill_arguments=()` even when a routine had arguments.
- Manual and scheduled trigger builders rendered `build_routine_task_input(routine_id)` without passing routine arguments.

### Changes
- Added `Routine.arguments: tuple[str, ...] = ()` in the strict canonical model.
- Added unified validation for routine `arguments` shape and values:
  - field must be a list
  - each item must be a string
  - empty strings are rejected as `invalid-routine-argument`
- Updated model preparation to preserve ordered arguments as tuples.
- Updated `resolve_job_request()` to snapshot `routine.arguments` into `JobSpec.skill_arguments`.
- Updated manual and scheduled request builders to render immutable task input with the routine arguments already embedded.
- Left decision and retry flows unchanged with `routine_id=None`, `skill=None`, and empty args.

### Evidence
- Focused config/trigger/job validation:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py tests/test_job_submission.py tests/test_dispatch_run.py tests/test_agent_run.py -q`
  - Result: `158 passed in 3.37s`
- Task 12 regression validation:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_dispatch_run.py tests/test_agent_run.py tests/test_execute_decision.py tests/test_decision_verify.py -v`
  - Result: `57 passed in 2.99s`
- Full suite validation:
  - Command: `.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: `959 passed, 3 skipped in 44.42s`

### Notes
- Immutable task input now preserves argument order and exact text at submission time.
- Post-submission config edits do not change persisted `JobSpec.skill_arguments` or stored `task_input`.

## Review Fix 2

### Scope
- Removed the final jobs-layer superseded compatibility submission path and prompt-source authority inference.
- Migrated remaining production/test callers to `submit_job_request(JobRequest, launcher=None)` or explicit immutable `JobSpec(...)` snapshots for pure store/execution fixtures.

### Root Cause
- `agency.jobs` still exported public `submit_job(JobSpec, ...)`, and `agency.jobs.submission` still converted `JobSpec` back into intent for re-resolution.
- `JobRequest` still accepted `superseded_prompt_source`, and `JobSpec.create()` still performed routine/group inference and placeholder coercion from prompt metadata.
- Several production-adjacent and runtime tests still depended on those compatibility shims instead of strict canonical requests or explicit resolved snapshots.

### Changes
- Removed public `submit_job` from `agency/jobs/submission.py` and `agency/jobs/__init__.py`.
- Kept private `_submit_resolved(JobSpec, launcher)` as the resolved-snapshot persistence boundary.
- Removed `JobRequest.superseded_prompt_source` and `JobRequest.from_superseded_prompt()`.
- Removed `JobSpec.create()` and all jobs-layer compatibility helpers: routine inference, group-path inference, placeholder blueprint/runtime/memory coercion, and compat-only validation bypass.
- Kept `prompt_source` only as explicit resolver-produced audit metadata.
- Updated `agency/jobs/resolution.py` so prompt metadata is resolver-owned only and never influences routine/skill authority.
- Removed the stale `submit_job` production import from `agency/app.py`.
- Migrated runtime/store/execution tests to either:
  - `JobRequest` + `submit_job_request(...)` for submission behavior, or
  - explicit immutable `JobSpec(...)` fixtures for pure serialization/store/execution/recovery coverage.
- Added structural regression coverage for:
  - no public `submit_job` export,
  - no `JobSpec.create()` constructor,
  - no `superseded_prompt_source` on `JobRequest`,
  - no prompt-source path inference for routine/skill resolution.

### Evidence
- Focused model/submission regression:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_job_models.py tests/test_job_submission.py -q`
  - Result: `48 passed in 1.54s`
- Requested Task 10 + Task 12 suites:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_job_models.py tests/test_job_submission.py tests/test_dispatch_run.py tests/test_agent_run.py tests/test_execute_decision.py tests/test_decision_verify.py -v`
  - Result: `105 passed in 4.79s`
- Broader job runtime suites:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_job_execution.py tests/test_job_reconciliation.py tests/test_job_detached_process.py tests/test_job_systemd_integration.py tests/test_memory_publication.py tests/test_memory_recovery.py -v`
  - Result: `47 passed, 1 skipped in 10.79s`
- Full suite:
  - Command: `.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: `962 passed, 3 skipped in 50.73s`

### Notes
- Manual and scheduled triggers now require explicit `routine_id` through request resolution; no filename/path inference remains under `agency/jobs`.
- Decision and retry execution remain routine-less (`routine_id=None`) and continue to preserve Task 10 immutable submission behavior.

## Review Fix 3

### Scope
- Removed the superseded prompts-page POST route that rebuilt and saved `dispatch.agents`.
- Removed prompts-page schedule-assignment controls so the page is browse/edit only for prompt files.

### Root Cause
- `POST /{group}/prompts/dispatch` was still registered in production and still mutated group config by reconstructing `dispatch.agents` from form fields.
- The prompts template still rendered the scheduling form, inline assignment rows, and save controls, so the page implied mutation authority even though strict canonical rejects and routines replace that path.

### Changes
- Replaced the live `/{group}/prompts/dispatch` handler with a hard `HTTPException(404, "Prompts dispatch scheduling is not available")`.
- Removed the prompts-page dispatch form, assignment editor, save bar, and all schedule-mutation JavaScript from `agency/templates/prompts.html`.
- Updated the prompts-page regression coverage to assert the page is read-only, the dispatch POST returns 404, and strict canonical config bytes remain unchanged.

### Evidence
- Focused prompts regression:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_agent_run.py -k "prompts" -v`
  - Result after fix: `5 passed in 2.67s`
- Adjacent admin/web regression:
  - Command: `.venv\Scripts\python.exe -m pytest tests/test_admin_dispatch.py tests/test_admin_dispatch_xss.py tests/test_agent_run.py -v`
  - Result: `31 passed in 1.56s`
- Full suite:
  - Command: `.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: `964 passed, 3 skipped in 42.46s`

### Notes
- The prompts page still renders prompt browsing and prompt-file editing routes only; it no longer advertises or performs schedule assignment mutation.
- There is no production code path left in this task that writes `dispatch.agents` from the prompts page.
