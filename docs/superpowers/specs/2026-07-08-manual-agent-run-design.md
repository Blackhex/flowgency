# Manual Agent Run from Agent Cards — Design

**Date:** 2026-07-08
**Status:** Approved (design), pending implementation plan
**Topic:** Trigger an agent run manually by picking a prompt on the agents list

## Problem

Agents only run on a schedule. The dispatcher (`agency/dispatch/run.py`) fires
`at`/`every` rules via an OS-native timer, and the only web-initiated run today
is decision execution (`execute_decision` in `agency/app.py`). There is no way
for a user to say "run this prompt as this agent, right now" from the UI.

Users browsing `/{group}/agents` want to kick off a run on demand — for testing
a prompt, re-running a routine early, or reacting to something — without waiting
for the schedule or editing config.

## Goal

On `/{group}/agents`, each agent card lists **all** of the group's prompts.
Each prompt row lets the user:

- **Open the prompt** — the prompt name links to its detail page
  (`/{group}/prompts/{slug}`).
- **Run the prompt** — a Run button dispatches that prompt as that agent
  immediately, in the background, **in the same runtime environment (user
  profile / PATH) as the scheduled dispatcher**.

The run is fire-and-forget from the UI: it starts silently in-place, the card's
status flips to a pulsing "Running" indicator, and Run buttons for that card
disable until the page is next loaded.

## Environment Parity (core requirement)

A manual run MUST execute in the same environment as the scheduled runner.

Both the scheduled dispatcher (`_run_agent` → `integration.run(...)`) and the
existing web-triggered path (`execute_decision` → `integration.run(...)`) call
the **same** `integration.run(agent_dir, prompt_file, timeout, sandbox_root=...)`
method. That method spawns the tool's CLI via `subprocess.run(..., cwd=agent_dir)`
and **inherits the parent process environment** — no `env=` override is passed.

Parity is therefore achieved by:

1. **Reusing the identical execution path.** Refactor the dispatcher's per-run
   core into a shared, importable helper used by *both* the timer and the web
   app, so command construction, `cwd`, timeout resolution, the `.running-<agent>`
   marker, and `.out`/`.err` log conventions are identical.
2. **Never overriding `env`.** The subprocess continues to inherit the calling
   process's environment. Because both `agency.service` (the web app) and the
   dispatch timer run as **user-level** services under the same account, the
   inherited profile and PATH match.

`_resolve_cmd` (CLI lookup with `~/.local/bin` fallbacks) is already shared on
`BaseIntegration`, so tool resolution matches too.

## Approach

**Refactor the dispatcher core into a shared helper + add a background-task
route.** This mirrors the existing `execute_decision` pattern (FastAPI
`BackgroundTasks`) and the marker/log plumbing already surfaced by
`is_agent_running`.

### 1. Shared execution helper

Extract the body of `_run_agent` in `agency/dispatch/run.py` into a reusable
function (working name `run_agent_prompt`) that takes the resolved group dict,
agent name, prompt filename, timeout, log dir, agent config, agent dir, and
sandbox root. It:

- resolves the integration, applies `with_config` for `script`,
- writes the `.running-<agent>` marker before the run and removes it after
  (even on error),
- calls `integration.run(...)` with no `env=` override,
- writes `.out`/`.err` logs using the existing naming convention.

`agency/dispatch/run.py` calls this helper from its cycle; the web app imports
and calls the same helper from its background task.

### 2. New route: `POST /{group}/agents/{agent}/run`

- Accepts a `prompt` field (prompt filename).
- Validates the agent exists and the prompt exists in `shared/prompts/`, with
  path-traversal protection (same pattern as other file routes).
- If `is_agent_running(g, agent)` is already true → return **409** (busy); do
  not launch a second concurrent run for the same agent.
- Resolves the integration via `get_agent_integration` (filesystem-first, like
  the rest of the web UI), resolves `sandbox_root` and the per-agent timeout
  exactly as `execute_decision` does.
- Schedules the shared helper via `BackgroundTasks` and returns **202** JSON
  `{"status": "started"}`.

### 3. Agents list data

The `agents_list` route (or `collect_agents_with_identity`) additionally passes
`prompts = collect_prompts(g)` — **all** group prompts — to the template.

### 4. Agent card UI (`agents.html`)

Each card gains a compact, scroll-capped prompt list. Per row:

- Prompt name as a link to `/{group}/prompts/{slug}`.
- A **Run** button that POSTs via `fetch()` to the run route.

On a 202 response, JS swaps the card's status dot to the pulsing "Running"
state and disables that card's Run buttons. On 409 or error, it shows brief
inline error text. No page navigation occurs.

## Data Flow

```
User clicks Run on a prompt row
  → fetch POST /{group}/agents/{agent}/run  { prompt }
    → validate agent + prompt (path-safe)
    → 409 if is_agent_running(agent)
    → resolve integration / sandbox_root / timeout
    → BackgroundTasks.add_task(run_agent_prompt, ...)
    → 202 {status: started}
  → JS: dot → pulsing "Running", disable card Run buttons
Background: run_agent_prompt
  → touch .running-<agent>
  → integration.run(agent_dir, prompt_file, timeout, sandbox_root)   [inherits env]
  → write <agent>-<stem>-<ts>.out / .err
  → remove .running-<agent>
```

## Error Handling

- **Unknown agent or prompt** → 404.
- **Agent already running** → 409, no launch.
- **Integration lacks execution support** → 4xx with a clear message (mirrors
  the `supports_execution` check in `execute_decision`).
- **Run failure** (non-zero exit / timeout) → captured in `.err` and the
  `.out`/`.err` logs, same as scheduled runs; the marker is always cleared in a
  `finally`. The UI's transient "Running" state self-heals on next page load via
  the existing stale-marker timeout in `is_agent_running`.

## Testing

- **Shared helper:** writes `.out`/`.err` + `.running-<agent>` marker, clears the
  marker on success and on exception, respects the timeout, and calls
  `subprocess`/`integration.run` **without** an `env=` override (environment
  inheritance).
- **Route:** 202 on a valid run (background task scheduled with correct args),
  404 for unknown prompt/agent, 409 when the agent is already running.
- **Regression:** existing dispatch tests (`tests/test_dispatch_run.py`) still
  pass against the refactored helper.

## Scope / YAGNI

- No new config, no schedule changes, no persistence beyond existing log files.
- No queueing — one concurrent run per agent; additional attempts get 409.
- No live streaming of output; users view results via existing logs/timeline.
- Applies to regular agents on the cards; subagent cards are out of scope for
  this change.
