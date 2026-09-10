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
- Keep `127.0.0.1` loopback only, with no `allowLocalNetwork`, broad outbound grant, or `sandboxMcpServers=false` escape hatch.
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

### Task 2: Re-run live runtime acceptance and full feature gates under HTTP transport

**Files:**
- Modify: `tests/test_ticket_runtime_live.py`, `tests/test_ticket_end_to_end.py`, `tests/_runtime_probe_helpers.py`
- Modify: `tests/ui/accessibility.spec.ts`, `tests/ui/dashboard.spec.ts`, `tests/ui/fixtures/config.yaml`, `tests/ui/server.py`
- Modify: `docs/superpowers/verification/2026-09-08-ticket-workflows.md`
- Update any `README.md` or `kb/integrations.md` sections that describe the stdio MCP bridge as the live transport to reflect the HTTP endpoint contract.

**Interfaces:**
- Consumes: Task 1 `mcp-http` capability and worker-owned `LiveTicketEndpoint` launch path.
- Produces: live proof that Copilot can initialize, list tools, read tickets in a restricted run, complete the verified artifact path, deny workspace writes under read-only policy, and preserve assignment/cleanup semantics.
- Produces: full deterministic, UI, and branch-level verification evidence for the ticket-workflows feature.

- [ ] **Step 1: Extend the failing live tests to match the approved HTTP acceptance sequence.**

```python
# runtime_env fixture contract (own in tests/test_ticket_runtime_live.py or a shared fixture);
# implementer must produce all helpers used below before submitting Task 2:
#   submit_probe_job() -> handle       — read-only job whose prompt calls ticket_get
#   submit_artifact_job(expect_write_denial) -> handle
#   wait_for_terminal_job(handle) -> JobRecord
#   observed_tool_calls(handle) -> list[dict]  # MCP state events or output scan for tool results
#   project_hash_before / project_hash_after -> str
#   artifact_bytes(handle) -> bytes | None
#   denial_telemetry(handle) -> list[dict]
#   ticket_done(handle) -> bool        — ticket state == done after run
#   active_run_after(handle) -> str | None
#   protected_hashes_match(handle) -> bool

@pytest.mark.real_runtime
def test_copilot_read_only_probe_can_list_ticket_without_editing(runtime_env):
    handle = runtime_env.submit_probe_job()
    record = runtime_env.wait_for_terminal_job(handle)
    assert record.status == "complete"
    assert record.exit_code == 0
    calls = runtime_env.observed_tool_calls(handle)
    ticket_get_results = [c for c in calls if c.get("tool") == "ticket_get"]
    assert ticket_get_results, "ticket_get must appear in observed MCP tool calls"
    assert all(c.get("ok") for c in ticket_get_results), "ticket_get must succeed without errors"
    assert runtime_env.project_hash_before == runtime_env.project_hash_after


@pytest.mark.real_runtime
def test_copilot_http_run_keeps_ticket_commit_when_write_is_denied(runtime_env):
    handle = runtime_env.submit_artifact_job(expect_write_denial=True)
    record = runtime_env.wait_for_terminal_job(handle)
    assert record.exit_code == 0
    assert runtime_env.artifact_bytes(handle) is not None, "artifact must be produced"
    assert runtime_env.denial_telemetry(handle), "at least one write-denial event must be recorded"
    assert runtime_env.ticket_done(handle), "ticket must reach done state"
    assert runtime_env.active_run_after(handle) is None, "active_run must be cleared after completion"
    assert runtime_env.protected_hashes_match(handle), "protected file hashes must be unchanged"
```

Also add revoke, stale refresh, multi-ticket same run, optional sign-off, wrong-host, and failed-after-commit live assertions without loosening policy.

- [ ] **Step 2: Run the required live gates in the approved order.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_runtime_live.py -m real_runtime -v`
Expected: first the restricted no-op read path, then the full artifact path, denied write evidence, unchanged protected files, assignment cleanup, revoke handling, and preserved committed transitions after later failure.

- [ ] **Step 3: Run deterministic and UI acceptance after the live gate is green.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/ -m 'not real_runtime' -q`
Expected: pass with the transport change absorbed by deterministic suites.

Run `npm run test:ui`
Expected: approved workflow surfaces remain accessible and visually stable across desktop-light, desktop-dark, mobile-light, and mobile-dark.

- [ ] **Step 4: Run the full suite and whole-branch review before integration.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/ -q`
Expected: full green suite including installed live probes.

Review the branch for auth-context derivation, revoke semantics, cross-job isolation, bounded shutdown, denied-write behavior, original-target cleanup, and stale-transition handling. Repair any findings with regression coverage before the final verification rerun.

- [ ] **Step 5: Commit verified tests and verification evidence separately.**

```bash
git add tests/test_ticket_runtime_live.py tests/test_ticket_end_to_end.py tests/_runtime_probe_helpers.py tests/ui/accessibility.spec.ts tests/ui/dashboard.spec.ts tests/ui/fixtures/config.yaml tests/ui/server.py
git commit -m "test(workflows): verify ticket orchestration"

git add docs/superpowers/verification/2026-09-08-ticket-workflows.md
git commit -m "docs(workflows): record verification evidence"
```

The verification record must contain actual command results, runtime versions, live capability evidence, approved asset comparison notes, and any genuinely unverified requirement. Do not prefill expected totals.