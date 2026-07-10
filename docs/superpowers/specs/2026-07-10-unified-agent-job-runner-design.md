# Unified Agent Job Runner

**Date:** 2026-07-10
**Status:** Approved (design)

## Problem

Agency has one integration execution contract but multiple orchestration paths:

- Scheduled prompts run inline in the platform-timer dispatch process.
- Manually launched saved prompts run as FastAPI background tasks.
- Approved decisions and decision retries run as FastAPI background tasks.

FastAPI background tasks belong to the dashboard process. Stopping or restarting
the dashboard kills those agents, and startup recovery then marks their decisions
failed. The orchestration paths also duplicate integration, timeout, sandbox,
logging, and status logic.

Decision execution has a separate policy problem: Agency always sends an approved
decision to the proposal's `origin_agent`. The agent that identifies or proposes
work is not necessarily the agent that should implement it.

## Goals

1. Submit every agent run through one trigger-independent job API.
2. Run agents in detached processes that survive the dashboard and dispatch
   processes that submitted them.
3. Keep scheduled, manually launched saved-prompt, decision, and retry behavior on
   the same execution path.
4. Preserve immediate execution without requiring another installed service.
5. Make the launch transport replaceable with a dedicated worker daemon later.
6. Let proposals nominate an implementing agent and validate that choice before a
   decision is created.
7. Permit concurrent jobs, including multiple jobs for the same agent.

## Non-Goals

- Adding free-form prompt, public API, CLI, or event-driven trigger interfaces.
  Future triggers may use the shared submission API without changing execution.
- Adding a persistent daemon or queue consumer now.
- Serializing runs per agent or imposing a global concurrency limit.
- Automatically substituting another agent when the selected executor is invalid.
- Replacing integration-specific execution logic.
- Redesigning the jobs or decision user interface beyond executor selection and
  the status data required by this change.

## Architecture

Add an `agency.jobs` package with three explicit boundaries.

### 1. Submission

`submit_job(spec, launcher) -> JobHandle` is the only API that trigger code uses.
It validates a job, creates its durable record atomically, delegates launch to a
`JobLauncher`, and returns the job ID and initial status.

Trigger code remains responsible only for trigger-specific selection:

- Scheduled dispatch decides when a saved prompt is due.
- The dashboard chooses a saved prompt for a manual run.
- Decision routes resolve the selected executor and assemble decision context.
- Future CLI, API, or event sources build the same `JobSpec`.

No trigger calls `integration.run()` or owns the resulting process lifetime.

### 2. Launching

Define a narrow launcher protocol:

```python
class JobLauncher(Protocol):
    def launch(self, job_path: Path) -> LaunchResult: ...
```

The initial `DetachedProcessLauncher` invokes:

```text
python -m agency.jobs.worker <absolute-job-path>
```

It detaches the child from the submitting process on both supported platform
families:

- POSIX: a new session with no inherited standard streams.
- Windows: detached/new-process-group creation flags, no console window, and no
  inherited standard streams.

The worker receives only a job-file path. Dashboard objects, open file handles,
and in-memory configuration are never passed across the process boundary.

A future `DaemonLauncher` can enqueue or signal the same durable job document.
Submitters and worker execution semantics will not change. This launcher boundary,
not the current subprocess implementation, is the migration point to a dedicated
worker service.

### 3. Execution

`agency.jobs.worker` loads the job record and current `config.yaml`, resolves the
group and agent, then invokes a shared `execute_job()` function. This function is
the sole orchestration-level caller of `integration.run()`.

`execute_job()`:

1. Atomically changes the job from `queued` to `running` and records PID/start time.
2. Resolves the agent directory, integration configuration, timeout, and sandbox
   policy from the referenced group configuration.
3. Runs the integration with the immutable prompt snapshot.
4. Writes isolated stdout/stderr logs.
5. Records exit code, duration, changed files, summary, and completion time.
6. Atomically changes the job to `complete` or `failed`.
7. Applies trigger-specific result projection, currently decision-frontmatter
   updates for decision jobs.

Integration classes continue to own tool-specific command construction and result
parsing. The jobs package owns orchestration and durable run state.

## Job Contract

`JobSpec` is a versioned, fully serializable document. It contains references and
snapshots, not Python runtime objects:

- `schema_version`
- `job_id`
- `config_path`
- `group_key`
- `agent_name`
- `trigger` (`scheduled_prompt`, `manual_prompt`, `decision`, or
  `decision_retry`)
- `prompt_source` metadata for display and audit
- immutable `prompt_content`
- optional timeout override
- creation timestamp
- optional decision context: decision path and proposal path

The durable job record adds mutable execution fields:

- `status`: `queued`, `running`, `complete`, or `failed`
- worker PID where available
- started/completed timestamps
- stdout/stderr log paths
- exit code and execution summary
- changed files

Job records live at `{group}/shared/jobs/{job_id}.yaml`. Writes use a temporary
file plus atomic replace. IDs are collision-resistant and appear in prompt/log
filenames, so concurrent jobs cannot overwrite one another.

Prompt content is snapshotted when submitted. Editing or deleting a saved prompt,
proposal, or decision after launch does not change the instructions already given
to a worker. For decision jobs, the snapshot embeds the proposal content and the
human's answers; it does not instruct the integration to re-read those mutable
files as its source of instructions. Decision and proposal paths remain in the job
context only for audit links and result projection.

## Trigger Data Flow

All existing execution triggers converge before process creation:

```text
scheduled timer ───────────────┐
manual saved-prompt run ───────┼─> build JobSpec -> submit_job -> JobLauncher
approved decision ─────────────┤                         |
decision retry ────────────────┘                         v
                                            detached worker process
                                                       |
                                                       v
                                                  execute_job
                                                       |
                                                       v
                                                integration.run
```

