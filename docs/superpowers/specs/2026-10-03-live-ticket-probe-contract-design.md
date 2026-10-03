# Live Ticket Probe Contract

Date: 2026-10-03
Branch: `feature/setup-terminal-font-metrics`
Purpose: Approved gate correction alongside the paused terminal-font fix.

## Evidence

The unchanged full gate and one focused retry failed in live Copilot ticket
acceptance. The read-only probe made no broker call and claimed the supplied
reference lacked identifiers, although its actual prompt contains binding,
team, workflow, and ticket identifiers. The appended reporting protocol says
the granted tool allowlist is `read, search`, without distinguishing workspace
filesystem operations from the separately supplied live ticket channel.

The completion/sign-off probe rejected safe observed recovery. In one run the
agent retried a stale completion using the initial operation name. In the next
it first attempted sign-off before work started, received `not-working`, then
started work, read the fresh version, and signed off successfully with a new
operation ID. The test only inspected `signoff-b-1`, not that accepted recovery
or the verified final state.

The font branch changes only terminal JavaScript, UI tests, and documentation;
it does not change ticket behavior. These findings are a separately approved
test/guidance correction, not permission to weaken the integration gate.

## Approved Design

### Permission Guidance

Clarify the reporting protocol's tool policy as the workspace/filesystem tool
boundary. Explain that live Flowgency ticket tools, when actually supplied,
are governed by the authenticated per-job ticket channel and do not require
workspace write permission. Missing ticket tools remain a reported blocker;
never imply every integration exposes them or infer network consent.

Do not change CLI grants, sandbox policy, authentication, authorization, tool
inventory, transport, path permissions, or configured local-network consent.
No additional tool becomes accessible through prose.

### Live Acceptance

Evaluate actual, ticket-correlated broker events and retained final state rather
than incidental probe operation-ID names:

- Read-only: at least one successful `ticket_get` on the exact selected ticket;
  no mutating calls, complete ref supplied, MCP inventory and protected hashes
  still checked.
- Stale completion: force one concurrent version bump on the intended ticket's
  first completion attempt. Observe a real `stale-ticket` rejection, a subsequent
  successful read with the refreshed version, and a successful completion using
  that refreshed version. Require the intended final state and retained result.
- Sign-off: observe successful work start on the intended second ticket before
  successful sign-off using its current version, with assignment and active-run
  state cleared afterward. Earlier denied attempts may be retained as evidence;
  they never count as success.
- Require valid nonempty operation IDs and normal service idempotency semantics,
  but do not require the agent to choose a particular human-authored ID string.

The observer may retain minimal safe request/response version evidence for
causality. Never record credentials, bearer tokens, private headers, or raw
unrelated payloads. Preserve the existing authenticated broker boundary.

### Deterministic Coverage

Add real deterministic contract cases demonstrating valid recovery is accepted
and each missing behavior fails: absent read, wrong ticket, missing stale
rejection, retry without a fresh read/version, no work start, no successful
sign-off, or incorrect final state. Reuse existing ticket tests, broker harness,
and observer helpers rather than new broad frameworks.

Keep ordinary protocol tests and non-ticket/no-tool behavior covered. Test the
consumer-visible corrected guidance and real contract outcomes; do not add
checks that merely grep arbitrary prose.

## Verification And Scope

Use failure-first deterministic checks, then the two exact installed-Copilot
acceptance probes without model changes, retries-until-pass, or marker exclusions.
Authentication/quota/connection/timeout failures remain failures, not skips.
Record actual observations and evaluate causality rather than trusting the
agent's final summary or exit code alone.

If corrected probes pass, rerun the full Python and browser gates sequentially
for the combined branch, then whole-branch review and the pre-authorized master
verification/publication/cleanup sequence. Keep all prior font tests, source,
snapshots, and safety constraints intact.

No live user session input/restart, existing configuration/ticket edits,
permission broadening, model substitution, or unrelated backend repairs.

## Alternatives

- Approved: distinguish the permission boundaries and validate strict semantic
  broker evidence, allowing safe recovery with fresh operation names.
- Not selected: retain exact operation names and change only prompts. This is
  smaller but keeps acceptance dependent on incidental AI instruction naming.
- Rejected: ignore live failures, accept exit code alone, exclude markers, or
  change permissions/model to obtain green output.

## Acceptance Criteria

1. Reporting guidance does not falsely exclude supplied ticket tools from a
   read/search-only agent's permitted channel.
2. The read-only probe cannot pass without the required successful broker read.
3. The stale probe cannot pass without denied stale mutation and fresh-version
   causal recovery on the correct ticket.
4. Sign-off cannot pass without successful work start, accepted sign-off, and
   correct retained final state; valid recovery need not use one fixed ID name.
5. Security, protected-state, real CLI, and font-fix requirements remain strict.