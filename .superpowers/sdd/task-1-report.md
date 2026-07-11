# Task 1 Report: Shared Server Launcher And Reload Policy

## Implementation Summary

- Added immutable reload include and exclude policy tuples in `agency/app.py`.
- Added `_reload_excludes(root)` to combine existing absolute artifact directories with shared runtime-data glob exclusions.
- Added `run_server(host, port, reload=False)` so normal mode uses the in-memory FastAPI app and reload mode uses the `agency.app:app` import string with the project reload policy.
- Preserved first-run config creation and ensured `reload_groups()` runs before Uvicorn starts.
- Added the `--reload` module CLI flag and delegated module startup to `run_server()`.
- Added the direct Windows dependency `watchfiles>=0.20`; editable installation succeeded and reported WatchFiles `1.2.0`.
- Added four focused tests covering normal startup, reload startup, watched/excluded paths, and first-run initialization ordering.

## RED

Command:

```powershell
python -m pytest tests/test_server.py -v
```

Output summary:

```text
collected 4 items
4 failed in 0.44s
```

The failures were expected: three tests raised `AttributeError` because `agency.app` had no `run_server`, and the reload-policy test raised `AttributeError` because `agency.app` had no `RELOAD_INCLUDES`. This confirmed the tests exercised the missing launcher contract rather than an unrelated failure.

## GREEN

Command:

```powershell
python -m pytest tests/test_server.py -v
```

Output:

```text
collected 4 items
tests/test_server.py::test_run_server_normal_mode_uses_in_memory_app PASSED
tests/test_server.py::test_run_server_reload_mode_uses_import_string_and_project_policy PASSED
tests/test_server.py::test_reload_policy_watches_project_files_and_ignores_runtime_data PASSED
tests/test_server.py::test_run_server_creates_config_before_starting_uvicorn PASSED
4 passed in 0.37s
```

No warning reported that reload include/exclude options had no effect.

## Full Suite

Final pre-commit command:

```powershell
python -m pytest tests/ -q
```

Output:

```text
429 passed, 1 skipped in 3.65s
```

## Files Changed

- `agency/app.py`
- `pyproject.toml`
- `tests/test_server.py`

## Commit

- SHA: `fb2dc414289d4b4b221e844fe41e1a7afd81302a`
- Subject: `feat(server): add opt-in reload launcher`

## Self-Review Findings

- Confirmed the implementation and tests match the brief's prescribed code and exact policy values.
- Confirmed normal mode passes the in-memory app while reload mode passes the import string required by Uvicorn's reloader.
- Confirmed reload policy includes Python, templates, static assets, JSON, and YAML while excluding existing and future `shared` runtime data plus artifact directories.
- Confirmed first-run config creation precedes both group reload and server startup.
- Confirmed `git diff --check` and editor diagnostics reported no errors.
- Confirmed the commit contains only the three Task 1 files; prohibited documentation, CLI, plan, and VS Code task files were not modified.

## Concerns

None.

## Important Task 1 Review Follow-up: BLOCKED

### Review Finding

`_reload_excludes()` includes artifact directories only when they exist at startup. A later-created `.venv`, cache, VCS, or package metadata directory is therefore not represented by Uvicorn's `FileFilter` directory exclusions and can trigger reloads.

### Technical Verification

- Uvicorn `0.49.0` converts an exclusion to `FileFilter.exclude_dirs` only when `Path(exclusion).is_dir()` is true during filter construction.
- Passing an absent prospective absolute directory through the public `uvicorn.Config` API fails with `NotImplementedError: Non-relative patterns are unsupported` during reload-pattern resolution.
- Relative patterns remain filename patterns and are evaluated with `Path.match()`. In Python `3.13.14`, `**` in `Path.match()` does not recursively absorb arbitrary path depth, so patterns such as `**/.venv/**/*` reject only specific depths and accepted a deep `.venv/Lib/site-packages/tool.py` path under the pytest temporary root.
- A regression test that constructed `FileFilter` before creating `.venv`, then created `.venv/Lib/site-packages/tool.py`, failed against both the current helper and the finite-depth glob candidate because `file_filter(future_artifact.resolve())` returned `True`.

The public Uvicorn reload configuration cannot robustly express an absent directory plus descendants at arbitrary depth. Satisfying the binding requirement requires a material architecture change, such as owning the WatchFiles filter/supervisor rather than configuring `uvicorn.run()`. Per the human resolution, no arbitrary directory-depth approximation was retained.

### Validation

Command: `python -m pytest tests/test_server.py -v`

Output: `4 passed in 0.35s` (restored baseline; it does not contain the required future-artifact regression).

Command: `git diff --check`

Output: passed with no output.

### Files Changed

- `.superpowers/sdd/task-1-report.md` only (ignored task artifact)
- No source, test, executable-plan, CLI, task JSON, README, or KB changes retained

### Fix Commit

None. Status is BLOCKED; no fix commit was created.

## Important Task 1 Review Resolution: RESOLVED

### Technical Resolution

- Replaced startup-time Uvicorn exclusion discovery with `_AgencyReloadFilter`, which resolves each WatchFiles event relative to the single reload root.
- The filter accepts exactly `*.py`, `*.html`, `*.css`, `*.js`, `*.json`, `*.yaml`, and `*.yml`.
- It rejects paths outside the root and relative directory components named `.git`, `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, or `shared`, plus any component ending in `.egg-info`, at arbitrary depth and regardless of creation time.
- Reload mode now mirrors Uvicorn 0.49's `Config.load_app()` / `Server` / socket bind / `WatchFilesReload` sequence and replaces only the supervisor's assignable `watch_filter`. Normal mode remains `uvicorn.run(app, host=..., port=...)`.
- Only `KeyboardInterrupt` is caught around the reload supervisor, matching Uvicorn's helper; other errors propagate.

### RED: Future-Artifact Regression

Command: `python -m pytest tests/test_server.py -v`

Output: `3 failed, 2 passed in 0.44s`. The reload tests failed because `WatchFilesReload`, `_create_reload_supervisor`, and `_AgencyReloadFilter` did not exist. Normal mode and first-run setup remained green.

The regression constructs the actual Agency supervisor before creating deep `.venv/Lib/site-packages/tool.py` and `shared/jobs/job.yaml` paths.

### GREEN

Command: `python -m pytest tests/test_server.py -v`

Output: `6 passed in 0.34s`.

Coverage proves the two future paths and all required excluded components are rejected at arbitrary depth; all seven source types and root `config.yaml` are accepted; outside-root paths are rejected; the reload launcher uses the import string, root, host, and port without starting a real watcher; normal and first-run behavior remain intact; non-`KeyboardInterrupt` errors propagate.

### Full Suite And Diff

- `python -m pytest tests/ -q`: `431 passed, 1 skipped in 3.39s`
- `git diff --check`: passed with no output

### Files Changed

- `agency/app.py`
- `tests/test_server.py`
- `docs/superpowers/plans/2026-07-11-serve-hot-reload.md`
- `docs/superpowers/specs/2026-07-11-serve-hot-reload-design.md`

### Fix Commit

- SHA: `e2a7ddd06d6731beea8d9ae86601ae4199ddda37`
- Subject: `fix(server): filter future reload artifacts`

### Concerns

None.