# Ticket Workflow HTTP Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Copilot stdio ticket bridge with a worker-owned authenticated MCP HTTP endpoint while preserving the existing ticket service authority, runtime confinement, and feature acceptance gates.

**Architecture:** The worker starts a private loopback MCP HTTP server before launching Copilot, authenticates every request against live job authority, and reuses the existing typed ticket command dispatch path. Copilot receives only a per-job MCP HTTP config plus a Flowgency-only tool allowlist; deterministic and live tests then prove the same ticket semantics under the new transport.

**Tech Stack:** Python >=3.11, FastAPI/Starlette, Uvicorn, Pydantic >=2.8,<3, MCP SDK 2.2.0, existing job authority and runtime launch helpers, pytest, Playwright, installed Copilot CLI.

## Amendment Notice

This plan supersedes only the transport-specific implementation details from Task 7, Task 8, and the live transport portion of Task 16 in [2026-09-08-ticket-workflows.md](./2026-09-08-ticket-workflows.md). The original 2026-09-08 plan and design still control every other ticket-workflow requirement.

## Global Constraints

- The existing canonical configuration remains the sole control-plane authority.
- Shell access is not an assumed prerequisite.
- A workspace-read-only agent can still create/report tickets and transition tickets it owns when rules pass.
- No token prebinding bypass after revoke, refingerprint, or confirmed stop.
- Keep `127.0.0.1` loopback only. The default remains no local-network access; the only exception is an explicit per-agent Copilot opt-in via `integration_config.allow_local_network: true`. Do not introduce `allowOutbound`, `sandboxMcpServers=false`, or any broader grant.
- Preserve the fixed ticket tool catalog and existing request-digest, assignment, active-run, artifact, and error-envelope rules.
- Do not persist bearer values in `JobSpec`, CLI args, task text, logs, repr output, or canonical records.
- Unsupported runtimes remain `None`; do not guess support from native files or earlier probes.
- Run all commands from `C:/Projekty/Flowgency/.worktrees/ticket-workflows` on `feat/ticket-workflows`.
- Preserve the existing modified `tests/test_ticket_runtime_live.py` and untracked local files unless a later implementation task explicitly updates them.

---

### Task 1: Replace the Copilot stdio bridge with worker-owned MCP HTTP

**Files:**
- Modify: `flowgency/tickets/access.py`, `flowgency/tickets/broker.py`, `flowgency/tickets/protocol.py`, `flowgency/tickets/mcp_server.py`
- Modify: `flowgency/integrations/models.py`, `flowgency/integrations/ticket_tools.py`, `flowgency/integrations/flowgency/copilot.py`
- Modify if adapter request plumbing crosses its own hop: `flowgency/jobs/execution.py` (own hop only; do not broaden).
- Modify: `tests/test_ticket_end_to_end.py` — adapts direct `TicketToolLaunch` constructor consumers (`ticket_tools.env[...]` accesses) to the new `url`/`headers` shape; must not be left broken for Task 2.
- Test: `tests/test_ticket_access.py`, `tests/test_ticket_broker.py`, `tests/test_ticket_mcp.py`, `tests/test_ticket_runtime_capabilities.py`, `tests/test_copilot_ticket_tools.py`, `tests/test_copilot_launch_arguments.py`, `tests/test_copilot_credentials.py`

`flowgency/tickets/models.py` and `flowgency/integrations/__init__.py` are not listed because `LiveTicketEndpoint` is preserved unchanged and the `TicketToolLaunch` export name stays the same; add them only if a strictly required new export surface appears.

**Interfaces:**
- Consumes: `TicketAccessRegistry.open(authority) -> TicketAccessGrant`, the existing typed ticket command parser/dispatcher, and current Copilot launch plumbing.
- Preserves: existing `LiveTicketEndpoint(url: str, grant: TicketAccessGrant)` unchanged — `url` is the bare loopback origin; this struct is used by `TicketBroker.close()` and worker job generation.
- Produces: `TicketBroker.start() -> LiveTicketEndpoint` and `close()` that revoke access and join bounded server threads.
- Produces: `RuntimeCapabilities.live_ticket_transport: Literal["mcp-http"] | None`.
- Produces: `TicketToolLaunch(url: str, headers: Mapping[str, str], server_name: str = "flowgency-tickets", lifecycle: RuntimeProcessLifecycle | None = None)` replacing the prior `command/args/env` shape; `headers` is repr-redacted; `lifecycle` remains required for Copilot process supervision even without a bridge child; no canonical serialization.
- Produces: `build_ticket_tool_launch(endpoint: LiveTicketEndpoint) -> TicketToolLaunch` deriving the MCP URL as `endpoint.url + "/mcp"` and the bearer token from `endpoint.grant.token`, and a Copilot per-job MCP config writer that emits only the Flowgency HTTP server entry.

