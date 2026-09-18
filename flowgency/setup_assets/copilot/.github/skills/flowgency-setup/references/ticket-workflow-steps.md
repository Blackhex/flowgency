# Ticket Workflow Skill Guidance

Use this reference when writing a ticket-oriented Agent Skill in the global Agent Library. The Agent Skill is selected by an instance routine; do not copy this reference into a project prompt directory.

## Outcome

The routine discovers available tickets, claims one appropriate to its role, advances the ticket through its current workflow transition using live ticket tools, and records findings in semantic memory. Flowgency supplies the configured team workspace, projected blueprint, selected skill, runtime policy, and semantic memory snapshot.

## Recommended steps

1. List open tickets using the live ticket tools supplied by Flowgency. Filter to tickets in states relevant to this agent's responsibilities.
2. Claim one ticket that matches the agent's role and the current transition criteria. Assignment persists; do not expect an expiry or release signal.
3. Inspect the current transition's inputs, required and optional outputs, preconditions, and declared criteria before starting work. Inputs and preconditions are not criteria and are never assessed as one.
4. Perform the work described by the transition's declared criteria with the tools and workspace access the runtime policy grants.
5. When the declared criteria are satisfied, execute the transition, submitting durable results in the transition's outputs mapping and one assessment per declared criterion; submit an empty assessments list when the transition declares none. Attempt-only inputs do not update saved ticket fields. A read-only agent may execute transitions without filesystem write access. For restricted Copilot runs, require explicit per-agent `integration_config.allow_local_network: true` consent before assuming the tools are reachable; absent or `false` fails preflight with `ticket-local-network-required`.
6. If required results are unavailable, report the blocker with `ticket_report` and leave the ticket in its current state. Reports and logs do not populate output fields; do not substitute a report or a field update for a required transition output. Do not abandon assignment without a transition.
7. Keep durable facts in the semantic memory snapshot selected by the routine.

## Boundaries

Blueprint instructions define reusable role behavior. Config defines identity, integration, capabilities, runtime policy, routines, semantic memory selectors, and workflow instances. The skill must not discover another instance from a native file, widen runtime authority, alter Flowgency configuration, or bypass durable job submission. Ticket tools are only available when the Copilot integration supplies them; other integrations fail closed and cannot execute ticket operations. Do not silently grant broader local-network access, `allowOutbound`, or a sandbox bypass to make ticket workflows work.

## Git evidence

A field declared with `artifact_format: git-change` expects trusted local Git evidence, not an ordinary file or URL. Capture it with `ticket_capture_git_evidence(version, operation_id, transition_id, field_id, base_commit, end_commit, publication_ref=None)`, which returns the same `TicketToolResponse` envelope as every other ticket tool: a serialized capture result carrying the retained artifact reference and the ticket's new version.

### What the policy is

A team's Git-evidence policy has exactly two settings. `mode` is `local`, and `local` is the only value that exists. `allowed_refs` optionally restricts which of the workspace's own `refs/heads/...` or `refs/tags/...` names a capture may cite; an empty list means no local-ref restriction. The optional `publication_ref` argument names one such local ref in this same workspace, and it is checked against the local object graph only — it is never a record of a push, and leaving it out is normal.

Nothing in that policy addresses remotes. There is no remote mode, no remote or branch name, no credential, no authentication setting, and no push verifier — none absent by oversight, none planned, none merely switched off. Do not ask for one, do not propose adding one, do not describe an argument or configuration field that the tool schemas in front of you do not contain, and do not treat a policy that is not configured as one you may assume or grant.

### The sequence of work

1. Do the project's commit work through the project's own authorized workflow. If the project's instructions also require a push, a pull request, a review, or any other publication step, carry that out the same way, with the same project tooling you would use if Flowgency were not involved. Those obligations belong to the project; Flowgency neither performs them nor checks them.
2. Choose the exact `base_commit` and `end_commit` object ids that bound the committed work.
3. Call `ticket_capture_git_evidence` for the target transition and field.
4. Submit the returned `artifact` as that field's value through `ticket_transition`'s outputs, and send the complete `version` object that the capture returned. Capture advances the ticket, so the version you read before capturing is stale: copy the returned `ref`, `revision`, `workflow_digest` and `context_digest` as they came back instead of editing individual parts of an older version.

### What a result proves, and what it does not

A successful receipt proves that the selected commits exist in this workspace's repository, that the retained patch is their content, that the capture is bound to this ticket and to the agent and job that made it, and that any `publication_ref` you supplied satisfied the local-ref checks in force. It proves nothing beyond that. It is not a code review, not a passing test, not a delivery, and not a push. If you also want to state that the work was reviewed, that tests passed, or that it reached a remote, say so as the separate prior fact it is, and only when you actually hold that fact; never present it as something the capture established.

An error proves even less. A request rejected before Git runs — for example because the team has no Git-evidence policy configured — has not examined your commits, your range, or your workspace at all, so it is evidence neither that they are valid nor that they are faulty. Report the error's code and what it is actually about, and draw no conclusion the tool did not reach. A failed capture leaves the ticket unchanged.

Capture never commits, pushes, fetches, or verifies a push, and it never makes a commit outside the selected range attributable to the ticket. A written report, an uploaded patch file, or a link to a remote web URL is not a substitute for a captured Git-format result: none of them exercise this tool, and none of them establish trust the way a real capture does. When required Git evidence cannot be produced — the policy is missing, or the project's own publication step could not be completed — report that with `ticket_report`, leave the ticket in its current state, and let whoever administers the configuration or the project act on it.

