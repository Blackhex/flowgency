# Task 4 Report: Effective Runtime Policy and Fail-Closed Integration Capabilities

## RED
Command:
` .venv\Scripts\python.exe -m pytest tests\test_effective_policy.py tests\test_integration_contract.py -v `

Result:
- Failed during collection before implementation.
- Error: `ModuleNotFoundError: No module named 'agency.integrations.models'`

Evidence:
- The initial Task 4 focused run failed immediately because the effective-policy and runtime-capability surface did not exist yet.

## GREEN
Command:
` .venv\Scripts\python.exe -m pytest tests\test_effective_policy.py tests\test_integration_contract.py -v `

Result:
- `130 passed in 0.57s`

Coverage of binding requirements:
- Timeout precedence resolved as job override > agent override > group default.
- Effective roots resolve as ordered canonical group roots plus agent additions with first-seen, platform-aware deduplication.
- Unrestricted mode returns no roots and agent additions fail closed with a structured contradiction.
- Agent tools replace group tools only when explicitly configured; omission inherits.
- Unknown integrations fail closed with shared `ValidationIssue` data via `ValidationFailed`.
- Unsupported runtime path/tool modes emit shared `unsupported-path-policy` and `unsupported-tool-policy` issues.
- Copilot declares only the proven current argv capability set: restricted/unrestricted paths and all/allowlist tools.
- Existing execution APIs remain unchanged.

## FULL SUITE
Command:
` .venv\Scripts\python.exe -m pytest tests\ -q `

Result:
- `737 passed, 1 skipped in 28.12s`

## Files
- `agency/configuration/__init__.py`
- `agency/configuration/effective.py`
- `agency/configuration/models.py`
- `agency/integrations/__init__.py`
- `agency/integrations/agency/copilot.py`
- `agency/integrations/models.py`
- `tests/test_effective_policy.py`
- `tests/test_integration_contract.py`

## Self-Review
- Kept the change at the pure resolution and integration-validation boundary.
- Did not alter store code or execution call signatures.
- Preserved fail-closed defaults for all non-Copilot integrations by giving `BaseIntegration` only unrestricted/all runtime capabilities unless an adapter proves otherwise.
- Fixed one local regression in canonical runtime preparation so omission vs override semantics survive parsing and group inheritance works correctly.
- Used stable group and agent ids in structured validation scopes.

## Concerns
- `resolve_effective_policy()` raises `ValidationFailed` for unknown integrations and unsupported runtime policies, which is consistent with the shared validation-issue requirement, but downstream call sites are not yet wired to consume that surface. Task 6 appears to be the planned place for broader resolver integration.

## Review Fix 1
Command:
` .venv\Scripts\python.exe -m pytest tests/test_effective_policy.py tests/test_integration_contract.py -v `

Result:
- `130 passed in 1.29s`

Command:
` .venv\Scripts\python.exe -m pytest tests/ -q `

Result:
- `737 passed, 1 skipped in 27.94s`

Files:
- `tests/test_effective_policy.py`
- `tests/test_integration_contract.py`

Self-Review:
- Replaced the loose `ValueError` expectation with exact `ValidationFailed` assertions for code, scope, field, message, and corrective hint.
- Added a resolver-level contract test that exercises `resolve_effective_policy()` against the live Copilot registry entry and verifies the structured unsupported-policy issues.
- Kept the unknown-integration test in place.

Concerns:
- The new resolver-level contract test temporarily narrows the live Copilot integration's declared runtime capabilities and restores them in `finally`; the test is isolated, but it still mutates shared registry state during execution.