- [ ] **Step 1: Write deterministic failing tests for the HTTP authority boundary and Copilot launch contract.**

```python
def test_http_broker_rejects_wrong_origin(workflow_env):
    authority = workflow_env.running_job("observer", "run-observer")
    endpoint = workflow_env.start_http_broker(authority)
    response = sdk_http_call(endpoint, origin="http://evil.invalid")
    assert response.status_code == 403


def test_copilot_ticket_tools_use_http_config(copilot_request, monkeypatch):
    launch = TicketToolLaunch(
        url="http://127.0.0.1:43123/mcp",
        headers={"Authorization": "Bearer fixture-token"},
    )
    captured = capture_copilot_launch(monkeypatch, ticket_tools=launch)
    assert "--additional-mcp-config" in captured.argv
    assert "flowgency-tickets" in captured.tool_grants
    assert "fixture-token" not in " ".join(captured.argv)
```

- [ ] **Step 2: Run the focused RED checks and capture contract evidence before implementation.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_access.py tests/test_ticket_broker.py tests/test_ticket_mcp.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py -q`
Expected: failures for missing `mcp-http` support and HTTP launch wiring.

Run `copilot --help`
Expected: installed CLI version and the exact per-launch MCP config flags needed for HTTP server registration.

- [ ] **Step 3: Implement the worker-owned HTTP broker and Copilot adapter with one shared dispatch path.**

```python
server = MCPServer("flowgency-tickets")
app = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    max_request_body_size=2_097_152,  # 2 MiB outer cap; streamed result cap stays 64 KiB
    host="127.0.0.1",
)

async def guard_request(request: Request) -> AgentTicketContext:
    verify_loopback_host(request)
    verify_origin(request)
    token = read_bearer(request)  # defined in flowgency/tickets/broker.py
    return await run_in_threadpool(registry.authenticate, token)  # starlette helper
```

Reuse the existing typed parser/dispatcher for all MCP tool calls. Keep the outer 2 MiB request cap, revoke-on-close behavior, per-call authority validation, result-size limits, and current artifact/state semantics. Generate a per-job Copilot MCP config that points only to the authenticated Flowgency HTTP server, pass it with `--additional-mcp-config`, and keep the allowlist limited to the Flowgency server name.

- [ ] **Step 4: Run GREEN checks, review secrecy boundaries, and verify repository-boundary hygiene.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_access.py tests/test_ticket_broker.py tests/test_ticket_mcp.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q`
Expected: pass with explicit `mcp-http` capability, unchanged ticket semantics, and no token leakage.

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_repository_boundaries.py -q`
Expected: pass, confirming no forbidden tracked terminology or boundary regressions.

- [ ] **Step 5: Review and commit the implementation slice.**

```bash
git add flowgency/tickets/access.py flowgency/tickets/broker.py flowgency/tickets/protocol.py flowgency/tickets/mcp_server.py flowgency/integrations/models.py flowgency/integrations/ticket_tools.py flowgency/integrations/flowgency/copilot.py tests/test_ticket_access.py tests/test_ticket_broker.py tests/test_ticket_mcp.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py tests/test_ticket_end_to_end.py
git commit -m "feat(runtime): switch ticket tools to mcp http"
```

### Task 2: Add the explicit Copilot local-network opt-in, runtime form wiring, and preflight gate

**Files:**
- Modify: `flowgency/configuration/patches.py`, `flowgency/configuration/models.py`, `flowgency/jobs/resolution.py`, `flowgency/jobs/models.py`
- Modify: `flowgency/web/routes/agent_detail.py`, `flowgency/templates/agent_detail_runtime.html`
- Modify: `flowgency/integrations/flowgency/copilot.py`, `flowgency/integrations/flowgency/copilot_sandbox.py`
- Test: `tests/test_agent_detail.py`, `tests/test_config.py`, `tests/test_config_patches.py`, `tests/test_job_models.py`, `tests/test_ticket_runtime_capabilities.py`, `tests/test_copilot_sandbox_policy.py`, `tests/test_copilot_ticket_tools.py`, `tests/test_ticket_end_to_end.py`

**Interfaces:**
- Consumes: Task 1 `TicketToolLaunch(url, headers, server_name, lifecycle)` and existing `AgentRuntimePatch(timeout: int | None)` save flow.
- Produces: `AgentRuntimePatch(timeout: int | None, integration_config: dict[str, object] | object = _UNSET)` so the runtime POST path can distinguish an older form that omitted the local-network field from a submitted unchecked checkbox that means `false`.
- Produces: runtime form fields `revision`, `timeout`, hidden `integration_config.allow_local_network__present=1`, and checkbox `integration_config.allow_local_network` with label `Allow local-network access` and disclosure `Allows connections to local services and LAN hosts, not only Flowgency.`
- Produces: strict Copilot config typing in `validate_config(...)` for `integration_config.allow_local_network`, plus the ticket-only validation issue `code="ticket-local-network-required"`, `field="integration_config.allow_local_network"`, from `CopilotIntegration.validate_run(...)` for ticket-enabled restricted runs when the value is absent or not exactly `True`.
- Produces: per-job sandbox settings that keep the existing `confines` logic and add `sandbox.userPolicy.network.allowLocalNetwork = true` only when the resolved Copilot integration config explicitly opted in.
- Produces: immutable job snapshots that preserve the resolved `spec.integration_config["allow_local_network"]` value used for launch, without rewriting user config and without dropping unrelated `integration_config` keys.

- [ ] **Step 1: Write focused RED tests for config typing, runtime form preservation, run validation, and sandbox opt-in.**

```python
def test_runtime_post_unchecked_checkbox_persists_false_without_dropping_model_key(...):
    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
            "integration_config.allow_local_network__present": "1",
        },
    )
    assert response.status_code == 303
    assert saved_agent["integration_config"] == {
        "model": "gpt-5.4",
        "allow_local_network": False,
    }


