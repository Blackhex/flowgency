# Generic Workflow Transition Outputs

Date: 2026-09-18

Status: Generic outputs and Git-evidence design directions approved; revised
written specification awaiting user review.

## Goal

Make durable results an explicit part of the generic workflow-definition
process. A result-producing transition must not succeed without its required
outputs, and committed outputs must remain visible on the ticket after later
transitions, including terminal states.

This is a contract for every workflow, not special handling for Research,
Delivery, particular agents, or fields named `findings` and `conclusion`.
Overview shows the latest saved value of each output. History preserves the
outputs of earlier accepted transitions and their original context.

Code changes are represented by an immutable Git-change artifact generated
from exact committed revisions, with a line-by-line diff viewer. Whether those
commits must also be pushed is determined by the project's constitution and
explicit configured policy, not a universal Flowgency requirement.

## Evidence and Existing Contract

The inspected Research ticket reached Closed with `findings` and `conclusion`
present only in accepted transitions' `effective_inputs`. Its current field
values and provenance for both fields are absent. Both shipped workflows
currently declare empty output lists, even for transitions producing results.

The transition engine deliberately separates temporary inputs from committed
outputs. It validates required outputs and persists submitted outputs,
provenance, the state change, and the transition event in one ticket mutation.
Existing tests protect the rule that attempt-only inputs do not overwrite
current fields or their provenance. Preserve that rule.

The inspector currently classifies outputs using only transitions outgoing
from the current state. History already renders recorded `effective_outputs`.
The generic editor already exposes per-transition inputs, outputs, and required
flags. The setup reference incorrectly describes outputs as optional in general.

Job Detail still renders changed-file names and added/removed line counts. The
former Decisions page rendered a similar summary; the current application has
no line-by-line workspace diff viewer. Job capture retains a base commit and
file-change metadata, not an immutable source patch. Its Git fallback includes
the workspace's dirty files, so that summary cannot establish which changes a
particular agent or ticket produced. Do not reuse it as verified Git evidence.

Relevant implementation anchors:

- [Transition definitions](../../../flowgency/workflows/models.py)
- [Transition validation](../../../flowgency/workflows/rules.py)
- [Ticket mutations](../../../flowgency/tickets/service.py)
- [Ticket view projection](../../../flowgency/tickets/views.py)
- [Ticket inspector](../../../flowgency/templates/_ticket_inspector.html)
- [Workflow editor](../../../flowgency/static/workflow-editor.js)
- [Setup workflow guidance](../../../.github/skills/flowgency-setup/references/ticket-workflow-steps.md)
- [Existing changed-file capture](../../../flowgency/jobs/changes.py)
- [Job Detail](../../../flowgency/templates/job_detail.html)
- [Ticket tools](../../../flowgency/tickets/mcp_server.py)

## Scope and Boundaries

In scope are the generic workflow authoring process, setup and agent guidance,
tool descriptions, maintained workflow examples and packaged assets, ticket
projection and rendering, trusted committed-change capture, project publication
policy, an immutable diff viewer, documentation, and regression coverage.

There are no ticket backfills, history rewrites, automatic input promotion,
startup conversions, historical job replays, agent launches, or permission
changes. Do not infer results from reports, logs, semantic memory, state names,
field names, or agent roles.

Retain the generic field/output model and existing artifact references. Add an
opt-in artifact format for Git changes, a generic trusted capture operation,
and structured publication policy under canonical configuration. Do not add
workflow-specific APIs, a second ticket-result store, or metadata duplicating
the role already expressed by `outputs`. Existing definitions and ordinary
artifact fields retain their behavior without conversion.

Canonical configured workflow libraries remain authoritative. Updating shipped
examples does not silently replace separately installed definitions. The
authoring contract applies when definitions are created or deliberately edited
through the normal definition process. Existing ticket data remains untouched;
historical input-only values may consequently remain unset in Overview.

Do not manufacture evidence by committing, rebasing, merging, or pushing on
the agent's behalf. Do not reconstruct historical run diffs or redesign the
existing best-effort job change collector. No PR integration, patch application,
or code editing is part of the read-only viewer.

## Generic Definition Process

For every transition, workflow authors and setup agents must explicitly decide:

1. Which values the transition consumes or checks: declare them in `inputs`.
2. Which durable results the transition produces: declare them in `outputs`.
3. Which outputs are necessary for success: mark each of those as required.
4. Whether a result needs an artifact contract, such as verified Git changes.
5. Whether the transition intentionally produces no results: use `outputs: []`.

