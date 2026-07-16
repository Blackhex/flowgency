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

## superseded v1 migration

superseded schedules must be converted outside runtime with `agency-migration`:

```text
python tools/migrate_agent_model.py preview --config config.yaml --plan migration-plan.yaml
python tools/migrate_agent_model.py apply --plan migration-plan.yaml
python tools/migrate_agent_model.py verify --config config.yaml
python tools/migrate_agent_model.py rollback --plan migration-plan.yaml
```
