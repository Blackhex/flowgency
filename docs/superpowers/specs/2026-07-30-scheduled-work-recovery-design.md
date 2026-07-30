# Scheduled Work Recovery And The Job Pool

Scheduled work survives the machine being unavailable, and all local backend work
runs through a bounded pool.

## Problem

On 2026-07-29 the routine `duncan/suite-health`, scheduled `at: "08:00"`, did not
run. Nothing was broken. The machine slept at 22:04 the previous evening and
resumed at 11:40; the `AgencyDispatch` task first fired at 11:57 and has run
every fifteen minutes since. Gurney's 18:00 routine ran normally the same day.

The runner fires an `at` rule only inside a window of `interval + 2` minutes
after the target time. The 08:00 window closed at 08:17 while the machine was
asleep, so the occurrence was skipped, its marker was never written, and the
dashboard has shown the agent red ever since. There is no recovery path: a
missed occurrence is lost, permanently and silently.

Recovering missed work introduces a second problem. A machine opened after a
long absence can make several routines eligible in the same cycle, and each one
launches a real backend process against the same workspace. Today submission
launches a detached worker immediately and unconditionally, so nothing bounds
that burst. Recovery is only safe if execution is bounded, which is why both
belong to one change.

## Non-goals

- No new resident process, service, or daemon.
- No change to the marker file layout, the log directory shape, or the meaning
  of the health colours.
- No backlog replay. A routine recovers at most one occurrence.
- No reordering controls. Queue order is derived, never edited.

## Configuration

### `agency.jobs.pool`

```yaml
agency:
  jobs:
    pool: 4
```

Caps concurrently running workers across the whole installation. Default `4`,
validated as an integer `>= 1`. The constraint being expressed is this machine
and this account, neither of which is a per-group resource, so the setting is
global.

### `schedule.catch_up`

```yaml
routines:
  - id: suite-health
    schedule:
      at: "08:00"
      catch_up: always
```

Accepted values:

| Value | Meaning |
| --- | --- |
| `none` | No recovery. The occurrence fires only inside the ordinary dispatch grace window of `interval + 2` minutes, which is today's behavior. |
| `today` | Recover the occurrence if it falls on the current calendar day. |
| `always` | Recover the most recent missed occurrence regardless of its age. |
| duration | Recover if `now - occurrence <= duration`. Grammar is the `every` grammar: `30m`, `8h`, `7d`. |

When the key is absent the value is `today`, whatever the cadence. A missed
occurrence is recovered for the rest of the day it belonged to and forgotten at
midnight. Recovery across a longer absence is opted into per routine with
`always` or an explicit duration.

`ScheduleRule` is `extra="forbid"`, so `catch_up` is an explicit, validated
field, and every behavior the system can have is a value a user can write.

### Removal of `dispatch.daily_limit`

`groups.<group>.dispatch.daily_limit` is deleted. `GroupDispatch` is
`extra="forbid"`, so a configuration still carrying the key is rejected with a
validation error naming it. `dispatch.enabled` is unchanged.

The setting bounded cumulative starts per group per day, which is an axis
neither the pool nor `catch_up` covers. It is removed deliberately rather than
by oversight: concurrency is bounded by the pool, recovery amplification is
bounded by `catch_up`, and a group's daily volume is now whatever its schedules
ask for. Removing it also removes a documented lie — a group at its limit
skipped routines without writing markers, so the dashboard painted them overdue
while the runner was healthy.

Migration is manual and deliberate. `config.yaml` is runtime-local authority and
is never rewritten by the implementation, so an installation carrying
`daily_limit` fails validation until the key is deleted by hand. The tracked
`config.yaml.example` and the configurations under `examples/` are updated as
part of the change.

## Occurrence recovery

The firing rule stops being a window and becomes a single predicate: *the most
recent occurrence at or before now, if it has no marker and is still inside the
effective `catch_up`.*

There is exactly one candidate occurrence, never a list. For a daily `at` it is
today's occurrence when now is past it, otherwise yesterday's. An older
occurrence is never considered, because a newer one having run makes it moot and
a newer one having been missed makes it the candidate instead. "Run at most the
last one" is therefore a property of the definition, not a rule layered on top.

### `at`

```text
occurrence = today at HH:MM if now >= that time, else yesterday at HH:MM
marker     = <logs>/<occurrence date>/.event-<agent>-<routine>-<occurrence date>
```

If the marker exists, nothing runs. If it does not and the occurrence is inside
the bound, the routine runs once and the marker is written **for the
occurrence's own day**, not for today. A 03:00 recovery of yesterday's 08:00
therefore records yesterday as run and correctly leaves today's 08:00 ahead.

### `every`

The `.last-<agent>-<routine>` marker is the anchor. The candidate occurrence is
the latest `marker + n × period` at or before now. The bound is tested against
that occurrence's age, not against the marker's.