Use the shared field catalog for IDs, labels, and types. A field can be produced
by one transition and consumed by another; its role is per transition, not a
global input/output type. Existing support for a field appearing in both lists
of one transition is unchanged: inputs are evaluation context and outputs are
explicit committed values. Do not copy between the two payloads implicitly.

During setup or definition review, present each transition's source and target,
inputs, outputs, and required flags. A transition described as producing a
deliverable must declare that deliverable as an output. Reports and memory are
not substitutes. Intentionally output-free administrative transitions, such as
starting work or reopening a ticket, remain valid.

The existing generic editor remains the authoring surface. Retain its separate
Inputs and Outputs controls and its per-field required choice; preserve those
choices through preview, save, and reload. Newly added output uses retain the
existing required-by-default behavior, with an explicit optional choice.
For artifact fields, expose the ordinary artifact or Git-change format through
the existing field-definition controls. Persist the format in the reusable
field definition; it is not inferred from a label, filename, or MIME type.
Only artifact fields may declare that format. No new wizard, confirmation
modal, or redesign of the existing editor is needed.

The validator cannot infer semantic intent from names or prose. Therefore:

- Do not require at least one output on every transition.
- Do not infer that an input is really an output from its label.
- Enforce declared output requirements uniformly at execution time.
- Make the consume/produce distinction mandatory in authoring guidance and
  definition review, and demonstrate it in maintained examples.

## Agent and Tool Contract

Shared ticket instructions, setup-generated routine guidance, and MCP tool
descriptions must express the same contract for any workflow:

1. Read the current ticket and available transition definition.
2. Inspect required inputs, required and optional outputs, preconditions, and
   qualitative criteria before doing the work.
3. Submit durable results through the transition's `outputs` mapping, with the
   current version, operation ID, and required assessments.
4. Treat a failed transition as uncommitted work. Read current context after a
   version conflict and follow the existing operation-retry contract.

Inputs can supply attempt-specific context without changing saved fields.
`ticket_report`, stdout, and memory do not populate output fields. A separate
`ticket_update` remains available for ordinary field edits but is not required
before a transition to persist that transition's results.

For a Git-change output, the agent selects the actual committed range for the
ticket and requests trusted capture from Flowgency. It submits the returned
artifact reference as the output. It does not upload an invented patch, cite
working-tree counts, or report a successful push as a substitute for evidence.
The project constitution continues to govern the agent's commit and publication
work; artifact capture itself performs neither operation.

Remove blanket wording that all outputs are optional. Do not hard-code field
IDs or transition names into generic agent instructions. Preserve ticket-tool
availability, assignment, runtime permissions, and network-consent boundaries.

Update source guidance and its maintained packaged copies together, following
the repository's existing setup-asset ownership and synchronization rules.
Generated build output is not an editable source.

## Validation and Persistence

Reuse the existing transition evaluation and atomic ticket mutation boundary:

- Validate the source state, version, declared output IDs, required values,
  field types, artifact references, preconditions, and assessments.
- Reject missing or invalid required outputs without advancing state or
  replacing current fields and provenance.
- On success, commit outputs, their agent/job/event provenance, state, and audit
  event together. Preserve receipt-based idempotency and revision checks.
- A later accepted output for the same field replaces its current value while
  earlier accepted transition values remain in History.
- Omitting an optional output leaves its current value unchanged. Supplied
  values, including allowed nulls, follow existing field validation semantics.

Retain existing structured validation errors. Agents must be able to identify
the invalid or missing output and correct the request; no successful response
may mask an uncommitted result. Do not introduce automatic synthesis, fallback
copies, or a second field-write request into this path.

## Git-Change Artifact Contract

A workflow can declare any artifact field as a Git-change output and mark its
use required or optional on each transition. The field ID is arbitrary. The
declaration requires a retained artifact produced by the trusted capture path,
not merely any uploaded file or external Git web URL. Ordinary artifact fields
continue to accept their existing supported references.

One capture represents one explicit base-to-end committed range from the
team's configured execution workspace repository. Resolve both revisions to
full commit object IDs and require the base to be an ancestor of the end. Do
not substitute the job's starting HEAD, a moving branch, a guessed merge base,
or the whole job's change list for that explicit range. Retain the exact commit
list and net tree diff between those endpoints; this is not a replay of every
intermediate edit.

