# Task 1 Report: Relocate Canonical Team and Agent Permissions

## What I implemented

Relocated canonical permissions from `runtime.permissions` to sibling `permissions` blocks on both teams and agents.

Updated the configuration models so:
- `TeamRuntime` and `AgentRuntime` retain `timeout` only.
- `TeamConfig` and `AgentInstance` now own `permissions` with the existing `RuntimePermissions` model.
- nested `runtime.permissions` is rejected with `code="relocated-permissions"` and the required corrective hint.
- relative permission rule paths are resolved from the owner mapping instead of the runtime mapping.

Updated active producers and consumers so saved configuration stays valid under the new shape:
- effective policy resolution now reads sibling team and agent permissions.
- config patch writers and route helpers now persist sibling permissions while preserving runtime timeout semantics.
- permission-path and integration diagnostics now point at `permissions.mode` and `permissions.rules`.
- active examples, setup skill content, UI fixture config, and active tests were migrated to the sibling layout.

## TDD Evidence

### RED

Command:

```text
python -m pytest tests/test_permission_relocation.py -q
```

Relevant failing output before implementation:

```text
FAILED tests/test_permission_relocation.py::test_sibling_policy_keeps_union_and_timeout - IndexError: tuple index out of range
FAILED tests/test_permission_relocation.py::test_nested_policy_never_silently_ignored[False-team] - Failed: DID NOT RAISE <class 'flowgency.configuration.issues.ValidationFailed'>
FAILED tests/test_permission_relocation.py::test_nested_policy_never_silently_ignored[False-agent] - Failed: DID NOT RAISE <class 'flowgency.configuration.issues.ValidationFailed'>
FAILED tests/test_permission_relocation.py::test_nested_policy_never_silently_ignored[True-team] - Failed: DID NOT RAISE <class 'flowgency.configuration.issues.ValidationFailed'>
FAILED tests/test_permission_relocation.py::test_nested_policy_never_silently_ignored[True-agent] - Failed: DID NOT RAISE <class 'flowgency.configuration.issues.ValidationFailed'>
5 failed in 0.71s
```

Why this failure was expected:
- sibling `permissions` blocks were ignored by the current parser, so no merged rule existed.
- old nested `runtime.permissions` was still accepted instead of rejected.

### GREEN

Command:

```text
python -m pytest tests/test_permission_relocation.py -q
```

Relevant passing output after implementation:

```text
.....                                                                    [100%]
5 passed in 0.63s
```

## Focused validation

Command:

```text
python -m pytest tests/test_permission_relocation.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_effective_policy.py tests/test_executor_eligibility.py tests/test_agent_detail.py -q
```

Output:

```text
.......................................................................  [100%]
71 passed in 35.73s
```

Additional targeted repair validation during iteration:

```text
python -m pytest tests/test_admin_dispatch.py tests/test_cli_contract.py tests/test_job_detached_process.py tests/test_job_submission.py tests/test_proposal_questions.py tests/test_records_worker.py tests/test_team_settings.py tests/test_write_boundary_contract.py -q
```

```text
.....................................................s.................. [ 46%]
.............................................................s.......... [ 93%]
..........                                                               [100%]
152 passed, 2 skipped in 13.10s
```

```text
python -m pytest tests/test_dispatch_launch_recovery.py -q
```

```text
........                                                                 [100%]
8 passed in 2.37s
```

## Full-suite validation

Command:

```text
python -m pytest tests/ -q
```

Output:

```text
2097 passed, 6 skipped in 263.06s (0:04:23)
```

Cleanliness checks:

```text
git diff --check
git diff --stat
```

`git diff --check` produced no output.

## Files changed

- `AGENTS.md`
- `config.yaml.example`
- `examples/code-review-team/README.md`
- `examples/content-team/README.md`
- `flowgency/configuration/effective.py`
- `flowgency/configuration/models.py`
- `flowgency/configuration/patches.py`
- `flowgency/configuration/paths.py`
- `flowgency/integrations/__init__.py`
- `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`
- `flowgency/web/routes/admin_teams.py`
- `flowgency/web/routes/agent_detail.py`
- `kb/agent-identity.md`
- `kb/configuration.md`
- `tests/conftest.py`
- `tests/test_admin_dispatch.py`
- `tests/test_admin_org_sandbox.py`
- `tests/test_agent_detail.py`
- `tests/test_cli_contract.py`
- `tests/test_config.py`
- `tests/test_config_normalization.py`
- `tests/test_config_patches.py`
- `tests/test_dispatch_launch_recovery.py`
- `tests/test_effective_policy.py`
- `tests/test_executor_eligibility.py`
- `tests/test_integration_contract.py`
- `tests/test_job_detached_process.py`
- `tests/test_job_submission.py`
- `tests/test_path_validation.py`
- `tests/test_permission_capabilities.py`
- `tests/test_permission_form_preservation.py`
- `tests/test_permission_relocation.py`
- `tests/test_permission_resolution.py`
- `tests/test_proposal_questions.py`
- `tests/test_records_worker.py`
- `tests/test_surface_contracts.py`
- `tests/test_team_settings.py`
- `tests/test_write_boundary_contract.py`
- `tests/ui/fixtures/config.yaml`

## Self-review

- Confirmed sibling permissions are the only accepted canonical shape in active producers and consumers.
- Confirmed old nested `runtime.permissions` remains covered only in rejection tests and diagnostics.
- Confirmed timeout inheritance and rule union semantics are preserved.
- Confirmed no runtime helper or compatibility shim was added to auto-load the old shape.

## Concerns

None.

## Round 1 Fixes

Addressed the first review round without changing implementation behavior:
- corrected `kb/agent-identity.md` to describe instance-owned sibling `permissions` rather than `runtime permissions`.
- tightened `tests/test_permission_relocation.py` so all four parameterized cases assert the exact `relocated-permissions` diagnostic scope, field, and corrective hint.

Command:

```text
python -m pytest tests/test_permission_relocation.py tests/test_repository_boundaries.py -q
```

Result:

```text
9 passed in 0.74s
```

Commands:

```text
git diff --check
git diff --stat
```

Results:

```text
git diff --check: no diff errors; Git warned that tests/test_permission_relocation.py will normalize CRLF to LF on a future write.
git diff --stat:
 kb/agent-identity.md                |  2 +-
 tests/test_permission_relocation.py | 15 ++++++++++++++-
 2 files changed, 15 insertions(+), 2 deletions(-)
```