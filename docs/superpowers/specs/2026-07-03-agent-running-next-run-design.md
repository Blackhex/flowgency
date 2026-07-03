# Agent Running & Next-Run Status — Design

**Date:** 2026-07-03
**Status:** Approved (design), pending implementation plan
**Topic:** Show whether an agent is currently running, and when it is next scheduled

## Problem

The agents list, the agent profile, and the home dashboard fleet bar all show an
agent's health dot plus a "last seen" relative time (e.g. `19h ago`). None of
them convey two things a user often wants at a glance:

1. **Is this agent running right now?**
2. **When will it next be dispatched?**

Today there is no signal for "currently running" anywhere in the system — the
dispatcher (`agency/dispatch/run.py`) runs each agent synchronously and only
writes `.out`/`.err` log files after the run completes. And while the agent
profile shows the raw schedule pills (`@ 09:00`, `every 6h`), it never computes
the next occurrence.

## Goal

Across the agents list, the agent profile, and the home dashboard fleet bar,
surface:

- A **running** state when an agent is actively being dispatched.
- The **soonest next scheduled run** when the agent is idle and has a schedule.

Both integrated into the existing health-dot + last-seen presentation as a
single combined widget.

## Approach

**Marker-file signaling + on-read computation.** This fits the filesystem-only
architecture — no database, no background state, no process-table scans.

- The dispatcher writes a short-lived `.running-<agent>` marker file while a run
  is in flight, and removes it when the run finishes (even on error).
- The web app reads that marker to determine "running", and computes "next
  scheduled" on the fly from the existing dispatch rules and the `.last-*`
  markers the scheduler already maintains.

Rejected alternatives:

- **Process-table scan** — fragile and cross-platform painful.
- **Precompute next-run into config** — stale, and duplicates the scheduler's
  own rule logic.

The marker + on-read approach reuses the exact rule semantics the dispatcher
already applies, so the displayed "next run" matches what will actually happen.

## Design

### 1. Running marker (`agency/dispatch/run.py`)

In `_run_agent()`, immediately before calling `integration.run()`, write an
empty marker file at `shared/logs/.running-<agent>` (its mtime records the run
start time). Remove it in a `finally` block so a crash or exception during the
run cannot leave a dangling marker mid-call.

The dispatcher runs agents sequentially, so a single marker per agent name is
sufficient — there is never more than one concurrent run for a given agent.

### 2. Read helpers (`agency/app.py`)

**`is_agent_running(g, agent_name, timeout)` → bool**
Returns `True` when `shared/logs/.running-<agent>` exists *and* its mtime is
within `timeout` seconds of now. The staleness guard means an orphaned marker
left behind by a hard-killed process (older than the run timeout) is treated as
*not running*, so the UI self-heals. `timeout` comes from the group's dispatch
config (`dispatch.timeout`, default 1800).

**`compute_next_run(g, agent_name, dispatch_cfg, interval)` → datetime | None**
Iterates the agent's dispatch rules and returns the soonest upcoming run:

- **Dispatch disabled** or **no runnable rules** → `None`.
- **Skip** rules that have a `condition` (the Python dispatcher never runs them)
  or that are missing a `prompt`.
- **`at HH:MM`** → the next occurrence: today at that time if still in the
  future, otherwise tomorrow at that time.
- **`every Nm` / `every Nh`** → the `.last-<agent>-<stem>` marker mtime plus the
  interval. If the marker does not exist, the rule is due now (return now).
- Return the **minimum** datetime across all the agent's rules.

### 3. Enrich `collect_agents_with_identity()` (`agency/app.py`)

Add two fields to every agent and subagent dict:

- `running`: bool, from `is_agent_running(...)`.
- `next_run`: datetime | None, from `compute_next_run(...)`.

The group's dispatch config is read from `GROUPS[g["key"]]`. Because this helper
feeds **both** the agents list and the home fleet bar, enriching it once covers
two of the three surfaces.

### 4. `relative_future` template filter (`agency/app.py`)

A forward-looking mirror of the existing `relative_time` filter, formatting an
upcoming datetime:

- past / now → `due now`
- < 60 min → `in 5m`
- < 24 h → `in 2h`
- tomorrow → `tomorrow HH:MM`
- further → date string

### 5. The combined status widget

One inline widget, used consistently on the agents cards, replacing the current
separate dot + last-seen span. It holds: the health dot, then a status segment,
then (only when idle and scheduled) a separator and the next-run segment. Same
font size and muted color throughout — segments appear/disappear without the
component changing shape.

Three states:

| State | Widget |
|-------|--------|
| Running | `● Running` |
| Idle + scheduled | `● 19h ago · next in 2h` |
| Idle + no schedule | `● 19h ago` |

When an agent is running, the next-run segment is **not** shown — just
`Running` (no ellipsis). The running dot uses a subtle pulse (emerald) to
distinguish it from the static health dot.

### 6. Template changes

- **`agents.html`** — replace the dot + last-seen span (main cards and the
  subagents grid) with the combined widget above.
- **`home.html`** fleet bar — running agents get a pulsing dot; the footer
  summary line gains `· N running` when any agent is running.
- **`agent_profile.html`** — near the existing schedule pills, show a "Running"
  badge when active, otherwise a "Next run: in 2h (09:00)" line derived from
  `next_run`. The `agent_profile` route adds `running` and `next_run` to its
  context.

## Testing

New tests in `tests/`:

- **`compute_next_run`**: `at` in the future; `at` already passed (rolls to
  tomorrow); `every` with no marker (due now); `every` with a recent marker
  (interval added); a `condition` rule is skipped; dispatch disabled → `None`;
  no schedule → `None`; soonest-of-several is returned.
- **`is_agent_running`**: marker present and fresh → `True`; marker present but
  stale (older than timeout) → `False`; marker absent → `False`.
- **Dispatcher marker lifecycle**: `_run_agent` writes `.running-<agent>` around
  the run and removes it afterward, including when `integration.run()` raises.

## Out of Scope (YAGNI)

- Live auto-refresh / polling of running state.
- Per-rule next-run breakdown (only the soonest is shown).
- Timezone configuration (uses local time, consistent with the dispatcher).