Staged, unstaged, and untracked content never enters the artifact. A run working
several tickets selects a range for each ticket rather than attributing all run
changes to every ticket. Capture proves the selected Git content and its
association with the submitting agent, not exclusive human or agent authorship
of every included commit. Selecting a semantically appropriate range remains
the submitting agent's responsibility; do not claim automatic separation of
unrelated commits inside an explicitly selected range.

The retained artifact contains:

- Repository identity tied to the authorized workspace, without credentials.
- Full base and end commit IDs, the selected commit list, and a digest of the
  captured content.
- An immutable patch and changed-file summary generated from those Git objects,
  including additions, deletions, renames, and explicit binary-file metadata.
- Producing team, ticket binding, agent, job, and capture time from trusted
  context, not caller-supplied identity claims.
- The publication policy and its digest, plus the applicable verification
  receipt described below.

Bind the accepted reference and its capture identity to the transition event
when the transition commits. Capture can precede acceptance; it does not itself
advance a ticket. A captured but unsubmitted artifact is not a completed output.
Store the evidence through the existing immutable ticket-artifact boundary,
with trusted metadata that generic uploads cannot forge. Do not duplicate the
patch into job records, logs, or field text.

Later branch movement, repository edits, or unavailable commits must not alter
the retained diff. A range with no net file changes can be represented honestly
as such; requiring an artifact is not an implicit requirement to invent edits.
Missing commits, invalid ancestry, unavailable Git, non-Git workspaces, denied
access, or exceeded capture limits return structured errors, not empty success.

## Project Publication Policy

The project constitution decides what publication means. Setup must translate
the approved rules into explicit structured policy associated with the team's
workspace in canonical configuration. Runtime does not parse prose instructions
or infer a requirement from the presence of an `origin` remote.

The policy contains an explicit local-commit or remote-publication mode, any
accepted branch/ref restrictions, and, for remote mode, the approved configured
remote and accepted remote refs. Capture receives an allowed ref selection when
needed, never an arbitrary repository path or remote URL supplied by an agent.
Bind the approved remote's identity in policy; a workspace configuration edit
that retargets the same remote name must not silently change the verifier's
destination. Keep authentication material outside the policy and evidence.
If no applicable policy is configured, a Git-evidence request fails with an
actionable configuration error; ordinary workflows remain unaffected.

- Local mode verifies the committed range in the authorized repository and any
  configured local-ref restriction. It does not require a remote or perform a
  network check. Unpushed commits can satisfy the contract.
- Remote mode additionally verifies that the selected end commit is reachable
  from an accepted ref on the configured remote. A local tracking ref, agent
  assertion, or log of a push is insufficient. Record the observed remote ref,
  its object ID, the relationship checked, and the verification time. Do not
  retain credentials or credential-bearing URLs.

Remote verification is evidence of publication at the recorded time, not a
promise that a mutable remote ref will retain that commit forever. It is
read-only toward the remote and must not update the source workspace's files
or refs. Authentication or network failure cannot silently downgrade to local
mode. Use only explicitly authorized transports and credentials; no permission
widening or automatic local-network consent is part of this feature.

Policy changes participate in the ticket context/version fence. At transition
acceptance, validate the artifact's integrity, ticket/repository binding, and
matching policy receipt. A capture against a stale policy cannot satisfy the
current requirement. Accepted-operation replay returns its original receipt
without recapturing a moving range or reinterpreting later remote state.

Keep the responsibilities distinct: reusable workflow definitions declare the
kind of required result, and project configuration determines acceptable Git
publication. A workflow blueprint must work for both local-only and remotely
published projects without hard-coded remote names or branches.

## Git Capture and Error Boundaries

Use a narrow trusted capture operation behind the existing authenticated ticket
tool boundary. It resolves the execution repository from job authority, checks
ticket ownership and the requested output contract, validates the selected Git
range and publication policy, and returns an immutable artifact reference.
Read-only review agents may reference or inspect valid evidence through existing
ticket permissions; the new operation does not confer workspace write access.
Capture must also respect the actor's effective read permissions and workspace
boundary for selected source paths. Ticket-tool access is not permission to
extract otherwise unreadable repository content. Reject a disallowed range
rather than silently producing a partial diff.

Run Git with structured arguments, bounded work and output sizes, and external
diff helpers and text-conversion hooks disabled. Do not execute repository
hooks, interpret shell fragments, dereference repository symlinks as arbitrary
filesystem reads, or recursively inspect unapproved submodule repositories.
Represent submodule pointer changes as Git content, not permission to access
another repository. Apply existing artifact confinement and reparse defenses.

