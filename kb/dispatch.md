# Dispatch And Routines

Agency has one platform-native singleton heartbeat. Global `agency.dispatch.interval` controls how often it checks all teams. Team `dispatch.enabled` controls whether that team submits work.

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

## Recovery

When the dispatch runner starts a cycle, it checks whether any routine missed an
occurrence while the runner was stopped or momentarily late. A routine is recovered
when its most recent occurrence at or before now carries no marker and is still
within the routine's `catch_up` bound.

There is exactly one candidate occurrence per routine per cycle. The runner never
replays a backlog; it recovers at most the last missed occurrence.

`schedule.catch_up` controls how far back recovery reaches:

| Value | Meaning |
|-------|---------|
| `none` | No recovery. The occurrence fires only inside the ordinary grace window of `agency.dispatch.interval` plus two minutes. |
| `today` | Recover any occurrence on the current calendar date. This is the default when `catch_up` is absent. |
| `always` | Recover any occurrence regardless of age. |
| `30m`, `8h`, `7d`, … | Recover any occurrence within the specified duration. The grammar is the same as `every`. |

The marker for a recovered `at` occurrence records the occurrence's own calendar
day, not today's date. A 03:00 recovery of yesterday's 08:00 writes yesterday's
marker and leaves today's 08:00 still ahead. For an `every` routine the marker
timestamp is set to the occurrence rather than the launch moment, so a late
recovery does not push subsequent occurrences later.

## Job queue

`agency.jobs.pool` caps the number of concurrently running workers across the
whole installation. The default is 4; the minimum is 1.

When a job is submitted and the pool has a free slot it launches immediately.
When the pool is full the job waits in the `queued` status until a slot opens.
Jobs drain in ascending due-time order, ties broken by job id, globally across
all teams.

Draining runs at the end of every submission, inside every worker as it exits,
and at the start of every dispatch cycle. A backlog drains through worker exits
with no browser open and no external timer.

If the pool is full and no live worker or installed platform timer exists that
could ever open a slot, submission is refused with an error rather than parking
work indefinitely.

## Agent health on the dashboard

The fleet bar colours each agent from its schedule and its last outcome.

- **Gray** — no run on record. The agent has produced no log and no completed or
  failed job. A cancelled job is not a run and still leaves the agent gray.
- **Green** — the agent has run, nothing is overdue, and the last job did not fail.
- **Amber** — a routine is due. The expected time has passed but is still inside
  the grace window of `agency.dispatch.interval` plus two minutes.
- **Red** — the newest executed job failed, or an enabled routine is past its
  expected time by more than the grace window.

Lateness is measured against the markers the dispatch runner writes:
`<logs>/<date>/.event-<agent>-<routine>-<date>` for `at` rules and
`<logs>/.last-<agent>-<routine>` for `every` rules. A routine with no marker and
an `every` schedule produces no signal, because there is no reference point
before its first dispatch. Routines that are disabled or carry a `condition` are
never counted as late, matching what the runner actually fires.

## Superseded layouts

Runtime does not read prompt directories or per-agent schedule maps. Rewrite older schedule sources into instance routines before enabling dispatch.
