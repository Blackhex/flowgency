# Agent Identity And Blueprints

The current model separates reusable behavior from configured identity.

A blueprint is a global Agent Library directory containing reusable `AGENTS.md` instructions, standard Agent Skills, and shared prompts. It has no display identity, integration, schedule, permissions, workspace, or mutable memory.

An instance belongs to exactly one team. Its config record owns stable `name`, `blueprint`, explicit `integration`, `identity`, permissions, registered private prompts, routines, and default semantic memory. Display names, titles, and emoji may change without changing stable selectors.

```yaml
- name: advisor
  blueprint: advisor
  integration: copilot
  identity:
    display_name: Advisor
    title: Editorial Advisor
    emoji: "A"
  permissions:
    mode: restricted
    rules:
      - path: editorial
        tools: [read, search]
  default_memory:
    scope: agent
```

The Agents page lists team-owned instances. Agent Detail provides `Profile/Blueprint/Runtime/Permissions/Prompts/Routines/Memory/Activity/Logs`; Profile patches config identity with an expected revision and never edits blueprint source. Activity summarizes recent runs and ticket-linked history for the agent, while Logs lists that agent's readable execution log files with scoped return links through the shared log viewer. Identity is display name, title, and emoji. Agent Library edits reusable source separately.

Ticket assignment binds a ticket to an instance by its stable `name`, not its display identity. Assignment is persistent ownership: changing an instance's display name, title, or emoji leaves its ticket assignments untouched. Only agents move tickets between workflow states; sign-off is optional.

## Superseded layouts

Native identity sidecars and adjacent metadata files are not read by runtime. Identity belongs in explicit instance records.
