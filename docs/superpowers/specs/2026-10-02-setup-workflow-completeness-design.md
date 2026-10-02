# Setup Workflow Completeness

Date: 2026-10-02
Status: Design approved in conversation; written-spec review pending.
Branch: `feature/setup-workflow-completeness`
Worktree: `.worktrees/setup-workflow-completeness/`

## Problem And Evidence

First-run setup can register a workflow without creating its definition and
still report success. The observed guided Copilot session approved the
`software-delivery` blueprint and registered `atreides-delivery`, but never
materialized the blueprint in the configured workflow library.

The session's first `flowgency validate` failed because the workflow-library
directory did not exist. The agent created an empty directory, reran validation
successfully, and declared setup complete. The board then failed to resolve
the definition with `missing-blueprint`.

Three local gaps explain that false success:

- The setup skill names shipped blueprints but does not explicitly require
  their materialization. Its cited `ticket-workflow-steps.md` reference contains
  ticket-agent guidance rather than blueprint definitions.
- `cmd_validate` validates agent blueprints and prompts, not configured workflow
  definitions.
- `inspect_setup_status` considers valid configuration with at least one team
  ready without checking referenced workflow definitions.

A read-only reproduction confirmed that the packaged `software-delivery`
definition is valid, its configured copy is missing, and setup still reports
`ready`.

## Scope And Decisions

Fix future guided and manual setup through the shared setup skill, setup
completion checks, and explicit `flowgency validate` execution.

The user approved these constraints:

- Workflow creation remains owned by the setup skill. No new public template
  installation command or tool is introduced.
- Setup completion and explicit validation must reject missing or invalid
  referenced workflow definitions.
- Ordinary configuration loading and dashboard startup remain tolerant. A
  broken workflow must not take unrelated teams or workflows offline.
- Existing runtime data is untouched. This work does not restore the Atreides
  workflow, change its configuration, modify tickets, or alter the scheduler.
- No completion sidecar, persisted marker, last-good definition, startup
  conversion, or secondary authority is introduced.
- The existing waiting-page layout is retained. No visual companion or mockup
  is needed for the approved message and redirect behavior.

## Workflow Creation

Correct the canonical packaged setup skill and its relevant reference guidance
to make the required source step explicit.

After workflow and storage-path approval, and before the single atomic config
write, the skill must ensure every selected workflow has a validated definition
in the approved canonical workflow library:

1. Locate packaged examples through
   `flowgency.setup_assets.workflow_example_root()`, rather than assuming the
   ticket-agent reference contains them.
2. For an approved shipped blueprint absent from the destination library,
   inspect the packaged definition and create the canonical destination through
   the existing workflow-library creation API. Materialize only selected,
   approved blueprints, not every shipped example.
3. For an existing destination definition, validate and reuse it. Never
   silently replace a customized definition with a packaged example. Invalid,
   unreadable, unsafe, or identity-mismatched existing source is a blocker,
   not permission to overwrite it.
4. For custom blueprints, create the definition from the approved states,
   transitions, inputs, outputs, and evidence policy, using the same existing
   validation and publication conventions.
5. Inspect the resulting destination definitions before publishing the complete
   configuration with the revision-checked `ConfigStore` operation.
6. After publication, run `flowgency validate` and stop on a nonzero exit. Do
   not announce success or proceed to scheduler installation while validation
   is unresolved.

Configuration remains the sole control-plane authority. The workflow library
contains the canonical selected definition source. Packaged examples are source
templates only; they are never a runtime fallback.

Creation must preserve the existing approval, path-safety, validation,
revision-drift, and atomic-write requirements. An existing source conflict must
stop or be re-inspected; it must not authorize an overwrite. Validation and
service startup perform no creation or repair.

## Shared Read-Only Validation

Introduce one focused validation entry point consumed by setup completion and
CLI validation. It takes one parsed configuration snapshot and returns existing
structured `ValidationIssue` records. It does not load a second configuration,
create directories, initialize ticket storage, or launch services or agents.

For every configured workflow instance:

- Require a configured workflow-library root when a workflow references one.
- Inspect the referenced blueprint through `WorkflowLibrary.inspect`, reusing
  its source-size limit, safe path handling, YAML parsing, and domain validators.
- Require the definition's `id` to equal the configured blueprint ID.
- Report missing, unreadable, unsafe, malformed, oversized, or schema-invalid
  source as a validation issue associated with the affected team and workflow.
- Identify the affected workflow and required correction without exposing raw
  source, credentials, or private exception details in public messages.

Inspect each distinct referenced blueprint once per validation call, while
retaining issue context for each affected instance. Do not persist inspection
results or reuse a last-good snapshot across calls. A later poll must inspect
the current definition again.

Only referenced definitions participate in this completeness check. An invalid
unused blueprint cannot prevent setup completion. A team with no workflows is
valid and requires no workflow library or definition installation.