def test_copilot_validate_run_rejects_ticket_tools_without_local_network_opt_in(ticket_request):
    integration = get_integration("copilot").with_config({"allow_local_network": False})
    request = replace(
        ticket_request,
        runtime_policy=EffectiveRuntimePolicy(timeout=60, mode="restricted"),
    )
    issues = integration.validate_run(request)
    assert any(issue.code == "ticket-local-network-required" for issue in issues)


def test_build_sandbox_settings_sets_allow_local_network_only_when_opted_in(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))),
        allow_local_network=True,
    )
    assert settings["sandbox"]["userPolicy"]["network"] == {"allowLocalNetwork": True}
```

- Add one config validation test that rejects `integration_config.allow_local_network: "true"` for a Copilot agent as a type error rather than coercing it, one runtime-tab render test that shows the checkbox plus disclosure, one stale-revision runtime POST test that preserves the submitted timeout and checkbox state, and one job-model round-trip test that preserves `integration_config={"model": "gpt-5.4", "allow_local_network": True}` in the snapshot payload.

- [ ] **Step 2: Run the focused RED checks before implementation.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_agent_detail.py tests/test_config.py tests/test_config_patches.py tests/test_job_models.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_sandbox_policy.py tests/test_copilot_ticket_tools.py tests/test_ticket_end_to_end.py -q`
Expected: failures for the missing runtime checkbox, missing strict Copilot config validation, missing `ticket-local-network-required` preflight, and missing per-job `allowLocalNetwork` policy field.

- [ ] **Step 3: Implement the per-instance opt-in without widening unrelated runtime behavior.**

```python
_UNSET = object()


@dataclass(frozen=True)
class AgentRuntimePatch:
    timeout: int | None
    integration_config: dict[str, object] | object = _UNSET


def _runtime_form_integration_config(form) -> dict[str, object] | object:
    if "integration_config.allow_local_network__present" not in form:
        return _UNSET
    return {
        "allow_local_network": "integration_config.allow_local_network" in form,
    }
```

Merge the runtime patch into the agent's top-level `integration_config` mapping instead of `runtime`, preserve unrelated keys such as `model`, preserve permissions and workspace rules by leaving those sections untouched, and refresh services only after a successful patch. In `CopilotIntegration`, implement `with_config()` so per-instance config binds without mutating the global registered integration; `validate_run()` must raise `ticket-local-network-required` before `run()` reaches `task_file.read_text()` or spawns the Copilot process. Extend `build_sandbox_settings(...)` with an explicit boolean input and only emit `sandbox.userPolicy.network.allowLocalNetwork = true` when that input is exactly true; omit the `network` key entirely for absent/false.

- [ ] **Step 4: Run GREEN checks for the opt-in slice and confirm the preflight gate happens before launch.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_agent_detail.py tests/test_config.py tests/test_config_patches.py tests/test_job_models.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_sandbox_policy.py tests/test_copilot_ticket_tools.py tests/test_ticket_end_to_end.py -q`
Expected: pass, with consented fake Copilot/ticket cases setting `integration_config.allow_local_network` only where ticket-enabled restricted runs are supposed to work.

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q`
Expected: pass, confirming the opt-in did not leak secrets or change the HTTP MCP launch contract.

- [ ] **Step 5: Commit the config/runtime opt-in slice.**

