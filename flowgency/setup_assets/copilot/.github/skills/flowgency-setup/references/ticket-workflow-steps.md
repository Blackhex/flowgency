# Ticket Workflow Skill Guidance

Use this reference when writing a ticket-oriented Agent Skill in the global Agent Library. The Agent Skill is selected by an instance routine; do not copy this reference into a project prompt directory.

## Outcome

The routine discovers available tickets, claims one appropriate to its role, advances the ticket through its current workflow transition using live ticket tools, and records findings in semantic memory. Flowgency supplies the configured team workspace, projected blueprint, selected skill, runtime policy, and semantic memory snapshot.

## Recommended steps

1. List open tickets using the live ticket tools supplied by Flowgency. Filter to tickets in states relevant to this agent's responsibilities.
2. Claim one ticket that matches the agent's role and the current transition criteria. Assignment persists; do not expect an expiry or release signal.
3. Inspect the ticket's current state, required transition inputs, and any agent criteria on the available transitions.
4. Perform the work described by the transition criteria with the tools and workspace access the runtime policy grants.
5. When criteria are satisfied, execute the transition by providing required inputs and optional outputs through the live ticket tools. A read-only agent may execute transitions without filesystem write access.
6. If criteria cannot be met, record findings in the ticket's notes field and leave the ticket in its current state. Do not abandon assignment without a transition.
7. Keep durable facts in the semantic memory snapshot selected by the routine.

## Boundaries

Blueprint instructions define reusable role behavior. Config defines identity, integration, capabilities, runtime policy, routines, semantic memory selectors, and workflow instances. The skill must not discover another instance from a native file, widen runtime authority, alter Flowgency configuration, or bypass durable job submission. Ticket tools are only available when the Copilot integration supplies them; other integrations fail closed and cannot execute ticket operations.
