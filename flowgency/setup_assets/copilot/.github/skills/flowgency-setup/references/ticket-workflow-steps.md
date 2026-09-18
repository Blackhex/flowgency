# Ticket Workflow Skill Guidance

Use this reference when writing a ticket-oriented Agent Skill in the global Agent Library. The Agent Skill is selected by an instance routine; do not copy this reference into a project prompt directory.

## Outcome

The routine discovers available tickets, claims one appropriate to its role, advances the ticket through its current workflow transition using live ticket tools, and records findings in semantic memory. Flowgency supplies the configured team workspace, projected blueprint, selected skill, runtime policy, and semantic memory snapshot.

## Recommended steps

1. List open tickets using the live ticket tools supplied by Flowgency. Filter to tickets in states relevant to this agent's responsibilities.
2. Claim one ticket that matches the agent's role and the current transition criteria. Assignment persists; do not expect an expiry or release signal.
3. Inspect the current transition's inputs, required and optional outputs, preconditions, and criteria before starting work.
4. Perform the work described by the transition criteria with the tools and workspace access the runtime policy grants.
5. When criteria are satisfied, execute the transition, submitting durable results in the transition's outputs mapping. Attempt-only inputs do not update saved ticket fields. A read-only agent may execute transitions without filesystem write access. For restricted Copilot runs, require explicit per-agent `integration_config.allow_local_network: true` consent before assuming the tools are reachable; absent or `false` fails preflight with `ticket-local-network-required`.
6. If required results are unavailable, report the blocker with `ticket_report` and leave the ticket in its current state. Reports and logs do not populate output fields; do not substitute a report or a field update for a required transition output. Do not abandon assignment without a transition.
7. Keep durable facts in the semantic memory snapshot selected by the routine.

## Boundaries

Blueprint instructions define reusable role behavior. Config defines identity, integration, capabilities, runtime policy, routines, semantic memory selectors, and workflow instances. The skill must not discover another instance from a native file, widen runtime authority, alter Flowgency configuration, or bypass durable job submission. Ticket tools are only available when the Copilot integration supplies them; other integrations fail closed and cannot execute ticket operations. Do not silently grant broader local-network access, `allowOutbound`, or a sandbox bypass to make ticket workflows work.

## Git evidence

A field declared with `artifact_format: git-change` expects trusted local Git evidence, not an ordinary file or URL. Capture it with `ticket_capture_git_evidence(version, operation_id, transition_id, field_id, base_commit, end_commit, publication_ref=None)`, which returns the same `TicketToolResponse` envelope as every other ticket tool: a serialized capture result carrying the retained artifact reference and the ticket's new version.

Treat committing and capturing as two separate actions. First do the project-authorized commit work in the workspace, choosing the exact `base_commit` and `end_commit` object ids the capture should cover, and push separately only if the project's own instructions require a remote. Only then call `ticket_capture_git_evidence` to record that already-committed local range as this ticket's evidence and submit the returned artifact through `ticket_transition`'s outputs like any other field.

Capture never commits, pushes, fetches, or verifies a push, and it never makes a commit outside the selected range attributable to the ticket. A written report, an uploaded patch file, or a link to a remote web URL is not a substitute for a captured Git-format result: none of them exercise this tool, and none of them establish trust the way a real capture does. If capture fails because no Git-evidence policy is configured for the team, that is a configuration gap to raise, not something to work around with an ordinary artifact.

