# Explicit Setup Routine And Schedule Proposals

**Date:** 2026-09-06
**Status:** Design approved in conversation; written specification pending review

## Problem

First-time setup can register a complete team without proposing any recurring
work. The current setup skill permits explicit absence of routines and schedules
and forbids inventing a cadence without supporting context. It does not require
an explicit team-level decision to operate manually.

The reported setup produced five agents without routine entries and a team with
dispatch disabled. The user confirmed that no schedules were proposed. The
dashboard's "no schedule" label reflects that configuration; it is not evidence
of a rendering defect or a lost approved schedule.

## Goals

1. Include project-grounded routine and schedule proposals in the first complete
   team draft by default.
2. Require explicit approval, targeted revision, or a manual-only choice.
3. Separate approval of recurring work from permission to activate automatic runs.
4. Persist and verify the approved routines and their prompt references.
5. Report configured scheduling, dispatch enablement, and scheduler status without
   treating them as equivalent.

## Scope And Non-Goals

Change the canonical setup skill, its shipped copies through the existing
packaging mechanism, directly relevant setup documentation, and regression tests.
Use the existing configuration schema, scoped prompts, routine schedules, and
singleton scheduler. No new runtime behavior or configuration fields are needed.

Do not redesign the dashboard, require a routine for every agent, invent a
business cadence as fact, install another scheduler, or expand agent permissions
to accommodate a suggested routine. Do not modify the user's existing team,
runtime config, prompts, or scheduler as part of this change. Retrofitting that
team requires separate approval.

## Proposal And Approval Flow

Preserve the existing project inspection, team naming, agent count, and first
complete team draft ordering. Routine proposals belong in that draft, not in a
new questionnaire before users see the agents and their responsibilities.

For each agent, propose useful recurring work supported by the inspected project
and approved responsibilities. Each proposed routine identifies its task,
prompt purpose, recommended schedule, and rationale. Label a suggested cadence
as a recommendation rather than claiming it describes an existing practice.
Keep tasks within the profile's proposed permissions and ownership boundaries.

An agent with no useful recurring role may remain manual-only with a short
explanation. Do not add filler routines to satisfy a numerical requirement.
When evidence is insufficient to propose useful recurring work, ask one focused
question about the desired recurring checks or operating cadence before
finalizing the proposal. Incorporate the answer rather than silently omitting
scheduling. This clarification follows the first consolidated draft, preserving
the existing rule against additional questions before that draft.

The consolidated team review must explicitly offer:

- Approve the proposed team, including its listed routines and schedules.
- Request targeted changes to the profiles, routines, or schedules.
- Choose manual-only operation for the team.

The approval option must name routines and schedules; a generic team approval
with no visible scheduling decision is insufficient. Manual-only operation must
be a user choice, not an inferred consequence of missing proposals. Mixed teams
are valid: some agents can have approved routines while others remain manual-only.
Existing survivor and team-count rules continue to preserve approved profiles,
including their routines and schedules.

## Activation And Persistence

After the team and routines are approved, separately confirm whether automatic
execution should be enabled. Explain that an already-running singleton scheduler
can pick up enabled routines once configuration is saved. Schedule approval alone
does not authorize activation.

Resolve this activation choice before the existing single atomic config write.
Choosing manual-only operation produces no routine entries for the new team and
leaves its dispatch disabled. Approving schedules but declining activation saves
the approved routines with dispatch disabled. Approving activation saves those
routines with dispatch enabled. No second config write is introduced to complete
initial activation, and unrelated teams and existing settings remain preserved.

Map every approved routine to the existing instance `routines` field and create
its selected scoped prompt document using the existing prompt contract. Preserve
approved schedule values, optional arguments, and semantic memory selectors.
Do not encode schedules in blueprint instructions or native runtime files.

All existing path approval, filesystem safety, configuration revision checks,
cross-reference validation, and atomic replacement requirements remain in force.

After configuration verification, offer singleton scheduler installation when
activation was approved, using the existing install and status commands. Never
install it solely because schedules were approved. If installation is declined,
fails, or its status cannot be verified, report that separately; do not claim
automatic execution is operational or silently change the saved config.

## Verification And Completion Reporting

Before declaring setup complete, parse the saved configuration and compare it
with the approved in-session choices. Verify every approved routine's owning
instance, ID, scoped prompt reference, schedule, arguments, and memory selection.
Verify the selected prompt documents exist and meet the prompt contract, and
confirm dispatch enablement matches the activation decision.

Stop on missing or mismatched approved data, revision drift, validation failure,
or filesystem failure. Report the discrepancy without presenting setup as
complete or performing an unapproved repair write. Preserve existing failure
handling and do not delete approved source files as cleanup.

The completion summary must distinguish:

- **Manual-only:** no routines approved; dispatch disabled.
- **Scheduled but inactive:** routines saved; dispatch disabled.
- **Scheduled with dispatch enabled:** routines saved; activation approved.

Report singleton scheduler status separately, including not installed, confirmed
status, installation failure, or unknown status as supported by observed command
results. Dispatch enabled is not itself proof that the platform scheduler is
installed or running. Retain the existing storage, instance, memory, and config
summary, and list the saved routine schedules.

## Testing

Extend the neighboring setup skill contract tests and existing packaging parity
checks. Verify that the canonical and shipped instructions require proposals in
the first draft, label cadence recommendations, retain per-agent manual-only
exceptions, and require an explicit team-level scheduling choice.

Cover the insufficient-context clarification ordering, separate activation
approval before the single write, persistence comparison against approved
choices, and all three completion states with independent scheduler reporting.
Preserve existing tests for consolidated team review, profile survivors, path
approval, prompt contracts, and atomic configuration writes.

Static instruction tests demonstrate the required contract and packaging parity,
not guaranteed agent compliance. A guided setup acceptance exercise should
confirm that the assistant visibly proposes schedules and respects manual-only
and deferred-activation choices before claiming end-to-end behavior verified.
Any exercise uses disposable data and must not activate real recurring work.

## Baseline And Review Gate

The unmodified feature worktree baseline on 2026-09-06 returned 2,035 passed,
6 skipped, and 4 failed. All four failures were live Copilot projector probes
blocked by model-catalog timeouts, DNS failures, or network-dependent token
validation. The user authorized proceeding with documentation despite those
failures; this is not permission to disregard later regressions or skip the
implementation verification gates. Connectivity troubleshooting is out of scope.

No visual companion or mockup was needed or approved for this instruction-only
design. The next gate is user review of this written specification. Only after
that approval should the implementation plan be written and committed separately.