Preserve the existing transition atomicity: invalid, missing, wrong-ticket,
corrupt, or policy-incompatible evidence must not commit state or output values.
Git publication and ticket persistence are separate operations; do not claim a
transaction spanning a remote server and the local ticket store. Publishing or
capturing evidence can succeed while a stale ticket transition is rejected.
The agent then refreshes context and follows the existing retry contract.

## Stable Ticket Presentation

Classify output fields in the shared ticket detail projection, not by the
current state's outgoing transitions. The output field IDs are the union of:

- Output declarations across all transitions of the current workflow definition.
- Field IDs actually recorded in `effective_outputs` of accepted transition
  events, so definition edits do not hide previously produced results.

Use only structurally valid history for this classification. Do not promote
`effective_inputs` or report text into output fields or synthesize current
values from history. Read-only rendering must not mutate records.

Overview renders each output once, in stable field order, using its canonical
current `field_values` value. It remains an output after another transition,
closure, or a loop back to an earlier state. A result consumed by a later step
does not become a duplicate editable Input row. Non-output fields retain their
existing input controls; existing explicit field-update APIs remain available.

Retain the existing Inputs and Outputs sections and typed value rendering.
Show `Not submitted` for unset output values. Preserve meaningful `false` and
`0` values, text escaping, artifact links, and existing accessibility behavior.
Current field definitions supply labels and types. For a retained output whose
field has been removed from the current definition, use its latest accepted
output snapshot's field definition when valid, with a safe field-ID/plain-text
fallback. Never discard the stored value merely because its definition changed.

History shows each accepted transition's own outputs, actor, time, state
movement, and assessments. Labels and types there come from that event's
snapshot, not a later definition. Overview uses latest saved values; History
is not a substitute current-value store.

The board inspector, expanded ticket page, and detail snapshot must agree.
Existing refresh behavior must reveal committed values after refresh without
requiring an additional save. Do not redesign polling or unrelated editing
behavior in this change.

## Diff Viewer

A verified Git-change artifact in Overview or History exposes `View diff` and
`Download patch`. Both resolve the exact retained artifact, not today's branch
or working tree. The producing Job Detail page links to evidence associated
with that exact job; do not guess associations for historical changed-file
summaries. The existing filename/count summary is not relabeled as proof of
an agent's committed code changes.

Use a read-only page within the existing application shell. Show repository,
base/end revisions, publication mode and verification receipt, and a file list
with added/removed counts. Render an accessible unified line-by-line diff with
old/new line numbers and additions/deletions distinguishable without color
alone. Use a proven diff parser/renderer and escape all repository-controlled
content. Do not enable patch application, editing, or remote actions.

Provide file navigation and a return link to the originating ticket or job.
Long paths wrap in navigation; code panes scroll horizontally without widening
the page. Preserve keyboard access, focus indication, the existing light/dark
themes, and mobile usability. Binary files and submodule changes show explicit
metadata instead of fabricated text hunks. A verified empty diff shows a
distinct no-net-changes state.

Apply storage and preview limits. An oversized capture fails rather than
retaining a silently incomplete patch. A bounded preview must identify omitted
content while preserving access to the complete retained download. Missing,
corrupt, or inaccessible artifacts show an explicit error, never a regenerated
diff from the current workspace. Viewer access uses existing team/ticket
artifact confinement and never accepts a filesystem path from the browser.

## Maintained Workflow Examples

Apply the generic rules to the shipped definitions without special engine code:

| Blueprint | Transition | Required durable outputs | Input behavior |
| --- | --- | --- | --- |
| Research | Start exploration | None | Keep required research question |
| Research | Begin synthesis | Findings | Move Findings from input to output |
| Research | Close | Conclusion | Move Conclusion from input to output |
| Research | Reopen | None | Unchanged |
| Software delivery | Start | None | Unchanged |
| Software delivery | Submit for review | Notes | Replace optional input notes with required output notes |
| Software delivery | Approve | Review notes | Keep required `approved` input and its `equals true` precondition |
| Software delivery | Return to in progress | Review notes | Move Review notes from input to output |

Keep existing IDs, labels, state graphs, and criteria. The `approved` field
remains an evaluation gate, not an automatically persisted output. Update
documentation examples to demonstrate a real produced result and explain
required versus optional outputs accurately.

