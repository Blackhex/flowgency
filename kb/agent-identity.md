# Agent Identity And Blueprints

The current model separates reusable behavior from configured identity.

A blueprint is a global Agent Library directory containing reusable `AGENTS.md` instructions and standard Agent Skills. It has no display identity, integration, schedule, permissions, workspace, or mutable memory.

An instance belongs to exactly one group. Its config record owns stable `name`, `blueprint`, explicit `integration`, `identity`, `capabilities`, runtime overrides, routines, and default semantic memory. Display names, titles, and emoji may change without changing stable selectors.

```yaml
- name: advisor
  blueprint: advisor
  integration: copilot
  identity:
    display_name: Advisor
    title: Editorial Advisor
    emoji: "A"
  capabilities:
    write: false
  default_memory:
    scope: agent
```

The Agents page lists group-owned instances. Agent Detail provides `Profile/Blueprint/Runtime/Routines/Memory/Activity`; Profile patches config identity with an expected revision and never edits blueprint source. Identity is display name, title, and emoji. Agent Library edits reusable source separately.

## Superseded layouts

Native identity sidecars and adjacent metadata files are not read by runtime. Identity belongs in explicit instance records.