On a run the marker's modification time is set to the occurrence, not to the
launch moment. This keeps the cadence anchored: a recovery that happens four
hours late does not push every later occurrence four hours later. It is a
deliberate change to existing drift behavior.

### What does not change

Marker paths, the per-day log directories, `agency/health.py`, and the health
colours all keep their present meaning. Markers are written at submission, so a
recovered routine stops reading overdue the moment it is queued, and the runner
and the dashboard cannot disagree.

## Queue and pool

### States

`queued` is already a legal job status with legal transitions, `ACTIVE_STATUSES`
already includes it, and `reconcile_jobs` already reaps only `running` and
`waiting_for_memory`. A job that is `queued` across a restart is therefore left
alone. What is missing is something that starts it.

### `drain`

Draining is a function, not a process. `agency/jobs/queue.py` exposes one
`drain` called from three places:

1. at the end of every `submit_job_request`,
2. by every worker as it exits,
3. at the start of every dispatch cycle.

No process needs to stay alive. A backlog drains through worker exits with the
dashboard closed and no cycle in between; the dispatch cycle is the periodic
sweeper that repairs anything dropped, the same role it plays for markers.

Under a global lock in `<memory_store>/.jobs`, `drain`:

1. runs `reconcile_jobs` for every group, so the records it is about to count
   have just been checked against process reality,
2. counts records in `running` and `waiting_for_memory` across all groups,
3. while the count is below `pool`, launches the head of the queue.

Reconciling first is what makes record-based counting safe. A worker killed by a
power cut leaves a `running` record; without the reconcile it would consume a
slot forever and the pool would shrink monotonically toward zero. `reconcile_jobs`
consequently stops having the FastAPI lifespan as its only caller, so a headless
installation reconciles too.

### Ordering

A single global FIFO on `due_at`, ascending, ties broken by job ID so the order
is total and reproducible:

- scheduled job — the occurrence being served, which may be in the past,
- manual launch or decision execution — the moment of submission.

Work runs in the order it was meant to run. The consequence is accepted: after a
long absence a manual launch sorts behind the recovered backlog, because its due
time is later. Determinism matters more, since jobs can depend on one another.

### Enqueue guard

A job may only enter the queue if something will drain it: a live worker, or
failing that an installed and enabled platform dispatcher. Otherwise submission
raises `JobSubmissionError`.

In practice the guard almost never fires, because a job is only queued when the
pool is full, and a full pool means live workers exist, every one of which
drains on exit. The check exists for the case where those workers die
abnormally between the count and the enqueue.

### Expiry

Queued jobs never expire. The enqueue guard is what prevents a job entering a
queue nobody will drain; once a job is in the queue it is committed, whatever
happens to the machine afterwards.

## Session ledger

The job record is the record of a session start: `job_id` identifies it,
`started_at` times it, and the backend's native `session_id` is backfilled at
completion from the integration's output, as it is today. There is no second
ledger to drift out of agreement with the first.

With `daily_limit` gone, its `.out` glob in the runner goes with it, along with
the `dispatch_daily_limit` fields in `agency/configuration/patches.py` and the
Daily Limit input in Group Settings.

## Interface

### Inbox

A Work queue strip sits directly beneath Pipeline and above the Attention Queue
row.

![Inbox mockup. Fleet zone with three agent cards, Paul idle, Gurney running, Duncan in rose reading overdue 12h. Pipeline zone showing 7 observations, 2 proposals, 1 decision. Below it a highlighted Work queue strip whose header reads WORK QUEUE with pills "2 running" and "3 queued", listing "1 duncan / suite-health 08:00", "2 thufir / authority-audit 09:00", "3 jessica / docs-audit 11:42". Below that the Attention Queue beside the Activity feed.](assets/2026-07-30-scheduled-work-recovery/inbox-queue-strip.png)

The strip lists the whole queue in due order, each row carrying its position,
the agent and routine, and the due time. Its height is capped at roughly four
rows and the remainder scrolls, so a deep backlog cannot push the Attention
Queue — the zone that is acted on — off the screen. The header counts stay
accurate for rows scrolled out of view. When nothing is running and nothing is
queued the strip keeps one muted line, `idle · pool 4`, so the zone never moves
and the page does not reflow when work starts.

![Three renderings of the strip. First, three waiting with no scrollbar, header pills "2 running" and "3 queued". Second, nine waiting with header pills "4 running" and "9 queued", four rows visible and a scrollbar on the right. Third, the idle state: a muted strip whose header reads WORK QUEUE on the left and "idle · pool 4" on the right.](assets/2026-07-30-scheduled-work-recovery/queue-strip-states.png)

### Jobs

Queued rows show their FIFO position and their due time. A queued job offers
Cancel; `queued → cancelled` is already a legal transition.

### Agent detail and fleet cards