Add a generic Git-change artifact example to the workflow-definition guidance,
using the same output declaration and required flag available to any field.
Setup must make the artifact requirement and local/remote policy explicit for
projects that produce code changes. Do not make every Software delivery or
Research transition require Git: non-code work remains a supported use case.
Code-producing project definitions select the contract during authoring rather
than relying on workflow IDs or agent-role heuristics.

## Verification and Acceptance

The primary regression proof uses a custom workflow with arbitrary field and
transition IDs, not either shipped blueprint. It must cover:

1. Authoring required and optional outputs, saving the definition, and reloading
   the same contract without promoting inputs or losing required flags.
2. Rejection of missing or type-invalid required outputs, with unchanged state,
   saved fields, and provenance; success when valid outputs are supplied.
3. Atomic persistence and provenance through the real ticket service/provider,
   preserving stale-version rejection and idempotent retries.
4. Outputs visible in Overview and the detail snapshot immediately after a
   transition, after a later step, and in a terminal state with no outgoing
   transitions. Test both inspector and expanded page.
5. A repeated output replacing only its current value, with both accepted
   versions and their snapshot metadata retained in History.
6. An output reused as a later input without duplication, and a retained output
   remaining visible after its declaration is removed or renamed.
7. Unset values versus `false` and `0`, typed artifact rendering, escaped text,
   and safe handling of incomplete historical metadata.
8. Unchanged attempt-only input behavior and no implicit persistence from
   reports, logs, or earlier input-only history.
9. Both maintained examples declaring the intended required outputs, and
   setup/tool guidance teaching the same generic contract in packaged assets.
10. Authoring a custom Git-change artifact field, preserving its format and
  required flag through editor/save/reload, and rejecting an ordinary upload
  or external URL that attempts to satisfy verified Git evidence.
11. Capturing exact committed ranges from temporary Git repositories while
  excluding pre-existing dirty, staged, and untracked files. Verify retained
  bytes after subsequent branch movement and workspace changes, and prove
  that separate ticket submissions from one job are not conflated.
12. Local-only policy succeeding without a remote or network operation; remote
  policy requiring actual publication to an accepted ref. Exercise unpublished
  commits, disallowed refs, stale tracking refs, unreachable remotes, absent
  policy, and policy changes. Use isolated test remotes, not the user's origin.
13. Binding capture identity to trusted context, resisting forged metadata and
  wrong-ticket/repository references, and preserving state on corrupt,
  incomplete, oversized, denied, or otherwise invalid evidence.
14. Viewer and download links from current outputs, earlier History events, and
  the exact producing job. Cover text, renames, deletions, binary files,
  submodule pointers, empty diffs, escaped malicious content, long paths,
  bounded previews, and explicit artifact errors.

Reuse existing test suites and fixtures. Use focused tests while iterating,
including browser checks for editor round trips and output visibility across
states and diff-viewer navigation on desktop and mobile in light/dark themes.
Keep the established visual language. No mockup has been approved; any later
approved visual assets must be archived and referenced under the repository's
design-asset rules before implementation planning is finalized.

Before implementation, establish the repository-required clean full-suite
baseline in the feature worktree. Run the complete required suites after
implementation and review, and again on the integrated main branch according
to the repository workflow. No tests need to launch the user's live agents or
write to their configured ticket storage.

## Alternatives Rejected

- Persist every input: breaks intentional attempt-only evaluation semantics.
- Treat historical payloads as current results: leaves canonical fields empty
  and can contradict later explicit field edits.
- Rely only on prompt changes: cannot enforce required deliverables or fix
  output classification after a state change.
- Patch only Research and Delivery: leaves the generic workflow-definition
  process vulnerable to the same mistake in custom workflows.
- Require an output for every transition: invents results for administrative
  transitions and rejects otherwise valid workflows.
- Use dirty-workspace or whole-run counts as ticket evidence: cannot establish
  the selected committed result and can include unrelated work.
- Recompute historical diffs from moving refs: changes the evidence after the
  transition and fails when the repository is later unavailable.
- Require every project to push to a remote: contradicts local-only project
  constitutions. Conversely, accepting local commits unconditionally ignores
  projects that require verified remote publication.
- Accept an arbitrary uploaded patch as verified Git evidence: does not prove
  correspondence with actual commits or the applicable publication policy.
- Repair historical tickets: explicitly excluded by the user.

## Approval Boundary

This commit contains only the design specification. Implementation has not
started. After the user approves this written specification, create the
implementation plan in a separate documentation-only commit before changing
application code, setup guidance, or workflow assets.