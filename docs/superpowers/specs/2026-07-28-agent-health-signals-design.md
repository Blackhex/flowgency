# Agent health signals

## Problem

The dashboard fleet bar colors every agent from a single input: the modification
time of the newest log file that starts with the agent name. `agent_health_status`
returns `green` under 24 hours, `amber` under 48, and `red` otherwise — including
when no log exists at all.

An agent that has never run is therefore indistinguishable from an agent that ran
and then went silent. Both render rose, and both land in the footer's
`needs attention` count, which is derived as `total - healthy`. In the `atreides`
group this paints Paul Atreides and Gurney Halleck as failures when neither has
ever been asked to do anything.

Elapsed time is also the wrong measure. An agent with no routines is not late
when it has not run for a week, because nothing was expected of it. An agent with
a daily routine is late one hour after its routine fails to fire, not forty-eight.

## Goals

- Distinguish "no signal yet" from "something is wrong".
- Define red against the agent's own schedule and its last run outcome, not
  against a fixed elapsed-time threshold.
- Keep the summary counters partitioned so each agent is counted exactly once.

## Non-goals

- Checking whether the OS-native dispatch timer is installed. A missing timer
  would turn every scheduled agent red, and `get_timer_status` shells out to
  `schtasks`/`systemctl`/`launchctl`, which is too expensive for a page render.
- Reworking `agency/dispatch/run.py` scheduling. The runner keeps its current
  behavior; this change only reads the trail it leaves behind.
- Changing the running indicator. A queued or running job keeps its existing
  pulsing emerald dot and its job badge.

## Health model

Four states, evaluated in this precedence order.

| State | Meaning | Condition |
| --- | --- | --- |
| `red` | broken promise | the newest terminal job record for the agent has status `failed`, **or** any enabled routine is overdue past the grace window |
| `amber` | running late | an expected occurrence has passed but is still inside the grace window |
| `gray` | no signal | no run on record, and nothing overdue and nothing failed |
| `green` | fine | has run, nothing overdue, last run did not fail |

`running` is orthogonal. It continues to override the dot glyph only; it does not
change the computed health or the background tint.

"No run on record" means the agent has neither a log file named `<agent>-*` under
the group logs tree nor a job record whose status is `complete` or `failed`. A
`cancelled` job is not a run; it was never executed.

"Newest terminal job" means the record with the greatest sort key among records
whose status is `complete`, `failed`, or `cancelled`, where the sort key is
`completed_at` falling back to `started_at` and then to `spec.created_at`, since
`cancelled` records may carry no `completed_at`. Ties break on `job_id` for a
stable result. A `cancelled` job is not a failure. A later successful job clears a
red caused by an earlier failure.

### Applied to the `atreides` group

`paul` has no routines and no runs, so it is gray. `gurney` has `at: '18:00'` and
no runs; before 18:00 it is gray, and if 18:00 passes without a dispatch it turns
red. `thufir`, `duncan`, and `jessica` have recent runs and current markers, so
they stay green.

## Overdue computation

Evaluated per routine on the agent instance, and only when all of the following
hold: the group's `dispatch.enabled` is true, `routine.enabled` is true, and the
routine has no `condition` (the runner skips conditional routines, so the
dashboard must not hold them against the agent).

The dispatch runner records what it fired:

- `at` rules touch `<logs>/<YYYY-MM-DD>/.event-<agent>-<routine>-<YYYY-MM-DD>`
- `every` rules touch `<logs>/.last-<agent>-<routine>`

Both names pass agent and routine identifiers through `_marker_safe`, which
replaces every run of characters outside `[A-Za-z0-9._-]` with a hyphen and strips
leading and trailing dots and hyphens, falling back to `item`.

The grace window is `agency.dispatch.interval + 2` minutes, defaulting to 17. This
mirrors the runner's own firing window in `check_at_rule`, so red means the runner
genuinely failed to deliver rather than that it has not woken up yet.

**`at: "HH:MM"`.** The expected occurrence is today's `HH:MM`. If today's
`.event-` marker exists, the routine fired and produces no signal. Otherwise the
routine is `overdue` when `now > occurrence + grace`, `due` when
`occurrence <= now <= occurrence + grace`, and produces no signal when
`now < occurrence`. Occurrences before today are not considered; the runner only
ever writes a marker for the current day, so an older date carries no evidence
either way. A malformed `HH:MM` produces no signal, matching the runner, which
logs a warning and declines to fire.