```bash
git add flowgency/configuration/patches.py flowgency/configuration/models.py flowgency/jobs/resolution.py flowgency/jobs/models.py flowgency/web/routes/agent_detail.py flowgency/templates/agent_detail_runtime.html flowgency/integrations/flowgency/copilot.py flowgency/integrations/flowgency/copilot_sandbox.py tests/test_agent_detail.py tests/test_config.py tests/test_config_patches.py tests/test_job_models.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_sandbox_policy.py tests/test_copilot_ticket_tools.py tests/test_ticket_end_to_end.py
git commit -m "fix(runtime): require copilot local-network opt-in for tickets"
```

### Task 3: Re-run live runtime acceptance and full feature gates under HTTP transport

**Files:**
- Modify: `tests/test_ticket_runtime_live.py`, `tests/_runtime_probe_helpers.py`, `tests/test_ticket_end_to_end.py`
- Modify: `tests/ui/accessibility.spec.ts`, `tests/ui/dashboard.spec.ts`, `tests/ui/fixtures/config.yaml`, `tests/ui/server.py`
- Modify: `docs/superpowers/verification/2026-09-08-ticket-workflows.md`

**Interfaces:**
- Consumes: Task 1 `mcp-http` capability and Task 2's explicit `integration_config.allow_local_network` opt-in path.
- Produces: live proof that default-missing/false restricted Copilot ticket runs fail with `ticket-local-network-required` before launch, while explicit opt-in unlocks the remaining approved restricted HTTP sequence without widening any other sandbox policy.
- Produces: the original deterministic, UI, and branch-level verification evidence for the ticket-workflows feature.

- [ ] **Step 1: Extend the live tests to cover both the new preflight denial and the approved consented sequence.**

```python
@pytest.mark.real_runtime
def test_copilot_restricted_ticket_run_requires_explicit_local_network_opt_in(runtime_env):
    handle = runtime_env.submit_probe_job(allow_local_network=False)
    record = runtime_env.wait_for_terminal_job(handle)
    assert record.status == "failed"
    assert "ticket-local-network-required" in record.execution_summary


@pytest.mark.real_runtime
def test_copilot_http_run_keeps_ticket_commit_when_write_is_denied(runtime_env):
    handle = runtime_env.submit_artifact_job(
        allow_local_network=True,
        expect_write_denial=True,
    )
    record = runtime_env.wait_for_terminal_job(handle)
    assert record.exit_code == 0
    assert runtime_env.artifact_bytes(handle) is not None
    assert runtime_env.denial_telemetry(handle)
    assert runtime_env.ticket_done(handle)
    assert runtime_env.active_run_after(handle) is None
    assert runtime_env.protected_hashes_match(handle)
```

Keep the existing wrong-host, unauthorized token, revoke, stale refresh, multi-ticket same run, optional sign-off, unchanged-files, and failed-after-commit assertions intact; update only the setup so consented cases submit `integration_config={"allow_local_network": True}` and default-missing cases assert the new preflight failure instead of trying to reach the broker.

- [ ] **Step 2: Run the required live gates in the approved order.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_runtime_live.py -m real_runtime -v`
Expected: first a failing restricted preflight when opt-in is absent/false, then the explicit-opt-in no-op read path, then the full artifact path, denied write evidence, unchanged protected files, assignment cleanup, revoke handling, and preserved committed transitions after later failure.

- [ ] **Step 3: Run deterministic and UI acceptance after the live gate is green.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/ -m 'not real_runtime' -q`
Expected: pass with the transport change and explicit opt-in absorbed by deterministic suites.

Run `npm run test:ui`
Expected: the Runtime tab shows the checkbox/disclosure and the approved workflow surfaces remain accessible and visually stable.

- [ ] **Step 4: Run the full suite and whole-branch review before integration.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/ -q`
Expected: full green suite including installed live probes.

Review the branch for auth-context derivation, revoke semantics, cross-job isolation, bounded shutdown, denied-write behavior, original-target cleanup, stale-transition handling, explicit-opt-in preservation in job snapshots, and absence of any auto rewrite to user config outside submitted runtime forms. Repair any findings with regression coverage before the final verification rerun.

- [ ] **Step 5: Commit verified tests and verification evidence separately.**

```bash
git add tests/test_ticket_runtime_live.py tests/_runtime_probe_helpers.py tests/test_ticket_end_to_end.py tests/ui/accessibility.spec.ts tests/ui/dashboard.spec.ts tests/ui/fixtures/config.yaml tests/ui/server.py
git commit -m "test(workflows): verify ticket orchestration"

git add docs/superpowers/verification/2026-09-08-ticket-workflows.md
git commit -m "docs(workflows): record verification evidence"
```

The verification record must contain actual command results, runtime versions, live capability evidence, approved asset comparison notes, and any genuinely unverified requirement. Do not prefill expected totals.