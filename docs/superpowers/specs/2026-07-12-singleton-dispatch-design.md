# Singleton Dashboard Dispatch - Design

**Date:** 2026-07-12
**Status:** Approved design, pending written-spec review
**Topic:** Unify group schedules and host scheduler status around one Agency dashboard configuration

## Problem

Agency currently exposes two different meanings of dispatch without distinguishing
them in the UI:

- The Agent Groups page displays "Dispatch on" when a group has
  `dispatch.enabled: true` in `config.yaml`.
- The Dispatch page displays "Set Up Dispatch" when Agency cannot find its
  platform scheduler.

The local Agency Setup run also created a project-specific Windows task named
`christag-agency-dispatch`. That task directly runs `agents/shared/dispatch.ps1`
at 07:00 and 21:00. The dashboard only recognizes its own global task,
`AgencyDispatch`, which runs `agency.dispatch.run` every 15 minutes. Renaming the
project task would make the UI appear healthy without changing its incompatible
action, trigger, deduplication, or execution path.

This creates ambiguous status, duplicate scheduler ownership, and a risk that
installing the dashboard scheduler alongside the generated project task will run
the same agents twice.

## Product Invariant

Agency supports exactly one dashboard installation and one authoritative
`config.yaml` per operating-system user. Every scheduled agent group is stored in
that config. Multiple dashboard processes backed by different configs for the
same user are unsupported.

The product does not need a registry of config paths or a second project schedule
format. A platform scheduler stores the canonical absolute path to the singleton
dashboard config in its action.

If setup discovers multiple valid Agency configs and cannot identify the
authoritative one from an explicit `$AGENCY_CONFIG` override, it stops and asks
the user to select the singleton installation. It never schedules all candidates.

## Goals

- Use one user-level platform scheduler for every group in the dashboard config.
- Make configured group schedules and host scheduler health visibly distinct.
- Give the dashboard, CLI, and Agency Setup one scheduler-management API.
- Prevent setup from creating project-specific scheduler implementations.
- Detect scheduler definitions that point to the wrong config or use the wrong
  action or interval.
- Replace the current local project task only after the global scheduler is
  installed and verified.

## Non-Goals

- Supporting multiple dashboard configs for one user.
- Adding a config registry under `$HOME` or elsewhere.
- Adding standalone project dispatch manifests.
- Supporting or automatically discovering superseded project-specific tasks in
  production code.
- Enforcing the singleton invariant with a cross-process lock.
- Changing the semantics of group `at`, `every`, or conditional schedule rules.
- Running schedulers with elevated privileges or stored user credentials.

## Decisions And Rejected Alternatives

### Chosen: One scheduler and one dashboard config

The platform scheduler invokes the existing Python dispatch runner with the
canonical config path. Each heartbeat evaluates all groups whose
`dispatch.enabled` value is true and submits due work through Agency's job system.

Platform identities remain the existing global identities:

- Windows Task Scheduler: `AgencyDispatch`
- Linux user systemd: `agency-dispatch.timer` and `agency-dispatch.service`
- macOS launchd: `com.agency.dispatch`

### Rejected: Rename the project task

`christag-agency-dispatch` has different triggers and runs a generated PowerShell
dispatcher directly. Renaming it would not turn it into the global Agency
heartbeat and could cause the dashboard to report a false healthy state.

### Rejected: Registry of project configs

A registry is only needed when multiple independent configs are supported. Under
the singleton dashboard invariant it adds stale pointers, conflict handling, and
another lifecycle without adding capability.

### Rejected: One scheduler per project

Per-project tasks duplicate platform logic, cannot be managed reliably from the
dashboard, and can overlap with the global heartbeat. Schedule ownership belongs
to the group entries in the central dashboard config.

## Architecture And Ownership

### Authoritative config

The singleton `config.yaml` owns:

- registered groups and agents;
- each group's `dispatch.enabled`, timeout, daily limit, and schedule rules;
- the desired global heartbeat interval under `agency.dispatch.interval`.

The config does not own observed runtime state. In particular,
`agency.dispatch.installed` is no longer used to decide whether the scheduler
exists. Existing occurrences of that key are ignored; no compatibility behavior
is required.

### Scheduler management

`agency/dispatch/install.py` remains the only module that creates, inspects, or
removes platform scheduler resources. Its public operations must:

- install or idempotently update the global scheduler for a canonical config;
- inspect the complete scheduler definition, not only its name;
- identify absent, inactive, healthy, and misconfigured states;
- remove only the global Agency scheduler;
- use current-user, non-elevated execution without storing credentials.

