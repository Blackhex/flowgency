# Dispatch And Routines

Agency has one platform-native singleton heartbeat. Global `agency.dispatch.interval` controls how often it checks all groups. Group `dispatch.enabled` and `daily_limit` control whether and how often that group submits work.

Schedules are instance routines, not prompt files. A routine selects a standard Agent Skill:

```yaml
routines:
  - id: daily-review
    skill: daily-review
    schedule:
      at: "09:00"
    memory:
      scope: routine
  - id: strategy-review
    skill: strategic-review
    schedule:
      every: 7d
    memory:
      scope: channel
      channel: product-strategy
```

The stable routine ID preserves routine-scoped memory when timing or arguments change. Scheduled runs use persisted memory selectors. The integration must prove it can discover and activate the selected skill before submission.

Install and inspect the scheduler with:

```text
christag-agency dispatch install --config C:/Agency/config.yaml
christag-agency dispatch status --config C:/Agency/config.yaml
```

## Superseded layouts

Runtime does not read prompt directories or per-agent schedule maps. Rewrite older schedule sources into instance routines before enabling dispatch.
