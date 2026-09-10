# Task 2 Canonical Network Consent Report

Date: 2026-09-10
Branch: feat/ticket-workflows
Worktree: C:/Projekty/Flowgency/.worktrees/ticket-workflows

## RED and GREEN evidence

- RED: `Set-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'; & './.superpowers/venv-cpython/Scripts/python.exe' -m pytest tests/test_ticket_runtime_capabilities.py -q -k local_network`
  - Result: `2 failed, 10 deselected`
  - Failure boundary: `CopilotIntegration` had no `with_config(...)`, so restricted ticket preflight could not consult per-agent consent.
- GREEN: same command after the first integration patch
  - Result: `2 passed, 10 deselected`
- GREEN: `Set-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'; & './.superpowers/venv-cpython/Scripts/python.exe' -m pytest tests/test_agent_detail.py tests/test_config.py tests/test_config_patches.py tests/test_job_models.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_sandbox_policy.py tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q`
  - Result: `281 passed, 1 warning`
- GREEN: `Set-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'; & './.superpowers/venv-cpython/Scripts/python.exe' -m pytest tests/test_agent_detail.py tests/test_config.py tests/test_config_patches.py tests/test_job_models.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_sandbox_policy.py tests/test_copilot_ticket_tools.py tests/test_ticket_end_to_end.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q`
  - Result: `284 passed, 1 warning`

## Contract outcomes

- API and preflight:
  - `CopilotIntegration.with_config(...)` now binds per-instance config without mutating the registered integration.
  - `CopilotIntegration.validate_run(...)` now raises `ticket-local-network-required` before prompt read or subprocess launch when a ticket-enabled Copilot run would execute inside a confining sandbox without explicit opt-in.
- Patch and save path:
  - `AgentRuntimePatch` can now carry an omitted-vs-submitted `integration_config` payload for runtime saves.
  - Runtime POST merges only the approved `integration_config.allow_local_network` field for Copilot agents and preserves unrelated keys such as `model`.
  - Forged checkbox posts for non-Copilot agents do not persist a new grant.
- Snapshot and launch:
  - Immutable job specs continue to carry the resolved `integration_config` mapping, including `allow_local_network` when present.
  - Copilot sandbox settings now emit `sandbox.userPolicy.network.allowLocalNetwork = true` only for explicit per-job opt-in and otherwise omit the key.
- UI:
  - Copilot runtime tab shows `Allow local-network access` plus the approved disclosure.
  - Conflict responses preserve submitted timeout and checkbox state.

## Safety unchanged

- No automatic rewrite of `config.yaml` outside an explicit runtime form submission.
- No runtime bypass or global network grant.
- No widening of non-Copilot integrations.
- No change to existing ticket transport auth, launch arguments, credential gating, or read-only sandbox behavior beyond the explicit per-agent local-network consent.

## Scoped files for commit

- flowgency/configuration/models.py
- flowgency/configuration/patches.py
- flowgency/integrations/flowgency/copilot.py
- flowgency/integrations/flowgency/copilot_sandbox.py
- flowgency/templates/agent_detail_runtime.html
- flowgency/web/routes/agent_detail.py
- tests/test_agent_detail.py
- tests/test_config.py
- tests/test_config_patches.py
- tests/test_copilot_launch_arguments.py
- tests/test_copilot_sandbox_policy.py
- tests/test_copilot_ticket_tools.py
- tests/test_job_models.py
- tests/test_ticket_runtime_capabilities.py
- .superpowers/sdd/2026-09-10-ticket-http-transport/task-2-consent-report.md

## Out of scope left untouched

- `.vscode/tasks.json`
- `tests/_runtime_probe_helpers.py`
- `tests/test_ticket_runtime_live.py`
- `config.yaml.example.lock`
- `tests/test_records_worker.py`
- Browser smoke was not run for this task.