Status inspection compares the actual scheduler definition with the expected
executable, dispatch module, canonical config path, heartbeat interval, and
enabled state. A scheduler with the right name but a mismatched definition is
reported as misconfigured rather than active.

### Dispatcher

`agency/dispatch/run.py` continues to accept one required `--config` path. It
loads that config once per heartbeat, iterates every enabled group, evaluates due
rules, and submits jobs through the existing job submission layer. Existing
per-rule marker files remain the deduplication authority.

No registry enumeration, project script invocation, or superseded-task detection is
added to the runner.

### CLI

`agency/cli.py` adds a `dispatch` command family:

- `christag-agency dispatch install [--config PATH] [--interval MINUTES] [--replace]`
- `christag-agency dispatch status [--config PATH]`
- `christag-agency dispatch uninstall [--config PATH] [--force]`

The commands default to the dashboard's active `CONFIG_PATH` and accept an
explicit config path for Agency Setup. They delegate to
`agency/dispatch/install.py`; they do not reproduce platform commands.

Installation reads the desired interval from `agency.dispatch.interval`, using
15 minutes when it is absent. Supplying `--interval` atomically updates that
desired value before installing the matching scheduler definition.

Installation is idempotent when an existing global scheduler points to the same
canonical config. An existing global scheduler pointing to another config is a
singleton conflict and is not overwritten silently. Replacing that conflicting
definition requires `dispatch install --replace` after explicit user approval.
Likewise, uninstall refuses to remove a scheduler tied to another config unless
the user explicitly supplies `--force`. Status returns a nonzero exit status when
the scheduler is absent, inactive, or misconfigured so setup can verify it
without parsing display text.

### Dashboard

The dashboard calls the same installer module directly. Runtime scheduler
inspection is authoritative; `get_dispatch_status()` must not combine that result
with the persisted `agency.dispatch.installed` flag.

The desired heartbeat interval remains configuration, while installed, active,
and definition-match values come from the platform scheduler on every status
request.

### Agency Setup skill

Agency Setup continues generating agent identities, memory, prompts, and the
interactive runtime workspace. It also atomically registers the complete group
and its schedule rules in the singleton Agency config.

It no longer generates or installs any of the following:

- `agents/shared/dispatch.ps1`;
- `agents/shared/install-dispatch.ps1`;
- `agents/shared/dispatch.sh`;
- project-specific systemd service or timer files.

After config registration, setup asks whether scheduling should be enabled. On
approval it invokes the official `christag-agency dispatch install` interface
with the selected config, then invokes `dispatch status` to verify the global
scheduler. If no valid singleton dashboard config is available, setup may still
generate the agent team, but it reports that dashboard registration and
scheduling were not completed. It does not create a fallback project scheduler.

## Data Flow

### Setup

1. Agency Setup locates and validates the singleton dashboard config.
2. It resolves the project and group paths to canonical absolute paths.
3. It atomically merges the group's agents, workspace, and dispatch rules while
   preserving unrelated settings and concurrent changes.
4. It parses the config from disk again and verifies the complete group entry.
5. With user approval, it calls the official global scheduler installer for that
   config.
6. It checks scheduler status and reports whether schedules are operational.

### Scheduled heartbeat

1. The platform scheduler invokes
   `pythonw -m agency.dispatch.run --config <config>` on Windows, or the
   equivalent configured Python executable on Linux and macOS.
2. The runner loads the singleton config.
3. Disabled groups are skipped.
4. Due rules from enabled groups are submitted through the job system.
5. Rule markers prevent a subsequent heartbeat from submitting the same event
   again.

### Dashboard status

1. Group schedule state is read from each group's config.
2. Global dispatcher state is inspected from the operating system.
3. The UI presents those states separately and never infers scheduler health
   from a group's enabled flag.

## UI Behavior

### Agent Groups

Replace "Dispatch on" with "Schedule enabled". This badge describes only the
group configuration.

### Dispatch page

Use platform-neutral language such as "system scheduler" instead of claiming a
systemd timer will be installed on every platform. Present one of these states:

- **Dispatcher active:** the scheduler is enabled and its definition matches the
  current config and desired interval.
- **Dispatcher inactive:** the scheduler is absent or disabled.
- **Dispatcher misconfigured:** a global scheduler exists but its action, config
  path, executable, or interval does not match.

The primary action is "Set Up Dispatcher" when absent and "Repair Dispatcher"
when misconfigured. A repair that would replace a scheduler pointing to another
config requires explicit confirmation because it represents a singleton
conflict. The confirmed request uses the same guarded replacement operation as
the CLI; ordinary setup and repair never imply replacement.

