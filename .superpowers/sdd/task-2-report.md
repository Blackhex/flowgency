# Task 2 Report: Centralize Group Paths, Validation, and Initialization

## Summary
Implemented a central resolved group path model, disjoint authority validation for global/group/workspace roots, safe storage directory initialization, and validate-before-initialize ordering in config save/startup/job submission flows.

## RED / GREEN Evidence

### RED
Command:
```powershell
.venv\Scripts\python -m pytest tests\test_path_validation.py tests\test_config_store.py tests\test_server.py -q
```
Result:
- `8 failed, 41 passed, 1 skipped in 1.90s`
- Expected failures observed:
  - `ModuleNotFoundError: No module named 'agency.configuration.group_paths'`
  - `ImportError: cannot import name 'initialize_storage_directories'`
  - overlap/order tests failing under old validation/initialization semantics

### GREEN (focused)
Command:
```powershell
.venv\Scripts\python -m pytest tests\test_path_validation.py tests\test_config_store.py tests\test_server.py tests\test_job_submission.py -q
```
Result:
- `77 passed, 1 skipped in 2.82s`

### Full suite
Command:
```powershell
.venv\Scripts\python -m pytest tests\ -q
```
Result:
- `89 failed, 1126 passed, 3 skipped, 1 warning in 70.28s`
- Failures are outside this task's focused scope and remain in broader branch/worktree areas (examples: admin dispatch, agent roster/run pages, CLI contract, instances, job routes, memory channel routes).

## Exact implementation changes
- Added `agency/configuration/group_paths.py` with `ResolvedGroupPaths` and `resolve_group_paths()`.
- Rewrote `agency/configuration/paths.py` to:
  - validate workspaces as existing directories
  - validate group roots as creatable control directories
  - compare global stores, group roots, and workspaces as disjoint resolved authorities
  - preserve sandbox/additional-root checks against control stores
  - initialize only group-state record directories (`observations`, `proposals`, `decisions`, `locks`, `logs`)
  - create directories with symlink/reparse-safe component checks
- Switched validate-before-initialize ordering in:
  - `agency/configuration/store.py`
  - `agency/web/dependencies.py`
  - `agency/jobs/submission.py`
- Exported the new path model from `agency/configuration/__init__.py`.
- Added/updated tests in:
  - `tests/test_path_validation.py`
  - `tests/test_config_store.py`
  - `tests/test_server.py`
  - `tests/test_job_submission.py`

## Changed files
- `agency/configuration/group_paths.py`
- `agency/configuration/paths.py`
- `agency/configuration/store.py`
- `agency/configuration/__init__.py`
- `agency/web/dependencies.py`
- `agency/jobs/submission.py`
- `tests/test_path_validation.py`
- `tests/test_config_store.py`
- `tests/test_server.py`
- `tests/test_job_submission.py`

## Self-review
- Verified the new resolved path model has no `shared` segment and initializes only group-state record directories.
- Verified invalid configs no longer create storage during config saves or service startup.
- Verified focused submission/startup/path tests pass after making job-submission fixtures use distinct workspace vs. group-state authorities.
- Reviewed the directory creation helper and tightened it to use per-component real-directory checks with `exist_ok=True` to avoid creation races.

## Concerns
- The full suite still has many unrelated branch/worktree failures outside this task's touched scope.
- Existing full-suite tests in other areas still encode pre-redesign assumptions (including same-path group/workspace setups and older UI/route expectations) and were not mass-updated here.
