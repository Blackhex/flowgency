# Task 2 Report: Strict canonical Configuration Models and Validation

## Status

Complete.

## RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
ERROR collecting tests/test_config_canonical.py
ModuleNotFoundError: No module named 'agency.configuration.models'
```

## GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
12 passed in 0.29s
```

## Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
625 passed, 1 skipped in 24.73s
```

## Files Changed

- [agency/configuration/__init__.py](agency/configuration/__init__.py)
- [agency/configuration/models.py](agency/configuration/models.py)
- [agency/configuration/validation.py](agency/configuration/validation.py)
- [pyproject.toml](pyproject.toml)
- [tests/conftest.py](tests/conftest.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)

## Self-Review

- `schema_version: 2` is enforced as the only accepted runtime shape.
- Explicit omission defaults are encoded in the typed models and validated in the focused tests.
- Config-relative path resolution is handled in parsing without mutating the input mapping.
- Extra fields remain representable on the broad control-plane models via `extra="allow"`.
- The canonical validator emits sorted `ValidationIssue` tuples for semantic problems.

## Concerns

- The canonical parser currently resolves and validates the strict typed shape, but Task 3 still needs to preserve raw YAML round-trip behavior in the config store.

## Review Fix 8

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
85 collected, new identifier-contract regressions failed before implementation. The failures covered missing validation for memory channel keys plus parse/validate parity gaps for invalid group identifiers, invalid channel identifiers, invalid blueprint identifiers, and missing default-group references.
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
85 passed in 0.63s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
698 passed, 1 skipped in 27.13s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- The shared identifier pipeline now enforces the Task 2 lowercase slug contract across group keys, memory channel keys, agent names, routine ids, blueprint references, and `agency.default_group`.
- `agency.default_group` remains optional when blank, but a nonblank value must now be both syntactically valid and a declared group reference.
- The fix stayed within semantic validation only: no integration registry membership checks, no filesystem existence checks, and no migration behavior were added.
- The new tests prove parse/validate parity for the added invalid identifier and cross-reference cases, and retain valid hyphenated identifiers as accepted examples.

### Concerns

- This tightens identifier syntax to lowercase hyphenated slugs in the canonical validator. Any external unpublished configs that relied on uppercase or underscore identifiers will now fail validation, which matches the approved Task 2 model but may need explicit migration handling in later tasks if such configs exist.

## Review Fix 7

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests\test_config_canonical.py -v
```

Result:

```text
Focused red phase was established by adding regressions for strict group dispatch validation and explicit missing/blank blueprint semantic validation.
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests\test_config_canonical.py -v
```

Result:

```text
68 passed in 0.77s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
681 passed, 1 skipped in 31.46s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- Human ruling applied: group dispatch is strict and now rejects any unknown field, while `dispatch.agents` keeps its dedicated superseded migration issue.
- `GroupDispatch` now forbids extras, and the raw validator emits the stable `invalid-config` issue for any other unknown dispatch key.
- Blueprint absence and blank values now surface as explicit `missing-blueprint` issues before Pydantic enforcement still guards the typed model.
- Full-suite validation required installing `portalocker` into the local venv before tests could run successfully.

## Review Fix 1

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
tests/test_config_canonical.py::test_rejects_undeclared_channel_memory_reference FAILED
E       assert False
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
14 passed in 0.26s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
627 passed, 1 skipped in 28.41s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- The channel-scoped memory selector now rejects unknown top-level `memory.channels` references before parsing succeeds.
- The new tests cover both rejection and acceptance, and the existing missing-channel check still passes.
- The fix stays inside the configuration validation boundary and does not broaden model behavior beyond the review finding.

## Review Fix 2

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
...FF.FF....FFF...
```

## Review Fix 10

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests\test_config_canonical.py -q
```

Result:

```text
Initial sandbox ownership regressions failed because the config model still shared one runtime sandbox shape across group and agent scopes.
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests\test_config_canonical.py -q
```

Result:

```text
95 passed in 0.60s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
708 passed, 1 skipped in 23.34s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- Group runtime sandbox now uses its own strict model with `mode` and `roots` only.
- Agent runtime sandbox now uses its own strict model with `mode` and `additional_roots` only.
- Wrong-owner sandbox fields are rejected by model validation with `invalid-config`, not just by semantic contradiction checks.
- The existing allowlist and blueprint validators were restored after the refactor so unrelated config behavior stayed intact.

### Concerns

- The validator still emits named-agent field paths for agent sandbox errors, which is correct for the current model but should be kept stable if downstream tooling depends on positional indices.

