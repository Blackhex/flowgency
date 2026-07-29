# Inbox agent health cards

## Problem

The Inbox fleet bar renders each agent as a chip carrying a colored dot, a name,
a blueprint, and an integration. The dot's only explanation is a `title`
attribute reading `Healthy`, `Run is due`, `No run on record`, or
`Needs attention`. None of those say what is wrong or when it went wrong.

`collect_agents_with_identity` already computes `last_run`, `last_seen`,
`next_run`, and `next_run_detail` for every agent, and `relative_time` and
`relative_future` are still registered as Jinja filters. No template renders any
of them. The agent card markup that displayed them was dropped in the Mission
Control redesign; the data behind it was never removed.

`_agent_health` distinguishes a failed last job from an overdue routine and then
collapses both into the string `red`, so the reason is computed and discarded on
every page render.

The result is that the fleet footer can read `1 needs attention` while the
Attention Queue directly beneath it reads `No items need attention right now`,
because that panel only ever holds proposals and observations. In the `atreides`
group today, Duncan Idaho is rose because routine `suite-health` was due at 08:00
and the dispatcher has not run since 2026-07-28 18:12 — a fact the page states
nowhere.

## Goals

- Restore last run and next run for every agent, healthy ones included.
- Name the fault: which routine, when it was due, how late it is, or which job
  failed.
- Make the fleet counters and the Attention Queue agree.
- Keep the reason reachable in one click from where it is stated.

## Non-goals

- Changing the health model. The four states, their precedence, the grace
  window, and the marker files stay exactly as specified in
  `2026-07-28-agent-health-signals-design.md`. This change surfaces the existing
  computation; it does not redefine it.
- Touching the Agents page. That page exists for ad-hoc invocation and keeps its
  launch cards and job badges unchanged.
- Detecting whether the OS-native dispatch timer is installed. An agent is
  overdue because its marker is missing, not because a timer was probed.
- Adding any new filesystem read. Every value rendered here is already gathered
  by `collect_agents_with_identity` and `_agent_health`.

## Approved visual design

The drawing below was approved during brainstorming and is normative for layout,
ordering, and copy. `assets/2026-07-29-inbox-agent-health-cards/inbox-fleet-cards.html`
is the source of these images and is authoritative where the prose and the
picture disagree about spacing or arrangement. The prose is authoritative for
behavior and for text content.

### Fleet zone

![Fleet zone rendered as a three-up card grid. Paul Atreides is dimmed with "never run". Gurney Halleck, Thufir Hawat and Lady Jessica are green with a last-run and next-run pair. Duncan Idaho is rose, reads "1d ago / overdue 3h", and carries a fault line reading "suite-health due 08:00". The footer reads "5 agents, 3 healthy, 1 never run, 1 needs attention".](assets/2026-07-29-inbox-agent-health-cards/fleet-cards.png)

### Attention Queue

![Attention Queue holding one health item. It reads "overdue", a Duncan Idaho pill, then "Routine suite-health was due at 08:00 and has not run - 3h 46m late", then "last run 2026-07-28 08:12, succeeded in 3m 55s", then three links: Open routine, Last job log, Run now.](assets/2026-07-29-inbox-agent-health-cards/attention-queue.png)

Rejected during brainstorming, recorded so they are not revisited by accident:
wide cards carrying the full sentence inline, a dense aligned table, and a
hover-only tooltip. The tooltip was rejected because it is invisible on touch,
awkward for assistive technology, and cannot be scanned.

## Health reason model

`agency/health.py` gains a lateness value that carries the offending routine
instead of only its severity.

```python
class Lateness(NamedTuple):
    routine_id: str
    state: str        # OVERDUE or DUE
    due_at: datetime
```

- `schedule_lateness(schedules, *, logs_root, agent_name, now, grace) -> Lateness | None`
  returns the strongest lateness across an agent's routines. `OVERDUE` outranks
  `DUE`. Within one state the earliest `due_at` wins, and ties break on the
  routine's index in the configured list, so the result is stable across renders.
- `schedule_state` keeps its signature and its return values and is reimplemented
  as `schedule_lateness(...).state`, so no existing caller or test changes.

