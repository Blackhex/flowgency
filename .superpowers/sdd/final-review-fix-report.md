# Group Storage Redesign Final Review Fix Report

## Status

Completed all requested final-review fixes and low-risk triage notes.

## RED/GREEN evidence

### Finding 1: admin group path validation

RED:

```text
.\.venv\Scripts\python -m pytest tests\test_admin_org_sandbox.py -k "invalid_paths_rerender_submitted_form_without_writing" -q
4 failed
```

The save and create requests propagated `ValidationFailed` instead of returning
the submitted form.

GREEN:

```text
.\.venv\Scripts\python -m pytest tests\test_admin_org_sandbox.py::test_admin_org_save_invalid_paths_rerender_submitted_form_without_writing tests\test_admin_org_sandbox.py::test_admin_org_create_invalid_paths_rerender_submitted_form_without_writing -q
4 passed
```

Invalid nonexistent and overlapping paths now return HTTP 422, show field/path
diagnostics, preserve submitted values and revision, and leave configuration
unchanged.

### Finding 2: platform-aware operation-lock keys

RED (with the implementation temporarily changed back to `.lower()`):

```text
.\.venv\Scripts\python -m pytest tests\test_job_models.py -k "operation_lock_paths_use_platform_case_normalization" -q
1 failed
```

The focused test confirmed the platform normalizer was not called.

GREEN:

```text
.\.venv\Scripts\python -m pytest tests\test_job_models.py -k "operation_lock_paths_use_platform_case_normalization" -q
1 passed
```

POSIX case-distinct roots remain distinct; Windows normalization collapses
case-equivalent roots.

### Finding 3: late-conflict storage initialization

RED:

```text
.\.venv\Scripts\python -m pytest tests\test_config_store.py -k "late_conflict_does_not_initialize_candidate_group_storage" -q
2 failed
```

The induced outside-lock conflict left the candidate group directory behind.

GREEN:

```text
.\.venv\Scripts\python -m pytest tests\test_config_store.py::test_late_conflict_does_not_initialize_candidate_group_storage -q
2 passed
```

Both `replace` and `patch` now perform candidate validation without side
effects, re-check bytes under the lock, then initialize storage and atomically
write.

### Combined new regressions

```text
.\.venv\Scripts\python -m pytest tests\test_admin_org_sandbox.py::test_admin_org_save_invalid_paths_rerender_submitted_form_without_writing tests\test_admin_org_sandbox.py::test_admin_org_create_invalid_paths_rerender_submitted_form_without_writing tests\test_config_store.py::test_late_conflict_does_not_initialize_candidate_group_storage tests\test_job_models.py::test_operation_lock_paths_use_platform_case_normalization -q
7 passed in 0.58s
```

## Validation commands and results

```text
.\.venv\Scripts\python -m pytest tests\test_admin_org_sandbox.py tests\test_group_settings.py tests\test_config_store.py tests\test_job_models.py tests\test_effective_policy.py tests\test_cli_contract.py -q
108 passed in 4.07s

.\.venv\Scripts\python -m pytest tests\test_repository_boundaries.py tests\test_server.py tests\test_admin_dispatch.py -q
40 passed in 1.44s

.\.venv\Scripts\python -m pytest tests\ -q
1227 passed, 3 skipped in 125.24s

rg -n 'schema_version:\s*2|group\.path.*/.*shared|\["shared"\]|shared/(observations|proposals|decisions|jobs|logs)|workspace_dir|group_path=' agency tests CLAUDE.md README.md kb skills examples
No stale matches

.\.venv\Scripts\python -c "from pathlib import Path; from agency.configuration import ConfigStore; ConfigStore(Path('tests/ui/fixtures/config.yaml')).load(); print('valid')"
valid

git diff --check
Passed
```

The fixture validation generated `tests/ui/fixtures/config.yaml.lock`; it was
removed before final status review. No repository-local `shared` directory is
present.

## Changed files

- `agency/web/routes/admin_groups.py`: catches `ValidationFailed`, renders
  submitted save/create values, preserves revisions, and returns 422
  diagnostics.
- `agency/configuration/store.py`: separates side-effect-free validation from
  storage initialization and moves initialization after final conflict checks.
- `agency/jobs/store.py`: uses `os.path.normcase`; renames `job_path`'s
  directory parameter to `jobs_dir`.
- `agency/configuration/models.py`: renames the workspace-base local to
  `workspace_root`.
- `agency/configuration/effective.py`: adds the missing EOF newline.
- `tests/test_admin_org_sandbox.py`: adds save/create invalid-path regressions.
- `tests/test_config_store.py`: adds replace/patch late-conflict regressions.
- `tests/test_job_models.py`: adds platform case-normalization coverage.
- `tests/test_effective_policy.py`: makes resolved-path assertions explicit.
- `tests/test_cli_contract.py`: removes unused fixture padding.

The previously noted hardcoded research state path is no longer present; the
fixture uses `create_group_environment`.

## Self-review

- Invalid POSTs do not redirect or write configuration; submitted paths,
  values, hidden revision, extension-preserving state, and POST semantics are
  retained.
- Candidate parsing and validation remain fail-closed. Initialization failures
  occur before atomic replacement, preserving write atomicity.
- POSIX and Windows lock-key behavior is explicit and covered.
- No stale schema-2/shared-storage references remain in the requested scan.
- Changes are limited to the requested findings and low-risk review cleanup.

## Concerns

- The full suite retains three existing platform/environment-gated skips.
