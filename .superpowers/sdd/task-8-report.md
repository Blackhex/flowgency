# Task 8 Report

## Status

- Completed in worktree `C:\Projects\christag-agency\.worktrees\unified-agent-configuration`
- Base: `fb539bd`

## RED

- Command: `.\.venv\Scripts\python.exe -m pytest tests/test_memory_selectors.py tests/test_memory_store.py -v`
- Result: failed during collection with `ModuleNotFoundError: No module named 'agency.memory'` in both new test files.

## GREEN

- Command: `.\.venv\Scripts\python.exe -m pytest tests/test_memory_selectors.py tests/test_memory_store.py -v`
- Result: `27 passed`.

## Full Suite

- Command: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
- Result: `869 passed, 3 skipped in 35.72s`.

## Files

- `agency/memory/__init__.py`
- `agency/memory/models.py`
- `agency/memory/selectors.py`
- `agency/memory/store.py`
- `tests/test_memory_selectors.py`
- `tests/test_memory_store.py`

## Self-Review

- Added canonical selector hashing with exact sorted compact UTF-8 JSON and `agency-memory:v1\0` domain separation.
- Enforced effective selector precedence: manual override, routine selector, agent default, implicit run.
- Kept channel selectors global-only by hashing the declared channel key and excluding group, agent, routine, and content.
- Implemented flat canonical layout under `<root>/<64hex>/*.md` and reserved dot-directories for infrastructure.
- Rejected nested paths, non-Markdown files, hidden infrastructure names, reserved Windows names, trailing ambiguity, and case-fold collisions.
- Seeded first-use memory with zero-byte `memory.md` under the memory lock and preserved the invariant that at least one direct Markdown file remains.
- Used content revisions derived only from sorted direct filenames and exact bytes.
- Implemented nonblocking `try_save_memory()` with lock contention surfacing as `ResourceBusyError` and stale saves raising `MemoryConflictError` with current snapshot plus attempted content.
- Staged memory under the store volume only; no publication journals or job artifact publication were added.
- Self-review found one race after the first green run: unlocked reads could observe a partial replacement. Fixed by taking the same memory lock in `read_memory()` and reran focused plus full-suite verification.

## Concerns

- Task 8 intentionally creates `.backups`, `.staging`, `.locks`, and leaves `.conflicts`/`.journals` reserved but unused until later tasks. That matches the brief, but the unused directories are not eagerly created.
- The current save path uses directory-local backup staging without a Task 9 journal, so crash recovery semantics remain deferred exactly as planned.

## Untracked

- `agency/memory/`
- `tests/test_memory_selectors.py`
- `tests/test_memory_store.py`

## Review Fix 1

### RED

- Command: `\.venv\Scripts\python.exe -m pytest tests/test_memory_selectors.py tests/test_memory_store.py -v`
- Result: `3 failed, 40 passed in 3.64s`.
- Failing cases:
	- `test_try_save_rolls_back_if_install_fails_after_evacuating_old_files`
	- `test_try_save_rolls_back_if_install_fails_immediately_after_evacuation`
	- `test_try_save_preserves_backup_if_rollback_recovery_fails`

### GREEN

- Command: `\.venv\Scripts\python.exe -m pytest tests/test_memory_selectors.py tests/test_memory_store.py -v`
- Result: `43 passed in 2.41s`.

### Full Suite

- Command: `\.venv\Scripts\python.exe -m pytest tests/ -q`
- Result: `885 passed, 3 skipped in 48.58s`.

### Files

- `agency/memory/store.py`
- `tests/test_memory_selectors.py`
- `tests/test_memory_store.py`

### Self-Review

- Added strict `job_id` validation before any staging filesystem access and containment-checked the resolved stage path directly under `.staging/<memory-hash>`.
- Replaced host-dependent filename collision keys with deterministic Unicode normalization plus `casefold()` so cross-platform case and normalization collisions reject consistently.
- Added in-process rollback for canonical replacement failures: partially installed new files are removed, old files are restored, and rollback failures preserve the backup directory for recovery.
- Split evacuation, install, and restore move phases so failure injection and rollback semantics are explicit and testable without depending on host path behavior.

### Concerns

- Task 8 still intentionally stops short of Task 9 journaling and crash-phase recovery receipts; this fix only guarantees in-process rollback and backup preservation within the current save transaction.

## Review Fix 2

### Focused Validation

- Command: `..\.venv\Scripts\python.exe -m pytest tests/test_memory_selectors.py tests/test_memory_store.py tests/test_config_canonical.py -q`
- Result: `150 passed in 4.31s`.

### Full Suite

- Command: `..\.venv\Scripts\python.exe -m pytest tests/ -q`
- Result: `1 failed, 896 passed, 3 skipped in 60.72s`.
- Failing test: `tests/test_job_detached_process.py::test_detached_worker_survives_submitter_exit`
- Failure: job record stayed `queued` instead of reaching `running`.

### Files

- `agency/configuration/models.py`
- `agency/memory/selectors.py`
- `tests/test_config_canonical.py`
- `tests/test_memory_selectors.py`

### Self-Review

- Added config validation that rejects selector-shaped `channel` data on `run`, `routine`, `agent`, and `group` scopes instead of ignoring it.
- Added a direct resolver guard so `resolve_memory_selector()` rejects the same invalid selector shape when callers construct models directly.
- Covered both populated and blank channel values in config parity tests for non-channel scopes, plus direct resolver rejection, while preserving valid `channel` scope behavior.

### Concerns

- The full suite still has one unrelated failure in `test_job_detached_process.py::test_detached_worker_survives_submitter_exit`; the selector fix does not touch that path.