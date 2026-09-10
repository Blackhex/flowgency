# Ticket Workflow HTTP Transport Amendment

Date: 2026-09-10

> Amendment notice: This document supersedes only the live-agent transport and runtime wording in Sections 7 and 8 of [2026-09-08-ticket-workflows-design.md](./2026-09-08-ticket-workflows-design.md). All other requirements, ownership rules, UI behavior, storage rules, and acceptance gates from the 2026-09-08 design remain in force.

## 1. Purpose

Task 16C proved that the approved live ticket contract cannot be delivered reliably through the Copilot sandboxed stdio host loopback bridge. The approved replacement is a worker-owned authenticated loopback HTTP MCP endpoint that preserves the existing workflow service authority, fixed tool catalog, read-only workspace sandboxing, job supervision, and default no-LAN posture.

This is a transport amendment, not a product-scope change. It does not add new ticket features, broaden agent permissions, or relax the existing assignment, active-run, or storage guarantees. The only exception to the earlier no-LAN wording is an explicit per-agent Copilot opt-in for sandboxed ticket runs.

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
- Keep loopback traffic private to the worker job. The default remains no local-network access. The only exception is an explicit per-agent Copilot opt-in for sandboxed ticket runs; absent or false does not widen network policy. Do not grant broader outbound access or shell access.

## 5. Runtime Contract Changes

- `RuntimeCapabilities` advertises live ticket support explicitly as `mcp-http` or `None`; support is never inferred from native files or guessed from prior adapters.
- Unsupported or fake adapters stay unsupported. CLI and non-ticket workflows remain unaffected.
- `TicketToolLaunch` may change from command/args/env to HTTP launch metadata such as `url`, redacted `headers`, `server_name`, and lifecycle hooks. It remains runtime-only and is not persisted into canonical job state.
- Copilot receives a per-job JSON MCP config pointing only to the Flowgency server, then a matching allow-tool restriction. Inspect the installed CLI help and config schema before fixing the exact JSON keys.
- Do not disable all MCP sandboxing globally just to admit the Flowgency server. In particular, do not rely on `sandboxMcpServers=false` if that widens unrelated stdio servers.
- The canonical per-agent setting is `integration_config.allow_local_network`, stored only as a YAML boolean. Absent means `false`; strings and truthy parsing are not accepted.
- `CopilotIntegration.with_config(...)` returns a configured integration instance without mutating global integration state and without dropping unrelated `integration_config` keys.
- The Runtime page for Copilot agents adds a checkbox labeled `Allow local-network access` with the disclosure `Allows connections to local services and LAN hosts, not only Flowgency.`
- Runtime save keeps other integration settings intact, distinguishes an unchecked checkbox (`false`) from an omitted field on older forms, and does not rewrite existing agent configs unless the user submits the runtime form.
- `validate_config` and `validate_run` surface the same incompatibility for ticket-enabled sandboxed Copilot runs when `integration_config.allow_local_network` is not explicitly `true`. The validation issue code is `ticket-local-network-required`, the field is `integration_config.allow_local_network`, and the hint directs the user to either enable the broad-scope runtime checkbox or choose a non-ticket unrestricted run.
- When the flag is explicitly `true`, generated per-job sandbox settings include `sandbox.userPolicy.network.allowLocalNetwork = true` for that job only. When the flag is absent or `false`, the generated settings omit that field and do not widen or deny beyond the existing defaults.
- Workspace read-only behavior, git/gh credential boundaries, confirmed process-stop requirements, and Windows `run_supervised` tree containment remain unchanged.
- Immutable job snapshots continue to preserve the resolved `integration_config` value used for launch so older jobs remain readable and later config edits do not rewrite past launches.

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
- Deterministic and live coverage must reject ticket-enabled sandboxed Copilot launches before start when `integration_config.allow_local_network` is absent or `false`, and must prove that an explicit opt-in produces the per-job local-network grant without widening unrelated sandbox policy.
- Live acceptance uses the explicit opt-in for the remaining restricted runtime sequence: the required no-op read run, then the full artifact-producing run, denied write attempt, unchanged-files check, assignment cleanup, stale refresh, optional sign-off, multi-ticket behavior, and failed-after-commit behavior already required by Task 16.
- The pending real-project verification result remains required. Do not replace it with a synthetic claim that an agent merely returned a known string.
- The read-only agent and no-shell policy remain intact. If a prompt asks the agent to attempt a write, that is a denial-path test, not a policy relaxation.

## 9. Out of Scope

- No new tracking features, workflow behaviors, or product scope.
- No automatic config rewrite to enable local-network access, no all-network grant, no external callback channel, and no dashboard-owned ticket broker.
- No migration of canonical job records to persist transport secrets.
- No speculative adapter support for runtimes that cannot prove an equivalent per-launch HTTP MCP contract.