The validator checks definition availability and validity, not ticket-provider
connectivity, ticket compatibility, local-network consent, Git publication,
or scheduler activation. Existing checks for those independent contracts remain
unchanged.

## Completion And CLI Behavior

### Setup

Preserve the existing configuration-state distinctions:

- Absent config remains `waiting`.
- Invalid config remains `invalid`.
- Config with no teams remains `incomplete`.
- Valid config with missing or invalid referenced workflow definitions becomes
  `incomplete` with a concise corrective message.
- Valid config with teams and valid referenced definitions may become `ready`
  once the existing service-readiness checks succeed.

Apply the shared check to setup pages, session navigation, status polling, and
post-Stop destinations through their existing setup-status path. The waiting
page must render the corrective message without a layout redesign. Polling
must not redirect while a required definition is unresolved. Once its current
source validates and services are ready, the existing success redirect resumes.

The connected session remains accessible during incomplete setup. Validation
failure does not terminate, relaunch, or otherwise change process ownership.
Navigation still detaches rather than stops the server-owned CLI. Preserve the
existing behavior in which an already-complete session opened for inspection
stays on its terminal view.

Strict setup status does not become a general service-startup precondition.
Existing configured dashboards remain accessible through their ordinary routes;
affected workflow boards keep their existing unavailable behavior. Visiting a
setup surface may report incompleteness, but must not repair data or disable the
rest of the dashboard.

### Explicit CLI Validation

Add shared workflow issues to the existing `flowgency validate` result alongside
the current agent-blueprint and prompt checks. Preserve the established error
envelope and validation exit-code convention in both text and JSON modes.

Missing or invalid referenced definitions must produce a nonzero validation
result. Valid shipped and custom definitions, and teams with no workflows,
remain valid. The command must not install templates or repair source as a side
effect of validation.

## Verification

Reuse existing tests and helpers wherever suitable. Coverage must include:

- Valid shipped and custom definitions; reuse of an existing customized source.
- Missing source, malformed YAML, schema-invalid and oversized source,
  identity mismatch, read failure, and unsafe link or reparse paths.
- Multiple workflow instances sharing a blueprint and useful issue attribution.
- An invalid unreferenced blueprint and a team without workflows.
- Nonzero text and JSON CLI validation results for unresolved definitions,
  without weakening existing agent and prompt validation.
- Setup remaining incomplete when config is written before its definition,
  then becoming ready after the definition is created or corrected.
- Waiting-page error messaging and absence of premature browser redirects.
- The existing completion redirect, completed-session inspection behavior, and
  session ownership remaining unchanged.
- Ordinary dashboard startup and unaffected workflow access remaining tolerant
  of a broken referenced workflow.
- Packaged examples and corrected setup guidance working from an installed
  distribution, not only checkout-relative paths.
- No definition, config, ticket, or scheduler mutation during read-only
  validation or setup status inspection.

Run focused checks while iterating. Before review and completion, run the full
Python suite and the committed default-headless browser matrix sequentially;
they must not overlap because they share the UI runtime directory. Follow the
repository's required review and integration gates. Do not weaken assertions,
change unrelated snapshots, or adjust tolerances to obtain a pass.

The unchanged Python baseline at `6cefb50` in the named worktree passed with
3,293 tests passed, 19 skipped, and one existing Starlette deprecation warning.
The JUnit result is retained in the ignored worktree path
`.superpowers/baseline.junit.xml`.

## Alternatives Considered

- A dedicated template-install command or tool would make installation more
  mechanical, but adds a public operation and maintenance surface. The approved
  design reuses existing library APIs and keeps creation skill-owned.
- Globally rejecting invalid workflow references during normal config loading
  or service startup would be simpler, but could take unrelated functionality
  offline. The user chose strict setup and explicit validation only.
- Automatically repairing the current Atreides definition would address the
  immediate board, but the user explicitly excluded existing runtime-data
  repair from this work.
- Updating instructions alone would leave mechanical validation capable of
  accepting the same omission. Shared validation is required as the completion
  gate.

## Acceptance Criteria

1. A setup session selecting a shipped workflow creates its approved definition
   in the canonical workflow library before publishing the complete config.
2. Existing selected definitions are validated and reused, never silently
   overwritten or replaced by packaged examples.
3. Missing or invalid referenced definitions prevent setup completion and make
   explicit CLI validation fail with actionable structured issues.
4. Correcting the current definition allows polling to complete setup without
   restarting or stopping the connected CLI.
5. Ordinary dashboard startup remains tolerant and unrelated functionality
   remains available.
6. Teams without workflows require no workflow materialization, and invalid
   unused blueprints do not block completion.
7. No existing Atreides config, ticket data, workflow source, or scheduler state
   is changed by this work.

## Next Step

Obtain user review of this written specification, then create and separately
commit the implementation plan before implementation begins.