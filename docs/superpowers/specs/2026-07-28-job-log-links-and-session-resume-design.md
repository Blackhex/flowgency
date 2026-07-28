# Job log links and Copilot session resume

Date: 2026-07-28
Status: Approved

## Problem

The job detail page at `GET /{group}/jobs/{job_id}` renders the stdout and
stderr log paths as inert text. Reading a job's output means copying a Windows
path out of the browser and opening it by hand, even though the dashboard
already has a log viewer that the agent activity tab links to.

Separately, a finished Copilot job cannot be picked up again. The Copilot CLI
prints a session id that `copilot --resume=<id>` accepts, and Agency already
parses that id, but it is discarded into a formatted summary line instead of
being kept as data.

## Goals

- Turn the job detail log paths into links to the existing log viewer.
- Persist the integration session id on the job record.
- Offer a job detail control that reopens the session in the integration's CLI,
  with the equivalent command always available to copy.
- Backfill the session id onto job records that predate the change.

## Non-goals

- Streaming or tailing a running job's output in the browser.
- Resume support for integrations other than Copilot. The seam is generic so
  `claude_code` and `codex` can opt in later, but neither is implemented here.
- Any new log-serving route. The existing viewer is sufficient.

## Design

### Log links

`_job_detail_context` in `agency/web/routes/jobs.py` gains `stdout_href` and
`stderr_href`. A module-level helper builds
`/{group}/logs/view?path={path}` with both segments percent-encoded, and
returns an empty string when the record's path is unset. `job_detail.html`
replaces its two plain-text lines with anchors whose text is the log file's
basename and whose `title` carries the full path.

This introduces no new route and no new path validation. `GET
/{group}/logs/view` in `agency/app.py` already calls
`validate_file_access(fpath, logs_dir)` against the group's resolved logs
directory, and `agency/jobs/execution.py` writes job logs to
`<group.path>/logs/<date>/<agent>-<trigger>-<job_id>.{out,err}`, which is
inside that root. The link shape matches `_recent_log_rows` in
`agency/web/routes/agent_detail.py`, so both surfaces reach the viewer the
same way.

### Session id as a first-class field

`RunResult` in `agency/integrations/__init__.py` gains
`session_id: str | None = None`.

`CopilotIntegration` gains a `_parse_session_id(raw)` static method that scans
the JSONL stdout for the last `type == "result"` event and returns its
`sessionId`. This is the same event `_usage_summary` already locates; the new
method exists so the id can be obtained without the session-state directory
lookup that `_usage_summary` performs. Malformed lines are skipped and a
missing event yields `None`, matching the existing contract that a run must
never fail because of output parsing.

`CopilotIntegration.run` sets `session_id` on both the normal return and the
`subprocess.TimeoutExpired` branch, so a job that exhausts its timeout is
still resumable from whatever the CLI had emitted.

`BaseIntegration` gains:

```python
def resume_command(self, session_id: str) -> tuple[str, ...] | None:
    return None
```

`CopilotIntegration` overrides it to return
`(self.cli_command, "--resume", session_id)`. The command is built from
`cli_command` rather than `require_executable()` so that rendering the
copyable text can never raise when the CLI is not installed. The POST handler
resolves the real executable separately.

`JobRecord` in `agency/jobs/models.py` gains
`session_id: str | None = None`. Because `from_dict` expands the payload into
keyword arguments, records written before this change load with the default
and records written after round-trip through `to_dict`. No migration is needed
to read old records.

`agency/jobs/execution.py` threads `result.session_id` into the terminal
`transition_job` call alongside `stdout_path` and the other run outputs.

### Resume control

The job detail button row gains a **Resume in {integration display name}**
submit button, rendered only when the record carries a `session_id` and the
job's integration returns a non-`None` `resume_command`. Directly below it, and
independent of whether the button renders, a readonly field holds the resume
command text with a Copy button. The copy behaviour reuses the
`navigator.clipboard.writeText` call with a focus-and-select fallback already
implemented in `setup.html`, so a remote browser session still gets the
command even though it cannot spawn anything locally.

The button posts to `POST /{group}/jobs/{job_id}/resume`. The handler:

1. Validates the group and job store, and loads the record.
2. Resolves the integration from `record.spec.integration_name`.
3. Rejects the request when the record has no `session_id`, when the session id
   fails validation, or when the integration has no resume command.