Scheduled dispatch no longer runs integrations inline. After a due rule is
validated, it submits a job. It writes the existing deduplication marker only
after submission and detached launch succeed. The timer process may then exit
without affecting the agent.

Manual saved-prompt routes return HTTP 202 after successful launch. They no longer
use FastAPI `BackgroundTasks`.

Decision execution and retry submit decision jobs through the same API. Their
status remains visible in decision frontmatter for compatibility with the existing
decision UI, while the job document is the detailed run record.

## Decision Executor Resolution

Proposals may add an optional `execution_agent` frontmatter field. On the decision
form, the executor selection defaults in this order:

1. Proposal `execution_agent`.
2. Proposal `origin_agent` for existing proposals without an explicit executor.

The human may change the selection before submitting the decision. Agency validates
that the selected agent:

- exists in the group,
- resolves to an existing agent directory, and
- uses an integration whose `supports_execution` is true.

If validation fails, Agency renders the decision form again with a clear error. It
does not create the decision, change the proposal to `decided`, submit a job, or
select a fallback agent.

A successfully created decision stores:

- `execution_agent`: the validated executor selected at decision time.
- `execution_job_id`: the current job ID.
- `execution_job_history`: prior job IDs after retries.

Retries default to the executor persisted on the decision, not the proposal's
possibly changed metadata. A retry may select another valid executor. On successful
retry submission, the prior current job ID moves into history and the new ID
becomes `execution_job_id`.

## Failure And Recovery

### Submission failures

The trigger reports failure if the job record cannot be written or the launcher
cannot start a detached process. A failed launch is recorded as `failed` in its job
record with a launch-error summary.

Decision creation is transactional at the application level: executor validation
and job construction occur first; if durable submission or launch fails, Agency
does not leave the proposal marked `decided` or a decision claiming a pending run.
A retry launch failure leaves the existing decision failed and preserves its prior
job references.

### Worker failures

The worker catches integration, configuration, and filesystem errors and records a
useful failure summary. Integration timeout (exit code 124) is a failed job. Its
final job update and decision projection occur in a `finally`-protected completion
path where possible.

### Process isolation and stale jobs

Dashboard startup must stop treating every `running` decision as orphaned. A live
detached worker is valid even when the dashboard PID changes.

Status reconciliation checks the referenced job and worker PID. It leaves a live
worker untouched. A `running` job is marked failed only when its worker is confirmed
absent. If liveness cannot be determined reliably, Agency leaves the job running
rather than risk corrupting a live run; an explicit stale-age threshold can be
added with a daemon if operational experience requires it.

The worker writes status directly, so normal completion does not depend on polling
or dashboard availability.

## Concurrency

The launcher permits concurrent jobs without per-agent locking. Multiple jobs may
run for one agent at the same time. Each has an immutable ID, prompt snapshot, job
record, and log files.

This choice allows simultaneous edits in the same sandbox. Agency will not attempt
to merge, serialize, or undo conflicting changes. The UI may report multiple active
runs, but it must not collapse their job state into one shared per-agent marker.
superseded `.running-{agent}` markers therefore cannot be the authoritative job-state
model; running state is derived from active job records.

## Security

- The worker resolves group and agent paths through existing configuration helpers.
- Prompt and job paths are passed as argument-list elements, never interpolated into
  a shell command.
- Detached workers inherit only the environment needed by integrations; no browser
  request data or open server streams are inherited.
- Sandbox roots and allowed tools are resolved by the worker through the same
  configuration policy used today.
- Decision and job paths are validated against the selected group's allowed roots
  before reading or writing.
- Job documents may contain prompt text and operational paths and therefore remain
  under the existing group-owned `shared` tree rather than a public static path.

## Testing

### Unit tests

- `JobSpec` round-trip serialization and schema validation.
- Atomic creation and every valid status transition.
- Rejection of invalid transitions and malformed job files.
- Detached launch arguments and flags on POSIX and Windows.
- Prompt snapshot immutability.
- Unique records and logs for concurrent jobs belonging to one agent.
- Worker success, nonzero exit, timeout, exception, logs, and changed-file capture.
- Decision result projection and retry history.
- Executor resolution from `execution_agent`, superseded fallback to `origin_agent`,
  explicit form override, missing agent, missing directory, and non-executing
  integration.

### Trigger tests

- Scheduled dispatch submits through `submit_job` and writes a marker only after
  successful launch.
- Manual saved-prompt runs submit through `submit_job` and return 202.
- Decision creation and retry submit through `submit_job` and persist job IDs.
- None of the four trigger paths calls `integration.run()` or FastAPI background
  execution directly.

### Process tests

- A detached worker continues and records completion after its short-lived
  submitting process exits.
- Dashboard restart recovery leaves a live worker/decision running.
- Reconciliation marks a confirmed dead worker failed.

Run the focused job, dispatch, manual-run, and decision tests first, followed by:

```text
python -m pytest tests/ -q
```

## Migration

Existing saved prompts and schedules require no data migration. Existing proposals
without `execution_agent` use `origin_agent`. Existing decisions without job IDs
continue to render from their current execution fields and use superseded retry
resolution once; the next retry stores `execution_agent` and job references.

The current `execute_decision()` and dispatch `run_agent_prompt()` orchestration
functions are removed or reduced to compatibility wrappers after all call sites use
`submit_job()`. `integration.run()` remains stable.

The initial transport is a detached child process. Moving to a dedicated daemon
later requires implementing `DaemonLauncher`, deploying its consumer, and selecting
that launcher in configuration. Job producers, serialized `JobSpec`, worker
execution, integrations, and trigger routes remain unchanged.
