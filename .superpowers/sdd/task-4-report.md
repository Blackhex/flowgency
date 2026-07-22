# Task 4 Report: Redesign Job Snapshots, Locks, and Execution Paths

## Status
Implemented and verified. Commit: `3e5658b` (`feat(jobs): separate execution and group roots`).

## RED/GREEN evidence

RED:
- Command: `.venv\Scripts\python -m pytest tests\test_job_models.py::test_job_record_round_trips_through_atomic_store -q`
- Result: `1 failed`; expected `TypeError: JobSpec.__init__() got an unexpected keyword argument 'group_root'` before implementation.
- Command: updated focused suite before implementation.
- Result: failed as expected because schema-3 fields and new lock/log contracts were not yet implemented.

GREEN:
- Command: `.venv\Scripts\python -m pytest tests\test_job_models.py tests\test_job_submission.py tests\test_job_execution.py tests\test_job_reconciliation.py tests\test_job_authority.py tests\test_instances.py tests\test_memory_recovery.py tests\test_memory_publication.py tests\test_integration_contract.py tests\test_integration_script.py -q`
- Result: `306 passed in 12.18s`.
- Command: `.venv\Scripts\python -m pytest tests\ -q`
- Result: `1218 passed, 3 skipped in 134.18s`.
- Additional check: `git diff --check` passed.

## Files changed

Production:
- `agency/jobs/models.py`, `resolution.py`, `execution.py`, `store.py`, `reconciliation.py`, `__init__.py`
- `agency/integrations/models.py`, `agency/integrations/agency/copilot.py`, `script.py`
- `agency/memory/recovery.py`
- `agency/app.py`

Tests updated for strict schema-3/root contracts across job, execution, integration, route, dashboard, CLI, decision, roster/status, memory, and UI fixtures.

## Self-review

- `JobSpec` is strict schema version 3, serializes only `workspace_root` and `group_root`, resolves both roots independently, and rejects obsolete payload keys.
- `IntegrationRunRequest` and script placeholders use `workspace_root`; obsolete script placeholders are rejected.
- Job validation and execution use `ResolvedGroupPaths`; prompts and logs are stored under dated `group_root/logs`; old job-authority sibling prompts are not created.
- Operation locks resolve to `group_root/locks/.operations.lock`.
- Reconciliation and memory recovery consume `group_root` consistently.
- Copilot change capture uses `workspace_root`.

## Concerns

None blocking. The strict cutover intentionally rejects retired job payload keys and obsolete script placeholders.
