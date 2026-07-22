# Task 1 Report — Establish the Canonical Schema Version 3

## Summary

Implemented the canonical schema v3 for configuration parsing/validation and updated supporting path/bootstrap behavior plus test fixtures/helpers so the repository consistently uses:

- `CONFIG_SCHEMA_VERSION = 3`
- `AgencyConfig.schema_version: Literal[3]`
- `GroupConfig.workspace_path: Path`
- `GroupConfig.path: Path`

No compatibility aliasing or migration behavior was added.

## Implementation details

### 1. Canonical schema models and validation

Updated `agency/configuration/models.py` to:

- introduce `CONFIG_SCHEMA_VERSION = 3`
- accept `schema_version` as a canonical root key
- require `AgencyConfig.schema_version: Literal[3]`
- require `GroupConfig.workspace_path` and `GroupConfig.path`
- emit `unsupported-schema-version` when `schema_version != 3`
- emit explicit missing-field issues for both `groups.<name>.workspace_path` and `groups.<name>.path`
- resolve both `workspace_path` and `path` relative to the config directory
- resolve runtime sandbox roots relative to `workspace_path`

### 2. Group state/bootstrap and validation alignment

Updated `agency/configuration/paths.py` to align with the split roots:

- `workspace_path` is now the existing writable workspace validated with `invalid-group-workspace`
- `path` is treated as the writable group-state/control root and may be initialized if absent
- `initialize_control_directories()` now creates each group `path`
- overlap checks now include both `workspace_path` and `path`

### 3. Group patch helpers kept config writes canonical

Updated `agency/configuration/patches.py` so group create/save patch operations persist `workspace_path` alongside `path`. Current admin/group patch flows still accept one path input, so they now mirror that value into both fields to keep saved configs valid under schema v3.

### 4. Canonical tests, fixtures, and setup skill

Updated:

- `tests/conftest.py` canonical fixture to separate workspace/state roots
- schema assertions in `tests/test_config.py`, `tests/test_config_store.py`, `tests/test_surface_contracts.py`
- `tests/test_path_validation.py` for workspace-vs-state semantics
- many config-writing test helpers across the suite so they now include `schema_version: 3` and `workspace_path`
- `skills/agency-setup/SKILL.md` canonical YAML example to include `schema_version: 3` and split `workspace_path` / `path`
- `tests/test_agency_setup_skill.py` to match the canonical v3 skill content

## RED / GREEN evidence

### RED 1 — schema tests before production changes

Command:

```powershell
.venv\Scripts\python -m pytest tests\test_config.py tests\test_config_store.py tests\test_surface_contracts.py -q
```

Result:

- `28 failed, 98 passed in 1.72s`
- failures included:
  - `Extra inputs are not permitted.`
  - `Group workspace_path is required.`

### GREEN 1 — schema tests after implementation

Command:

```powershell
.venv\Scripts\python -m pytest tests\test_config.py tests\test_config_store.py tests\test_surface_contracts.py -q
```

Result:

- `126 passed in 3.86s`

### Additional targeted validation

Command:

```powershell
.venv\Scripts\python -m pytest tests\test_path_validation.py -q
```

Result:

- `6 passed, 1 skipped in 0.25s`

Broad regression subset:

```powershell
.venv\Scripts\python -m pytest tests\test_admin_agent_create.py tests\test_admin_dispatch.py tests\test_admin_org_sandbox.py tests\test_agency_setup_skill.py tests\test_agent_library_routes.py tests\test_agent_roster.py tests\test_agent_run.py tests\test_cli.py tests\test_cli_contract.py tests\test_config_normalization.py tests\test_config_patches.py tests\test_dashboard.py tests\test_dispatch_run.py tests\test_execute_decision.py tests\test_job_detached_process.py tests\test_job_routes.py tests\test_job_submission.py tests\test_logs.py tests\test_memory_channel_routes.py tests\test_proposal_questions.py tests\test_workspaces.py -q
```

Result before final fix:

- `1 failed, 334 passed in 79.23s`
- remaining failure: `test_patch_agent_runtime_preserves_extension_keys`

Follow-up fix validation:

```powershell
.venv\Scripts\python -m pytest tests\test_config_patches.py -q
```

Result:

- `9 passed in 0.44s`

## Full suite

Initial full-suite check after core implementation exposed remaining old-schema helpers:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

Result:

- `228 failed, 977 passed, 3 skipped in 80.05s`
- dominant failure mode: test configs still missing `schema_version` and/or `workspace_path`

Final full-suite verification:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

Result:

- `1205 passed, 3 skipped in 135.66s`

## Files changed

- `agency/configuration/models.py`
- `agency/configuration/patches.py`
- `agency/configuration/paths.py`
- `skills/agency-setup/SKILL.md`
- `tests/conftest.py`
- `tests/test_admin_agent_create.py`
- `tests/test_admin_dispatch.py`
- `tests/test_admin_dispatch_xss.py`
- `tests/test_admin_org_sandbox.py`
- `tests/test_agency_setup_skill.py`
- `tests/test_agent_library_routes.py`
- `tests/test_agent_roster.py`
- `tests/test_agent_run.py`
- `tests/test_cli.py`
- `tests/test_cli_contract.py`
- `tests/test_config.py`
- `tests/test_config_normalization.py`
- `tests/test_config_patches.py`
- `tests/test_config_store.py`
- `tests/test_dashboard.py`
- `tests/test_decision_verify.py`
- `tests/test_dispatch_run.py`
- `tests/test_group_settings.py`
- `tests/test_instances.py`
- `tests/test_job_detached_process.py`
- `tests/test_job_routes.py`
- `tests/test_job_submission.py`
- `tests/test_logs.py`
- `tests/test_memory_channel_routes.py`
- `tests/test_path_validation.py`
- `tests/test_proposal_questions.py`
- `tests/test_surface_contracts.py`
- `tests/test_workspaces.py`

## Self-review

- Confirmed no compatibility alias/migration logic was introduced.
- Confirmed schema v3 is enforced centrally in validation and model parsing.
- Confirmed relative sandbox roots now resolve from `workspace_path`, matching the new split-root model.
- Confirmed group create/save patch helpers persist canonical v3 shape instead of saving invalid intermediate configs.
- Confirmed the repository-wide test helper updates were necessary and sufficient via final full-suite pass.

## Concerns

- Admin/group patch flows currently mirror the single submitted path into both `workspace_path` and `path` so existing UI flows remain valid under schema v3. This is intentional for Task 1, but later storage-redesign tasks should introduce fully separate edit/create inputs once the UI and workflows are ready.