4. Resolves the executable and calls `spawn_interactive_terminal(argv,
   cwd=Path(record.spec.workspace_root))` in a threadpool.
5. Redirects with 303 to `/{group}/jobs/{job_id}?resume=launched` on success or
   `?resume=failed` on `IntegrationError` or `OSError`.

The detail handler reads the `resume` query parameter and renders a short
notice. This mirrors the existing `POST /setup/launch` flow, which is the
codebase's established pattern for starting an interactive CLI from the
dashboard.

#### Security

This endpoint starts a local process in response to an HTTP request, so it is
constrained deliberately.

- It is POST-only, which blocks passive triggers such as prefetch, `<img>`,
  and crawlers; it does not defend against a cross-origin form submission,
  which is consistent with every other state-changing endpoint in the
  dashboard.
- `session_id` is validated against `^[A-Za-z0-9_-]{1,128}$` before it reaches
  an argument vector, and a non-conforming value is rejected with 400. This is
  the only attacker-influenceable element of the command, and it matters
  concretely: on POSIX, `spawn_interactive_terminal` falls through to
  `shlex.join(parts)` inside `xterm -e`, so an id sourced from a tampered job
  record would otherwise be a shell-injection vector.
- The working directory comes from the record's `workspace_root`, never from
  the request.
- The executable comes from integration resolution, never from the request.

### Backfill

`tools/backfill_job_session_ids.py`, invoked as
`python -m tools.backfill_job_session_ids --config config.yaml [--group NAME]
[--dry-run]`. For each job record whose `session_id` is empty and whose
`stderr_path` names a readable file, it extracts the id from the
`--resume=<id>` text that `_usage_summary` appended to stderr and writes it
back.

The write cannot use `transition_job`, which requires a status change and
rejects terminal-to-terminal transitions. The script instead takes
`exclusive_lock(job_lock_path(path))`, re-reads the record inside the lock,
applies `dataclasses.replace`, and calls `write_job`. Re-reading inside the
lock is what makes a concurrent worker write safe.

The script only ever fills an empty `session_id` and never overwrites a
populated one, so it is idempotent and cannot clobber a record produced by the
new run path. It reports per-record outcomes and, under `--dry-run`, performs
no writes.

It is not registered in `agency/cli.py`. It is a one-time utility for existing
local data, not a shipped command.

## Testing

Route and template:

- Job detail renders anchors to `/{group}/logs/view?path=...` with encoded
  paths for both stdout and stderr.
- No anchor is rendered for a record whose path is `None`.
- The resume button is absent when `session_id` is unset, absent when the
  integration's `resume_command` returns `None`, and present otherwise.
- The copyable resume command is rendered whenever a `session_id` exists.
- `POST .../resume` invokes the spawn seam with the expected argv and working
  directory and returns 303.
- `POST .../resume` returns 400 for a session id containing shell or option
  metacharacters, and 404 for an unknown job.

Integration:

- `_parse_session_id` extracts the id from a JSONL fixture with a `result`
  event, returns `None` when no such event exists, and ignores malformed lines.
- `run` populates `RunResult.session_id` on the timeout path.
- `BaseIntegration.resume_command` returns `None`; the Copilot override returns
  the expected tuple.

Persistence:

- `JobRecord` round-trips `session_id` through `to_dict`/`from_dict`.
- A record payload without `session_id` still loads.

Backfill:

- Fills an empty `session_id` from a stderr fixture.
- Leaves a populated `session_id` untouched.
- Writes nothing under `--dry-run`.

## Files touched

- `agency/web/routes/jobs.py` — log hrefs, resume context, resume POST route.
- `agency/templates/job_detail.html` — log anchors, resume button, copy field.
- `agency/integrations/__init__.py` — `RunResult.session_id`,
  `BaseIntegration.resume_command`.
- `agency/integrations/agency/copilot.py` — `_parse_session_id`,
  `resume_command`, `run` wiring.
- `agency/jobs/models.py` — `JobRecord.session_id`.
- `agency/jobs/execution.py` — persist `session_id` on the terminal transition.
- `tools/backfill_job_session_ids.py` — new.
- `tests/` — coverage described above.