`queued` renders as its own state, distinct from `running`, rather than being
collapsed into general activity.

## Failure behavior

- **No drainer available.** A scheduled submission logs the refusal and leaves
  the marker unwritten, so the occurrence stays eligible and `catch_up` retries
  it on a later cycle. A manual submission surfaces the error.
- **A queued job fails to launch.** Its record becomes `failed` with the launch
  error recorded, and the drain continues to the next job rather than stopping.
- **A worker dies without draining.** Its record is cleared by the reconcile at
  the head of the next drain, so it never permanently consumes a slot, and the
  dispatch cycle guarantees a drain happens within `interval`.
- **A routine that keeps failing to submit** is retried on every cycle for as
  long as its occurrence stays inside `catch_up`, and writes one error line per
  attempt.

## Testing

**Recovery.** `catch_up` parsing and rejection of malformed values; the default
of `today` when the key is absent; the candidate occurrence for `at` before and
after the daily time; the marker landing on the recovered occurrence's day
rather than today; expiry at `none`, `today`, `always` and a duration; the
`every` anchor advancing to the occurrence rather than to the launch moment;
exactly one run per routine per cycle.

**Queue.** Capacity counting with live records, with confirmed-dead records, and
with records whose liveness cannot be determined; due-time FIFO including the
tie-break; the enqueue guard admitting when a worker is live and refusing when
nothing can drain; a drain triggered from a worker exit with no dashboard and no
dispatch cycle running; a queued job surviving a simulated restart.

**Configuration.** Rejection of a configuration still carrying `daily_limit`;
`pool` validation at and below `1`; round-tripping `catch_up` through a config
patch.

**Interface.** The strip in its three states including idle; queue position on
the Jobs list; cancelling a queued job; the queued state on an agent card.

## Rejected alternatives

**Recovery**

- *Downtime-gated recovery*, arming catch-up only when a heartbeat gap proves
  dispatch was absent. Rejected: it adds state and a question ("was dispatch
  down, and for how long?") to distinguish cases that all want the same outcome.
- *A single last-fired marker for both schedule kinds*, replacing the per-day
  `.event-*` files. Cleaner in isolation, rejected for the migration and the
  rewrite of `_at_lateness` it would cost, to buy uniformity visible only from
  inside the code.
- *Never looking past today.* Rejected: it breaks the case that motivated the
  work, a machine opened at 06:00 after days away.
- *A default derived from the cadence*, giving `always` to `at` rules and to
  `every` periods of `8h` or slower and `today` to faster ones. Rejected in
  favor of a flat `today` default: the cutoff was arbitrary, the rule had to be
  explained before a routine's behavior could be predicted, and a routine that
  genuinely needs recovery across days can say so.
- *No configuration at all*, with the recovery bound hard-coded. Rejected
  because the rule then cannot be written down, overridden, or displayed.

**Queue**

- *A dedicated queue daemon.* Rejected: a new supervised process with its own
  install, status and failure story on two platforms.
- *Draining from the dashboard.* Rejected: it makes headless operation
  second-class.
- *Draining only in the dispatcher, kicked out of band* by `Start-ScheduledTask`
  or `systemctl start`. Rejected: `MultipleInstances: IgnoreNew` can swallow a
  kick, every instant job would pay a full cycle's cold start, and an inline
  fallback is needed anyway for installations with no timer — which is the
  chosen design, with a kick added on top.
- *Per-group pools*, or a global pool with a per-group cap. Rejected: the
  constraint is machine-wide, and a per-group number cannot express it.
- *Interactive-first queue tiering.* Rejected in favor of deterministic due-time
  order, since jobs can depend on one another.
- *Queued jobs inheriting the `catch_up` bound and expiring in the queue.*
  Rejected: the enqueue guard already prevents the situation that would make a
  queued job stale.
- *Liveness-based capacity counting with no operational check.* Rejected in
  favor of records plus the guard plus a reconcile at the head of every drain.
- *A `queue_size` knob.* Rejected: it guards a depth nothing reaches, and its
  only unique effect is refusing work a person just asked for.

**Ledger and limits**

- *A separate append-only session JSONL.* Rejected: a second source of truth
  about one event.
- *Capturing native session IDs at start.* Rejected: backend-specific and
  undocumented for the Copilot CLI.
- *Keeping `daily_limit` per group*, or moving it to `agency.jobs.daily_limit`
  as a machine-wide spend ceiling. Both rejected: resource spend is acceptable
  as long as the work gets done, and the pool is not expected to stay saturated.

**Inbox placement**

- *A strip under Fleet*, *a card above Activity*, and *folding pending work into
  the Activity feed.* Rejected in favor of a strip beneath Pipeline.
- *Counts and the head of the queue only*, and *drawn pool-occupancy bars*.
  Rejected in favor of the full list, capped and scrolling.
- *Hiding the strip when idle.* Rejected so the zone never moves.
