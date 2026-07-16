# Agent Identity And Blueprints

Strict canonical separates reusable behavior from configured identity.

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

Agent Detail is the sole identity editor. It patches config with an expected revision; it never edits blueprint source. Agent Library edits reusable source separately.

The standalone migration utility may read superseded native identity files and superseded `.agency-meta.yaml` as source history, then moves identity into explicit instance records. Runtime does not read those superseded files.