`agency/app.py` replaces `_agent_health` with `_agent_status`, returning:

```python
class AgentHealth(NamedTuple):
    color: str                  # green | amber | gray | red
    kind: str                   # healthy | never_run | due | overdue | job_failed
    routine_id: str | None
    due_at: datetime | None
    late: timedelta | None
    job: JobRecord | None
```

`color` is produced by the unchanged `evaluate_agent_health`. `kind` follows the
same precedence the color does, so the two can never disagree:

| `kind` | Condition | `color` |
| --- | --- | --- |
| `job_failed` | newest executed job has status `failed` | `red` |
| `overdue` | no failed job, and `Lateness.state` is `OVERDUE` | `red` |
| `due` | no failed job, and `Lateness.state` is `DUE` | `amber` |
| `never_run` | nothing failed, nothing late, no run on record | `gray` |
| `healthy` | none of the above | `green` |

`late` is `now - due_at` for `overdue` and `due`, and `None` otherwise.
`routine_id` and `due_at` are populated only for `overdue` and `due`. `job` is
the newest executed record whatever the `kind`, because the queue's last-run
line reports it for an overdue agent too, as the drawing shows.

`running` stays orthogonal. It overrides the dot glyph and the next-run cell; it
does not change `color` or `kind`, and a running agent never produces a queue
item.

## Fleet card

