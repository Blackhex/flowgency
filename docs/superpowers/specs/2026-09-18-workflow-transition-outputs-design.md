# Generic Workflow Transition Outputs

Date: 2026-09-18

Status: Design direction approved; written specification awaiting user review.

## Goal

Make durable results an explicit part of the generic workflow-definition
process. A result-producing transition must not succeed without its required
outputs, and committed outputs must remain visible on the ticket after later
transitions, including terminal states.

This is a contract for every workflow, not special handling for Research,
Delivery, particular agents, or fields named `findings` and `conclusion`.
Overview shows the latest saved value of each output. History preserves the
outputs of earlier accepted transitions and their original context.

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

Relevant implementation anchors:

- [Transition definitions](../../../flowgency/workflows/models.py)
- [Transition validation](../../../flowgency/workflows/rules.py)
- [Ticket mutations](../../../flowgency/tickets/service.py)
- [Ticket view projection](../../../flowgency/tickets/views.py)
- [Ticket inspector](../../../flowgency/templates/_ticket_inspector.html)
- [Workflow editor](../../../flowgency/static/workflow-editor.js)
- [Setup workflow guidance](../../../.github/skills/flowgency-setup/references/ticket-workflow-steps.md)

## Scope and Boundaries

In scope are the generic workflow authoring process, setup and agent guidance,
tool descriptions, maintained workflow examples and packaged assets, ticket
projection and rendering, documentation, and focused regression coverage.

There are no ticket backfills, history rewrites, automatic input promotion,
startup conversions, historical job replays, agent launches, or permission
changes. Do not infer results from reports, logs, semantic memory, state names,
field names, or agent roles.

No schema version change or new workflow-specific API is needed. Do not add a
second result store or a separate metadata flag duplicating `outputs`.

Canonical configured workflow libraries remain authoritative. Updating shipped
examples does not silently replace separately installed definitions. The
authoring contract applies when definitions are created or deliberately edited
through the normal definition process. Existing ticket data remains untouched;
historical input-only values may consequently remain unset in Overview.

## Generic Definition Process

For every transition, workflow authors and setup agents must explicitly decide:

1. Which values the transition consumes or checks: declare them in `inputs`.
2. Which durable results the transition produces: declare them in `outputs`.
3. Which outputs are necessary for success: mark each of those as required.
4. Whether the transition intentionally produces no results: use `outputs: []`.

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
No new wizard, confirmation modal, or layout redesign is needed.

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

Reuse existing test suites and fixtures. Use focused tests while iterating,
including browser checks for editor round trips and output visibility across
states on desktop and mobile. Keep the established visual language; no new
visual design or mockup is part of this specification.

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
- Repair historical tickets: explicitly excluded by the user.

## Approval Boundary

This commit contains only the design specification. Implementation has not
started. After the user approves this written specification, create the
implementation plan in a separate documentation-only commit before changing
application code, setup guidance, or workflow assets.