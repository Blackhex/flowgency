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
- Test: `tests/test_ticket_access.py`, `tests/test_ticket_broker.py`, `tests/test_ticket_mcp.py`, `tests/test_ticket_runtime_capabilities.py`, `tests/test_copilot_ticket_tools.py`, `tests/test_copilot_launch_arguments.py`, `tests/test_copilot_credentials.py`

**Interfaces:**
- Consumes: `TicketAccessRegistry.open(authority) -> TicketAccessGrant`, the existing typed ticket command parser/dispatcher, and current Copilot launch plumbing.
- Produces: `LiveTicketEndpoint(url: str, headers: Mapping[str, str], server_name: str)` with worker-owned lifecycle.
- Produces: `TicketBroker.start() -> LiveTicketEndpoint` and `close()` that revoke access and join bounded server threads.
- Produces: `RuntimeCapabilities.live_ticket_transport: Literal["mcp-http"] | None`.
- Produces: `TicketToolLaunch(url: str, headers: Mapping[str, str], server_name: str = "flowgency-tickets")` with secret-redacted repr and no canonical serialization.
- Produces: `build_ticket_tool_launch(endpoint) -> TicketToolLaunch` and a Copilot per-job MCP config writer that emits only the Flowgency HTTP server entry.

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
    max_request_body_size=4_194_304,
    host="127.0.0.1",
)

async def guard_request(request: Request) -> AgentTicketContext:
    verify_loopback_host(request)
    verify_origin(request)
    require_bearer(request.headers["Authorization"])
    return registry.authenticate(token)
```

Reuse the existing typed parser/dispatcher for all MCP tool calls. Keep the outer 2 MiB request cap, revoke-on-close behavior, per-call authority validation, result-size limits, and current artifact/state semantics. Generate a per-job Copilot MCP config that points only to the authenticated Flowgency HTTP server, pass it with `--additional-mcp-config`, and keep the allowlist limited to the Flowgency server name.

- [ ] **Step 4: Run GREEN checks, review secrecy boundaries, and verify repository-boundary hygiene.**

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_access.py tests/test_ticket_broker.py tests/test_ticket_mcp.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q`
Expected: pass with explicit `mcp-http` capability, unchanged ticket semantics, and no token leakage.

Run `./.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_repository_boundaries.py -q`
Expected: pass, confirming no forbidden tracked terminology or boundary regressions.

- [ ] **Step 5: Review and commit the implementation slice.**

```bash
git add flowgency/tickets/access.py flowgency/tickets/broker.py flowgency/tickets/protocol.py flowgency/tickets/mcp_server.py flowgency/integrations/models.py flowgency/integrations/ticket_tools.py flowgency/integrations/flowgency/copilot.py tests/test_ticket_access.py tests/test_ticket_broker.py tests/test_ticket_mcp.py tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py
git commit -m "feat(runtime): switch ticket tools to mcp http"
```

### Task 2: Re-run live runtime acceptance and full feature gates under HTTP transport

**Files:**
- Modify: `tests/test_ticket_runtime_live.py`, `tests/test_ticket_end_to_end.py`, `tests/_runtime_probe_helpers.py`
- Modify: `tests/ui/accessibility.spec.ts`, `tests/ui/dashboard.spec.ts`, `tests/ui/fixtures/config.yaml`, `tests/ui/server.py`
- Modify: `docs/superpowers/verification/2026-09-08-ticket-workflows.md`

**Interfaces:**
- Consumes: Task 1 `mcp-http` capability and worker-owned `LiveTicketEndpoint` launch path.
- Produces: live proof that Copilot can initialize, list tools, read tickets in a restricted run, complete the verified artifact path, deny workspace writes under read-only policy, and preserve assignment/cleanup semantics.
- Produces: full deterministic, UI, and branch-level verification evidence for the ticket-workflows feature.

- [ ] **Step 1: Extend the failing live tests to match the approved HTTP acceptance sequence.**

```python
@pytest.mark.real_runtime
def test_copilot_read_only_probe_can_list_ticket_without_editing(runtime_env):
    handle = runtime_env.submit_probe_job()
    record = runtime_env.wait_for_terminal_job(handle)
    assert record.status == "complete"
    assert runtime_env.project_hash_before == runtime_env.project_hash_after


@pytest.mark.real_runtime
def test_copilot_http_run_keeps_ticket_commit_when_write_is_denied(runtime_env):
    handle = runtime_env.submit_artifact_job(expect_write_denial=True)
    record = runtime_env.wait_for_terminal_job(handle)
    assert runtime_env.ticket_transition_committed()
    assert runtime_env.protected_files_unchanged()
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