# Recover Scheduled Jobs That Fail Before Runtime Invocation

## Approved Scope

The user approved a focused launch-recovery fix: retry confirmed pre-runtime
setup failures without changing agent prompts, permissions, or routine schedules.
Do not replay older records whose safety cannot be established from explicit
evidence. Do not clear markers, delete job history, or manually start other agents.

## Failure and Cause

The first four scheduled jobs started Flowgency workers but failed while starting
the ticket server because the dashboard environment had an incompatible MCP SDK.
Updating the environment repaired that import. A subsequent manually authorized
Auditor run successfully invoked Copilot and its ticket tools.

The general recovery defect remains: `lost_occurrences` recognizes only failures
that never launched a worker. `execute_job` launches the worker and records a
running state before ticket-server setup and before `integration.run(request)`.
A setup failure therefore leaves the submission marker satisfied until the next
daily or weekly interval, although no AI execution happened.

## Design

Keep worker identity, launch timestamps, terminal status, and existing queue
occupancy semantics unchanged. Add explicit worker-owned failure evidence to the
existing mutable `JobRecord.result_metadata`, without changing immutable job
authority or requiring a record-schema migration:

```json
{"execution_failure": {"phase": "before_runtime"}}
```

Emit this evidence only when an ordinary execution/setup exception terminalizes
a job as failed and `integration.run(request)` has not been invoked. Set the
in-memory invocation guard immediately before calling the integration, so a
raised integration exception cannot be mistaken for an unstarted run. Cancellation,
authority failures, retired triggers, nonzero runtime exits, publication failures,
and uncertain outcomes do not acquire retry eligibility.

Merge this evidence with existing result metadata, preserving ticket cleanup and
other diagnostics. Do not authorize replay while ticket cleanup remains pending
or unconfirmed. Existing queue and ticket reservation guards continue to apply.

Extend dispatch's existing lost-occurrence predicate to recognize the exact
explicit pre-runtime failure evidence even when a worker PID or launch timestamp
exists. Missing, malformed, or unknown metadata must not widen retry eligibility.
Retain the existing worker-spawn-failure recovery behavior.

The normal dispatcher offers the original scheduled occurrence again within its
existing catch-up window. It creates a new durable job rather than rewriting the
failed job. Preserve `at` and `every` anchors, later pending/launched job suppression,
pool/memory exclusion, and duplicate prevention. No retry loop is added inside a
single worker and no schedule is shortened.

## Alternatives

- Retrying every failure can repeat agent side effects and is rejected.
- Clearing timestamps or submission markers loses provenance and changes queue
  semantics, so it is rejected.
- Matching historical error text or inferring safety from missing session/output
  fields is not reliable evidence and is rejected.

## Verification

Use existing job-execution and dispatch-launch-recovery fixtures. Prove a worker
setup exception before runtime invocation produces persisted evidence, does not
invoke the integration, and is offered again through a real dispatch cycle.
Cover both `at` and `every` schedules, retained failed history, unchanged anchors,
catch-up expiration, pending/later-run suppression, and no third submission after
recovery. Negative cases cover integration exceptions, nonzero exits, cancellation,
authority failures, malformed/absent metadata, and unresolved ticket cleanup.

Establish a clean full-suite baseline in the isolated worktree, use focused
red/green checks while editing, then run the complete suite before review.
Review each task and the whole branch. Integrate by fast-forward only, preserve
main's staged and unstaged task-file edits, rerun the full main suite, publish both
branches, preserve evidence, and remove the worktree normally.

## Non-Goals

No reporting-protocol or empty-board changes, permission widening, dependency
upgrades, cleanup-task edits, UI changes, historical job reclassification, or
automatic replay of the user's existing failed jobs.