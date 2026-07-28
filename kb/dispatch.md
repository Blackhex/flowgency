# Dispatch And Routines

Agency has one platform-native singleton heartbeat. Global `agency.dispatch.interval` controls how often it checks all groups. Group `dispatch.enabled` and `daily_limit` control whether and how often that group submits work.

Schedules are instance routines, not prompt files. A routine selects one saved prompt from the effective catalog:

```yaml
routines:
  - id: daily-review
    prompt:
      scope: blueprint
      name: daily-review
    arguments: [--brief]
    schedule:
      at: "09:00"
    memory:
      scope: routine
  - id: strategy-review
    prompt:
      scope: instance
      name: strategy-review
    schedule:
      every: 7d
    memory:
      scope: channel
      channel: product-strategy
```

The stable routine ID preserves routine-scoped memory when timing or arguments change. Scheduled runs use persisted memory selectors and snapshot the selected prompt authority before submission. Manual launches from the roster may instead run a saved prompt immediately or submit a one-off task without mutating the routine. The integration must prove it can discover the selected prompt and activate the selected skill when a routine requires one.

Install and inspect the scheduler with:

```text
christag-agency dispatch install --config C:/Agency/config.yaml
christag-agency dispatch status --config C:/Agency/config.yaml
```

## Agent health on the dashboard

The fleet bar colours each agent from its schedule and its last outcome.

- **Gray** — no run on record. The agent has produced no log and no finished job.
- **Green** — the agent has run, nothing is overdue, and the last job did not fail.
- **Amber** — a routine is due. The expected time has passed but is still inside
  the grace window of `agency.dispatch.interval` plus two minutes.
- **Red** — the last finished job failed, or an enabled routine is past its
  expected time by more than the grace window.

Lateness is measured against the markers the dispatch runner writes:
`<logs>/<date>/.event-<agent>-<routine>-<date>` for `at` rules and
`<logs>/.last-<agent>-<routine>` for `every` rules. A routine with no marker and
an `every` schedule produces no signal, because there is no reference point
before its first dispatch. Routines that are disabled or carry a `condition` are
never counted as late, matching what the runner actually fires.

## Superseded layouts

Runtime does not read prompt directories or per-agent schedule maps. Rewrite older schedule sources into instance routines before enabling dispatch.