## Review Fix 9

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
5 focused regressions failed before the validator patch: missing-or-blank group default_integration, invalid group allowlist, and group sandbox contradictions were not being enforced at the group runtime scope.
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
91 passed in 0.69s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
704 passed, 1 skipped in 28.14s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- Group `default_integration` now fails explicitly when missing or blank, while registry membership remains deferred to Task 4.
- Group sandbox validation now uses `roots` for the group scope and `additional_roots` for the agent scope, preserving the ownership split.
- Allowlist validation now rejects blank names and empty trimmed results through the same shared pipeline used by parse and validate.
- Parse/validate parity was confirmed by the focused canonical run and then the full suite.

### Concerns

- The new `missing-default-integration` issue is semantic-only; registry membership and any UI safeguards remain separate work.

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
..................                                                       [100%]
18 passed in 0.40s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
631 passed, 1 skipped in 30.70s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- `parse_config_canonical()` now skips agent sandbox root resolution when a group has no `path`, so malformed configs surface as `ValidationFailed` through the existing validation path.
- `_validate_rule()` now rejects non-mapping schedules directly, which keeps scalar, list, and other malformed schedule shapes in shared `ValidationIssue` output.
- The focused regression tests prove both failure modes and the full suite stayed green after the minimal change set.

## Review Fix 3

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
tests/test_config_canonical.py::test_parse_config_canonical_rejects_malformed_agent_entries[None-invalid-agent-entry-agents[0]] FAILED
tests/test_config_canonical.py::test_rejects_blank_allowlist_names[names0-runtime.tools.names[0]] FAILED
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
24 passed in 0.35s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
637 passed, 1 skipped in 26.40s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- Malformed agent entries are now rejected deterministically before the parser can index into them.
- Blank and whitespace-only allowlist names are reported as field-specific issues instead of being treated as a valid allowlist.
- The fix remains local to canonical config validation and is covered by representative malformed-shape regressions.

## Review Fix 4

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
18 failed, 28 passed in 1.66s
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
46 passed in 1.19s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
659 passed, 1 skipped in 24.64s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- Added one centralized raw-shape audit ahead of parsing so malformed top-level and nested YAML shapes now fail deterministically as `ValidationFailed` instead of leaking implementation exceptions.
- Preserved the existing `invalid-dispatch-rule` contract for malformed routine schedules while reporting other shape errors with field-specific locations.
- Kept the change bounded to Task 2 raw config validation/parsing and expanded parameterized coverage for representative malformed mapping/list boundaries.

### Concerns

- None beyond the existing Task 3 raw-store preservation follow-up already noted earlier in this report.

## Review Fix 6

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
tests/test_config_canonical.py::test_validate_config_canonical_reports_superseded_group_dispatch_agents FAILED
tests/test_config_canonical.py::test_parse_config_canonical_rejects_superseded_group_dispatch_agents FAILED
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -v
```

Result:

```text
64 passed in 1.21s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
677 passed, 1 skipped in 26.16s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- `groups.<group>.dispatch.agents` now fails fast as a superseded v1 ownership violation with a deterministic corrective hint.
- `parse_config_canonical()` and `validate_config_canonical()` share the same rejection path, so the review finding is closed in the unified pipeline.
- Supported canonical group dispatch fields and agent routine schedules remain accepted, and the focused regression keeps that boundary explicit.

### Concerns

- None beyond the existing Task 3 raw-store preservation follow-up already noted earlier in this report.

## Review Fix 5

### RED

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
47 passed, 14 failed in 0.69s
```

### GREEN

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests/test_config_canonical.py -q
```

Result:

```text
61 passed in 1.15s
```

### Full Suite

Command:

```powershell
Set-Location 'C:\Projects\christag-agency\.worktrees\unified-agent-configuration'; & .\.venv\Scripts\python.exe -m pytest tests -q
```

Result:

```text
674 passed, 1 skipped in 27.72s
```

### Files Changed

- [agency/configuration/models.py](agency/configuration/models.py)
- [tests/test_config_canonical.py](tests/test_config_canonical.py)
- [.superpowers/sdd/task-2-report.md](.superpowers/sdd/task-2-report.md)

### Self-Review

- Replaced the split parser/validator behavior with one internal non-recursive pipeline that collects schema, shape, raw semantic, typed-model, and post-parse issues once and reuses the same parsed object for `parse_config_canonical()`.
- Malformed scalar and list routine items now emit field-specific `invalid-routine-entry` issues and can no longer leak `dict(routine)` type errors.
- Parse/validate parity is covered with representative semantic-invalid fixtures and a valid fixture proving both boundaries accept the same configuration model.

### Concerns

- None beyond the existing Task 3 raw-store preservation follow-up already noted earlier in this report.