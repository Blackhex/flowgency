# Task 3 Report - Revision-Checked Config Store and Ownership Patches

## Summary

- Implemented revision-checked config snapshots and locked patch transactions in `agency/configuration/store.py`.
- Added ownership-scoped patch operations and patch records in `agency/configuration/patches.py`.
- Reduced `agency/config.py` to compatibility re-exports via `agency/configuration/compat.py`.
- Added focused TDD coverage in `tests/test_config_store.py` and `tests/test_config_patches.py`.

## RED

Command:

```powershell
Set-Location 'c:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_store.py tests/test_config_patches.py -v
```

Result:

```text
6 failed, 5 errors in 0.56s
```

Key failure:

```text
ModuleNotFoundError: No module named 'agency.configuration.store'
```

## GREEN

Focused Task 3 command:

```powershell
Set-Location 'c:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_store.py tests/test_config_patches.py -v
```

Result:

```text
11 passed in 2.37s
```

Adjacent compatibility command:

```powershell
Set-Location 'c:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_normalization.py tests/test_job_models.py -q
```

Result:

```text
40 passed in 0.83s
```

Post-refactor affected-slice command:

```powershell
Set-Location 'c:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_store.py tests/test_config_patches.py tests/test_config_normalization.py tests/test_job_models.py -q
```

Result:

```text
51 passed in 2.21s
```

## Full Suite

Command:

```powershell
Set-Location 'c:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
719 passed, 1 skipped in 27.46s
```

## Files

- `agency/config.py`
- `agency/configuration/__init__.py`
- `agency/configuration/compat.py`
- `agency/configuration/store.py`
- `agency/configuration/patches.py`
- `tests/test_config_store.py`
- `tests/test_config_patches.py`

## Self-Review

- Revision hashes are computed from exact on-disk bytes via SHA-256 and compared before any mutation is applied.
- `ConfigStore.patch()` holds `<config>.lock`, reloads exact bytes, deep-copies the raw mapping, validates through `parse_config_canonical()`, rechecks the original bytes immediately before atomic replace, and writes UTF-8 YAML.
- Ownership patches only rewrite their owned subtrees and preserve unrelated top-level, group, agent, workspace, and `integration_config` content as raw values.
- `agency/config.py` is now a compatibility re-export layer; the new config store and patch logic live under `agency/configuration/`.

## Concerns

- One initial memory-channel preservation test tried to keep `memory.extension`, but Task 2's strict `MemoryConfig(extra="forbid")` correctly rejects that shape. The test was adjusted to preserve an unrelated top-level extension instead of changing reviewed Task 2 semantics.

## Review Fix 1

### Evidence

- `tests/test_config_patches.py::test_patch_agent_profile_preserves_extension_keys` now seeds `identity.nickname` and `capabilities.approve`, then verifies `patch_agent_profile()` updates `display_name`, `title`, `emoji`, and `write` without dropping the extension keys.
- `tests/test_config_patches.py::test_patch_agent_runtime_preserves_extension_keys` now seeds `runtime.runtime_extension`, `runtime.sandbox`, and `runtime.tools` extension keys, then verifies `patch_agent_runtime()` updates the owned known fields while preserving the extension keys.
- `tests/test_config_patches.py::test_patch_agent_runtime_clears_only_known_fields` now verifies that clearing known runtime fields removes only those fields and leaves unrelated runtime extension keys intact.
- `agency/configuration/patches.py` now merges into existing `identity`, `capabilities`, `runtime`, `sandbox`, and `tools` mappings instead of replacing them wholesale.

### Self-Review

- The fix keeps strict validation in place because every mutation still flows through `ConfigStore.patch()` and `parse_config_canonical()` before write-back.
- The change is scoped to owned subtrees only; it does not alter top-level config behavior or unrelated patch helpers.
- The new tests cover both preservation and intentional clearing semantics at the nested field level.

### Concerns

- `patch_agent_runtime()` currently clears the entire `tools` known subtree when `tools=None`; that matches the current clear semantics in the suite, but if the UI later needs partial tool clearing, this contract will need a follow-up patch.