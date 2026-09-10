# Ticket Workflow HTTP Transport Amendment

Date: 2026-09-10

> Amendment notice: This document supersedes only the live-agent transport and runtime wording in Sections 7 and 8 of [2026-09-08-ticket-workflows-design.md](./2026-09-08-ticket-workflows-design.md). All other requirements, ownership rules, UI behavior, storage rules, and acceptance gates from the 2026-09-08 design remain in force.

## 1. Purpose

Task 16C proved that the approved live ticket contract cannot be delivered reliably through the Copilot sandboxed stdio host loopback bridge. The approved replacement is a worker-owned authenticated loopback HTTP MCP endpoint that preserves the existing workflow service authority, fixed tool catalog, read-only workspace sandboxing, job supervision, and no-LAN posture.

This is a transport amendment, not a product-scope change. It does not add new ticket features, broaden agent permissions, or relax the existing assignment, active-run, or storage guarantees.

## 2. Approved Transport Decision

- The real Copilot host connects to a worker-owned MCP HTTP server by URL plus Authorization header.
- The worker exposes the official SDK HTTP server on `127.0.0.1` with an ephemeral port under the same private job broker process. There is no child MCP process for Copilot.
- The sandboxed stdio MCP bridge is removed for Copilot. Do not replace it with file IPC, runtime event automation, or another helper process.
- Raw internal HTTP operations and `TicketToolClient` may remain for trusted internal consumers and tests if they reuse the same authenticated dispatch path.
- The domain handlers stay singular. Do not build duplicate ticket-operation implementations for HTTP versus MCP callers.

## 3. Authority and Auth Boundary

- The fixed MCP tool catalog continues to share the existing typed `parse_ticket_command` and authenticated dispatch boundary.
- Bearer authentication is required on every MCP HTTP request, including initialize, tools/list, and tools/call.
- Authentication derives current actor context from registered live job authority on each call. A token does not prebind an actor that remains valid after revoke, refingerprint, or job shutdown.
- Closing or revoking the session, or changing the authoritative job fingerprint, blocks later calls immediately.
- Tokens are ephemeral per-job MCP launch material only. Do not persist them in `JobSpec`, task text, CLI args, logs, repr output, or canonical records.
- Expose only the MCP URL and redacted headers through runtime launch data. No config path, storage root, team override, or agent-name override is caller-controlled.

## 4. HTTP Security and Limits

- Bind only `127.0.0.1` on an ephemeral port owned by the worker.
- Require exact Host or origin matching for that loopback endpoint. `Origin` must be absent or match the trusted loopback origin.
- Keep CORS disabled and redirects off.
- Cap request bodies at 2 MiB even if the inner SDK default is larger.
- Preserve the existing streamed output and header/result size bounds, including the 64 KiB result contract already enforced for ticket tool responses.
- Keep loopback traffic private to the worker job. Do not grant `allowLocalNetwork`, broader outbound access, or shell access.

## 5. Runtime Contract Changes

- `RuntimeCapabilities` advertises live ticket support explicitly as `mcp-http` or `None`; support is never inferred from native files or guessed from prior adapters.
- Unsupported or fake adapters stay unsupported. CLI and non-ticket workflows remain unaffected.
- `TicketToolLaunch` may change from command/args/env to HTTP launch metadata such as `url`, redacted `headers`, `server_name`, and lifecycle hooks. It remains runtime-only and is not persisted into canonical job state.
- Copilot receives a per-job JSON MCP config pointing only to the Flowgency server, then a matching allow-tool restriction. Inspect the installed CLI help and config schema before fixing the exact JSON keys.
- Do not disable all MCP sandboxing globally just to admit the Flowgency server. In particular, do not rely on `sandboxMcpServers=false` if that widens unrelated stdio servers.
- Workspace read-only behavior, git/gh credential boundaries, confirmed process-stop requirements, and Windows `run_supervised` tree containment remain unchanged.

## 6. SDK and Server Requirements

- Use the official Python MCP SDK already installed for this branch. The verified import surface is `from mcp.server import MCPServer`.
- Build the HTTP surface with `MCPServer.streamable_http_app(...)`, using `json_response=True` and `stateless_http=True`.
- Integrate startup and shutdown through an `asynccontextmanager`, a readiness event, and bounded thread/process joins. The endpoint must be available before the Copilot CLI starts.
- Keep the outer authorization and size checks strict even if the SDK route is mounted beneath `/mcp`.
- Avoid a second inner loopback client that blocks the event loop. Use async dispatch, a threadpool hop, or another proved non-blocking adapter.
- Keep the SDK JSON-RPC implementation. Do not replace it with a custom protocol stack.

## 7. Fixed Tool Catalog

The live catalog stays fixed and job-scoped:

- `workflows_list`
- `tickets_list`
- `ticket_get`
- `ticket_create`
- `ticket_start_work`
- `ticket_update`
- `ticket_report`
- `ticket_transition`
- `ticket_end_work`
- `ticket_sign_off`
- `ticket_artifact_publish`

These tools keep the existing typed contracts, request-digest rules, and current-target validation. They do not expose user reassignment, workflow/config mutation, shell access, or arbitrary file reads.

## 8. Required Verification

- Deterministic RED and GREEN coverage must use a real HTTP SDK client to exercise initialize, tools/list, tools/call, unauthorized token, wrong Host or Origin, oversized body, revoke, and job separation.
- Deterministic tests must also verify that ticket state, artifact behavior, and error envelopes remain unchanged under the new transport.
- Live acceptance starts with the required restricted no-op read run, then the full artifact-producing run, denied write attempt, unchanged-files check, assignment cleanup, stale refresh, optional sign-off, multi-ticket behavior, and failed-after-commit behavior already required by Task 16.
- The pending real-project verification result remains required. Do not replace it with a synthetic claim that an agent merely returned a known string.
- The read-only agent and no-shell policy remain intact. If a prompt asks the agent to attempt a write, that is a denial-path test, not a policy relaxation.

## 9. Out of Scope

- No new tracking features, workflow behaviors, or product scope.
- No LAN exposure, external callback channel, or dashboard-owned ticket broker.
- No migration of canonical job records to persist transport secrets.
- No speculative adapter support for runtimes that cannot prove an equivalent per-launch HTTP MCP contract.