`home.html` Zone 1 becomes a grid: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`,
`gap-2`, `items-start`. Cards keep the current per-health background and border
tints. The whole card is the existing link to `profile_href`; the two timing
values are nested links and must therefore be rendered as anchors outside the
card anchor, using a wrapper `div` with the card link covering the header only.

Card contents, top to bottom:

| Row | Content |
| --- | --- |
| Header | avatar, display name, health dot pushed right |
| Title | `identity.title`, omitted when empty |
| Badges | blueprint key, integration key, open-observation pill when non-zero || Timing | last run on the left, next run on the right, separated by a top border |
| Fault | one line, present only when `kind` is `overdue`, `due`, or `job_failed` |

**Avatar.** `identity.emoji` when set. Otherwise initials: the first letter of
each of the first two whitespace-separated words of the display name, uppercased;
a single-word name yields its first two letters. The bare `~` placeholder is
retired.

**Health dot.** Unchanged glyph and colors, including the pulsing emerald dot for
a running agent. Its `title` becomes the full queue sentence from the section
below, so the tooltip and the queue never diverge.

**Never-run cards** carry `opacity-75`.

**Open observations** keep their existing amber count and render only when
non-zero. The mockup shows no pill because every count in that group is zero.

### Timing cells

| Case | Left cell | Right cell |
| --- | --- | --- |
| active job | `relative_time(last_run.at)` | `job_status`, linked to `job_href` |
| `never_run` | `never run` | `—` |
| `overdue` | `relative_time(last_run.at)` or `never run` | `overdue {relative}` |
| `due` | `relative_time(last_run.at)` | `due now` |
| otherwise | `relative_time(last_run.at)` | `relative_future(next_run)`, or `—` when the agent has no schedule |

An active job outranks every other case. `job_status` is the existing label
(`Waiting for memory`, `Queued`, `Running`) and the cell keeps the job link the
chip used to carry beside it, so a queued or running job stays one click away
from the Inbox. This is the only element of the old chip that survives as a
link rather than as text.

`{relative}` is `late` in coarse form: whole minutes under an hour, whole hours
under a day, whole days beyond that. The card never shows a compound value.

Left cell links to `/{group}/logs/view?path={last_run.path | urlencode}` when
`last_run` exists, and is plain text otherwise. Right cell links to
`/{group}/agents/{name}/routines` whenever a `next_run_detail` exists.

### Fault line

One line, no links, `text-xs`, tinted to match the card:

| `kind` | Text |
| --- | --- |
| `overdue`, `due` | `{routine_id} due {HH:MM}` when `due_at` falls today, otherwise `{routine_id} due {YYYY-MM-DD HH:MM}` |
| `job_failed` | `last job failed` |

## Attention Queue health items

Health items render above proposals, floated observations, and open
observations, because a stalled agent is why the other three are missing.

One item per agent whose `kind` is `job_failed`, `overdue`, or `due`. `never_run`
produces no item: an agent with nothing scheduled is a configuration state, not a
fault.

Item body:

| `kind` | Label | Sentence |
| --- | --- | --- |
| `job_failed` | `last run failed` | `Job {job_id[:8]} exited {exit_code} after {duration}, {relative_time(completed_at)}.` |
| `overdue` | `overdue` | `Routine {routine_id} was due at {due_at:%H:%M} and has not run — {late} late.` |
| `due` | `due` | `Routine {routine_id} came due {late} ago; the dispatcher has not picked it up yet.` |

Beneath the sentence, when a previous executed job exists, a monospace line:
`last run {completed_at:%Y-%m-%d %H:%M} · {succeeded|failed} in {duration}`.

`{late}` and `{duration}` are compound, unlike the card's coarse `{relative}`:
seconds alone under a minute (`12s`), minutes and seconds under an hour
(`3m 55s`), hours and minutes under a day (`3h 46m`), days and hours beyond that
(`2d 3h`). A zero remainder is omitted, so exactly three hours reads `3h`.

Then three links: `Open routine` to `/{group}/agents/{name}/routines`,
`Last job log` to `/{group}/jobs/{job_id}` when a job record exists, and
`Run now` to `/{group}/agents/{name}` — a link, not a POST, because launching
requires choosing a prompt.

`needs_action_count` gains the health-item count, so the panel header and the
fleet footer are computed from the same set. `fleet_healthy`, `fleet_never_run`,
`fleet_attention`, and `fleet_running` keep their current definitions.

The empty state changes from `No items need attention right now.` to render only
when there are no health items and no pipeline items.

## Agent Detail — Routines tab

The Routines tab today shows only the editable YAML textarea, so a configured
schedule cannot be compared against what actually fired. A read-only status
table is added above the form:

| Column | Value |
| --- | --- |
| Routine | `routine.id`, struck through when `enabled` is false |
| Schedule | `at HH:MM` or `every {spec}`, or `conditional` when the routine has a `condition` |
| Last fired | marker mtime as `YYYY-MM-DD HH:MM`, or `never` |
| Next due | `relative_future(due_at)`, `overdue {relative}`, `due now`, or `—` for conditional and disabled routines |

Values come from the same `agency/health.py` helpers the Inbox uses. The table is
informational; the form below it remains the only way to change a routine.

## Testing

- `tests/test_health.py` — `schedule_lateness` returns the offending routine;
  `OVERDUE` outranks `DUE`; earliest `due_at` wins within a state; config order
  breaks exact ties; `None` when nothing is late. Existing `schedule_state` and
  `evaluate_agent_health` cases stay green unchanged.
- `tests/test_agent_status.py` — `_agent_status` yields each of the five `kind`
  values, and `kind` never contradicts `color`. A `cancelled` job produces
  neither `job_failed` nor a run. A running agent still reports its underlying
  `kind`.
- `tests/test_dashboard.py` — a card renders both timing values; an overdue agent
  renders the fault line and exactly one queue item; a `never_run` agent renders
  no queue item; the fleet footer count equals the number of health items plus
  pipeline items; the empty state disappears when a health item is present.
  `test_dashboard_reports_never_run_agents_separately` asserts
  `'title="No run on record"'`, which moves from the chip to the card dot and
  must keep passing; its `1 needs attention` negative assertion gains a matching
  assertion that the queue empty state is still rendered.
- `tests/ui/dashboard.spec.ts` — the fleet zone exposes one card per agent and
  the timing anchors resolve.

## Settled decisions

- Cards follow configured order. Severity-first sorting was considered and
  rejected: a fleet that reorders itself when an agent goes late is hard to
  build a spatial memory of.
- The overdue fact is deliberately stated twice, tersely on the card and fully in
  the queue. The card is for triage, the queue is for acting.
- The fleet zone grows from roughly 60px to roughly 210px at three columns,
  pushing the pipeline strip and the queue down. Accepted.