**`every: "N(m|h|d)"`.** When the `.last-` marker is absent the routine produces
no signal. There is no reference point, so a newly configured routine must not go
amber or red before its first dispatch. When the marker exists, the due time is
`marker.mtime + N`, and the routine is `overdue` when `now > due + grace` and
`due` when `due <= now <= due + grace`. A malformed interval produces no signal.

Markers record submission, not completion. That is the correct signal for this
state: red means the schedule was not honored, while a run that was honored and
then failed is already covered by the job-record rule.

An agent's schedule state is the strongest state across its routines: `overdue` if
any routine is overdue, else `due` if any routine is due, else no signal.

## Structure

**`agency/dispatch/schedule.py`** (new). Owns everything the runner and the
dashboard must agree on about schedules: `marker_safe`, the `.event-` path, the
`.last-` path, and `parse_every`, which turns an `every` interval into a
`timedelta` or `None`. `agency/dispatch/run.py` imports from it in place of its
private `_marker_safe` and its inline interval parse. Marker filenames and
interval semantics are the two places where a silent drift between the runner and
the dashboard would make every scheduled agent look red, so both sides must
derive them from the same code.

**`agency/health.py`** (new). Owns the health model. Public surface:

- `schedule_state(routines, *, logs_root, agent_name, now, grace) -> str | None`
  returning `"overdue"`, `"due"`, or `None`.
- `evaluate_agent_health(*, has_run, last_job_failed, schedule) -> str`
  returning `"green"`, `"amber"`, `"gray"`, or `"red"`.

Inputs are plain values, so the module is testable without FastAPI, a TestClient,
or a config snapshot.

**`agency/jobs/store.py`.** Add `latest_terminal_job(job_paths, agent_name)`
alongside `active_jobs`, returning the newest record whose status is terminal, or
`None`. It reuses the existing tolerant read loop, skipping unreadable records.

**`agency/app.py`.** `agent_health_status` is replaced by calls into
`agency/health.py` from both fleet builders, `collect_agents_with_identity` and
`build_dashboard_fleet`. Both already resolve the group's logs root and job paths;
they additionally need the agent's routines and the agency-level
`dispatch.interval`. The interval is added to the group runtime dictionary by
`runtime_group`, alongside `logs` and `job_paths`, so that neither builder has to
reach back into agency settings.

`compute_next_run_detail` currently reads `dispatch_cfg["routines"]`. Schema 4
places routines on agent instances and `runtime_group` dumps only
`GroupDispatch`, which holds `enabled` and `daily_limit`, so that lookup never
matches and `next_run` is dead for every agent. It is re-pointed at
`instance.routines` and at the shared marker helpers, which restores the feature
and keeps one definition of a routine's next occurrence.

## Counters and rendering

The dashboard route replaces its single derived count with three that partition
the fleet: `fleet_healthy` counts green, `fleet_never_run` counts gray, and
`fleet_attention` counts amber plus red. `fleet_running` is unchanged and remains
orthogonal.

The footer reads `5 agents · 3 healthy · 2 never run`, appending `· N running` and
`· N needs attention` only when non-zero. Gray agents are reported as never run
and are not counted as needing attention.

In `home.html` the dot gains an explicit gray branch instead of falling through to
rose, the background tint gains a neutral gray case, and every dot gains a `title`
so that color is not the only carrier of meaning.

## Testing

Unit tests against `agency/health.py`, driving the clock through
`AGENCY_FIXED_NOW`:

- never run, no routines, no failure — gray
- never run, `at` routine whose time has not arrived — gray
- `at` past its time inside the grace window, no marker — amber
- `at` past the grace window, no marker — red
- `at` past the grace window with today's marker present — green
- `every` with no marker — no schedule signal
- `every` past its interval plus grace — red
- newest terminal job failed — red regardless of schedule
- a newer successful job after a failed one — not red
- `dispatch.enabled: false` suppresses overdue
- `routine.enabled: false` suppresses overdue
- a routine with a `condition` suppresses overdue
- malformed `at` and malformed `every` produce no signal

Marker-name tests assert that `agency/dispatch/schedule.py` produces exactly the
names `run.py` wrote before the extraction, and that `parse_every` accepts each
unit and rejects malformed intervals.

A store test covers `latest_terminal_job` ordering, its exclusion of active
records, and its tolerance of unreadable files.

Dashboard tests cover the three counters and the gray rendering. The existing
assertions in `tests/test_dashboard.py` that expect `red` for agents with no logs
move to `gray`.