### Group schedule editing

Schedules remain editable while the dispatcher is inactive. The group editor
shows an amber warning that enabled schedules will not run until the global
dispatcher is active. Configuration must not be hidden merely because the host
scheduler is absent.

## Failure Handling

- Config registration is completed and verified before scheduler installation.
- If scheduler installation fails, the valid group configuration remains in
  place and the UI reports "Schedule enabled, dispatcher inactive."
- A scheduler definition mismatch is surfaced with the mismatched field; it is
  not collapsed into either "not installed" or "active."
- An existing scheduler tied to another config is a conflict. Setup and the CLI
  stop without silently replacing it.
- Reinstallation for the same canonical config is idempotent and may update the
  desired interval.
- Missing platform APIs or permissions produce actionable errors and leave the
  config intact. Setup does not weaken execution policy or request elevation.
- Scheduler removal is idempotent when the global scheduler is already absent.

## Local One-Time Replacement

The current workstation is migrated explicitly after the implementation is
available. This is an operational step, not product migration code:

1. Disable `christag-agency-dispatch` without deleting its definition.
2. Install `AgencyDispatch` against this repository's canonical `config.yaml`.
3. Verify that its action, 15-minute trigger, enabled state, and config path all
  match.
4. Invoke the global task once and verify a successful no-op or due-job
  submission through Agency's job/log state.
5. Remove the disabled `christag-agency-dispatch` task.
6. Remove the obsolete generated `agents/shared/dispatch.ps1` and
   `agents/shared/install-dispatch.ps1` files.
7. Recheck Task Scheduler and confirm that exactly one Agency scheduler remains.

If installation or verification fails, remove any newly created global task and
re-enable the unchanged project task. This provides rollback without allowing
both schedulers to run concurrently.

Production code and the setup skill do not search for or migrate similarly named
superseded tasks on other machines.

## Testing

### Scheduler unit tests

- Install, status, and uninstall behavior for Windows, Linux, and macOS.
- Idempotent installation for the same canonical config.
- Detection of config-path, action, interval, enabled-state, and task-identity
  mismatches.
- Singleton conflict behavior when the global scheduler points to another
  config.
- Safe errors for missing platform APIs and insufficient permissions.

Platform tests mock Task Scheduler COM, systemd commands/files, and launchd
commands/files; automated tests do not modify real host schedulers.

### CLI tests

- `dispatch install`, `dispatch status`, and `dispatch uninstall` delegate to the
  shared installer API.
- Exit statuses distinguish healthy state from absent, inactive,
  misconfigured, and failed operations.
- Explicit config paths are canonicalized before comparison or installation.

### Dispatcher tests

- One heartbeat evaluates multiple enabled groups from one config.
- Disabled groups are skipped.
- Two heartbeats cannot submit the same `at` rule twice.
- Existing daily limits and job-submission failure behavior remain intact.

### Dashboard tests

- A group with `dispatch.enabled: true` renders "Schedule enabled" regardless of
  global scheduler state.
- The Dispatch page renders active, inactive, and misconfigured states from
  runtime inspection.
- Persisted `agency.dispatch.installed` cannot make an absent scheduler appear
  installed.
- Group schedule controls remain available while the dispatcher is inactive.
- Dispatch copy is platform-neutral.

### Agency Setup contract tests

- The skill does not generate project-specific dispatcher or scheduler files.
- It writes complete group schedule rules to the selected singleton config.
- It uses the official global scheduler CLI after user approval.
- It verifies scheduler status and reports conflicts without creating a fallback
  task.

### Local acceptance verification

- The full pytest suite passes.
- Exactly one Task Scheduler entry named `AgencyDispatch` exists.
- Its action points to this dashboard config and its next heartbeat is visible.
- `christag-agency-dispatch` no longer exists.
- The Agent Groups and Dispatch pages report their independent states correctly.
- A manually triggered heartbeat exits successfully without duplicate job
  submissions.

## Documentation Changes

Update user-facing dispatch and Agency Setup documentation to state:

- one dashboard config and one platform scheduler are supported per user;
- group schedule enablement is not proof that the scheduler is active;
- the global scheduler evaluates all enabled groups;
- setup uses Agency's official scheduler interface and does not create
  project-specific tasks.

## Acceptance Criteria

The change is complete when one global scheduler drives every enabled group in
the singleton dashboard config, the dashboard accurately distinguishes schedule
configuration from scheduler health, Agency Setup cannot create a second
project-specific scheduler, the local superseded task has been removed after verified
replacement, and all automated and local acceptance checks pass.