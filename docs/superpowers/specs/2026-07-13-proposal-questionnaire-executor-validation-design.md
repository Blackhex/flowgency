# Proposal Questionnaire and Executor Validation

**Date:** 2026-07-13
**Status:** Approved design

## Problem

Agency currently allows a proposal decision to dispatch the wrong agent and to
record incomplete answers:

- A proposal without `execution_agent` falls back to `origin_agent`, even when
  the origin agent is observational and read-only.
- The proposal form exposes an executor selector, but eligibility only checks
  whether the integration can execute. It does not model write authority.
- A malformed `choice` question without `options` renders no controls, yet the
  server accepts an empty answer and creates the decision.
- Boolean questions use Approve, Defer, and Reject even though Defer has no
  lifecycle for resuming the decision.
- Open-ended questions exist but do not have explicit requiredness semantics.
- There is no decision-level note for guidance that does not fit the structured
  questionnaire.
- Validation failures do not preserve all entered form values.

The immediate incident dispatched a decision to Sentinel because the proposal
declared `origin_agent: sentinel` and omitted `execution_agent`. The proposal
body said Builder should implement it and Sentinel was read-only, but Agency
does not infer routing or permissions from prose.

## Goals

1. Require proposals to nominate an executor explicitly.
2. Make write authority structured, explicit, and fail-closed.
3. Allow the human to override the nominated executor with another eligible
   agent while answering the proposal.
4. Validate proposal question definitions before rendering an actionable form.
5. Validate submitted answers before creating a decision or job.
6. Support required and optional open-ended questions.
7. Support one optional decision-level note.
8. Avoid dispatching work when every actionable boolean item is declined and no
   other substantive direction was supplied.
9. Preserve superseded decisions for display without permitting new malformed data.

## Non-Goals

- Inferring executor authority from agent identity prose or proposal content.
- Automatically assigning Builder when `execution_agent` is missing.
- Automatically migrating arbitrary user agent capabilities.
- Adding a Defer lifecycle, reminders, revisit dates, or partial decision state.
- Adding per-question comments in addition to the decision-level note.
- Rewriting proposal files while they are read.

## Approach

Add a focused proposal-decision validation module containing pure schema,
executor, answer, and execution-intent helpers. Both proposal rendering and
decision submission use these helpers. This keeps the policy independent of the
large route module and makes it reusable by the CLI or future APIs.

Validation is non-mutating. Invalid proposal files remain unchanged and produce
specific errors that identify the affected field or question.

## Agent Write Capability

Agent configuration gains a structured capability:

```yaml
agents:
  - name: advisor
    capabilities:
      write: false
  - name: builder
    capabilities:
      write: true
  - name: sentinel
    capabilities:
      write: false
```

Write authority is fail-closed. An omitted `capabilities` mapping or omitted
`capabilities.write` value means `false`.

An agent is eligible to implement a decision only when all of these conditions
hold:

1. The agent exists in the normalized group configuration.
2. Its directory is available.
3. Its integration supports execution.
4. It explicitly declares `capabilities.write: true`.

The current project configuration and shipped examples will be updated for
known roles. In the current Agents group, only Builder is writable; Advisor and
Sentinel are read-only. Agency will not infer or persist capabilities for other
user configurations.

This capability controls decision implementation eligibility. It does not stop
read-only agents from running observational scheduled prompts.

## Proposal Schema

Every actionable proposal must contain a non-empty `execution_agent`. There is
no fallback to `origin_agent` when creating a new decision.

Each question must contain:

- A non-empty, unique `id`.
- A non-empty `prompt`.
- A supported `type`: `boolean`, `choice`, `free-response`, or superseded `text`.

Additional rules by type:

- `boolean` has no options and accepts `approved` or `declined`.
- `choice` requires a non-empty `options` list. Each option may be a string or a
  mapping with a non-empty `label`. Option labels must be unique within the
  question. `multi: true` enables multiple selections.
- `free-response` and superseded `text` render as textareas. `required` defaults to
  `true`; proposal authors may set `required: false`.

Schema validation runs before an unanswered proposal becomes actionable. A
blocking error replaces or disables submission when the schema is invalid.

## Questionnaire UI

The unanswered proposal page renders:

1. Blocking proposal errors, when present.
2. Structured questions.
3. An optional `Decision note` textarea for caveats or alternate guidance that
   does not fit a structured answer.
4. A visible `Implement with` selector, preselected from the proposal's required
   `execution_agent` and containing only eligible writable agents.
5. The submit action.

Boolean questions use required **Approve** and **Decline** controls. New
submissions cannot produce `deferred` values.

Choice questions render radio controls by default and checkboxes when
`multi: true`. Open-ended questions use textareas and honor their `required`
flag.

When submission validation fails, the page re-renders with the selected
executor, every entered answer, and the decision note preserved.

## Submission Validation

The POST route repeats all schema and executor checks. Browser controls are not
trusted as an enforcement boundary.

Submitted answers are valid only when:

- Every boolean answer is exactly `approved` or `declined`.
- Every required open-ended answer contains non-whitespace text.
- Optional open-ended answers may be empty.
- Every single-choice answer matches one declared option.
- Every multi-choice answer is a list containing only declared options.
- Required choice questions contain at least one selection. Choice requiredness
  defaults to `true` and may be disabled with `required: false`.

Unknown answer fields do not become decision answers. Validation errors prevent
all side effects: no decision file, proposal status update, job record, or
detached process is created.

## Decision Data

A successful decision stores structured answers, the optional note, and the
selected executor separately:

```yaml
answers:
  rebuild_monitor_venv: approved
  version_keyed_venv: declined
  migration_detail: Keep the existing cache for rollback.
decision_note: Apply this only to the monitor environment.
execution_agent: builder
```

The immutable decision job prompt includes the proposal body, structured
answers, and decision note. The note is guidance to the executor but does not
alter answer validation.

## Execution Decision

After validation, Agency determines whether the decision contains work to
execute.

| Questionnaire result | Execute? |
| --- | --- |
| At least one boolean is approved | Yes |
| All booleans are declined, but a choice answer is selected | Yes |
| All booleans are declined, but an open-ended answer contains text | Yes |
| All booleans are declined, but the decision note contains text | Yes |
| Boolean questions exist, all are declined, and no other substantive input exists | No |
| No boolean questions exist and the validated questionnaire is submitted | Yes |

For this rule, substantive input means a non-empty choice selection, a
non-whitespace open-ended answer, or a non-whitespace decision note. Optional
empty answers do not count.

When execution is skipped, Agency still creates the decision and marks the
proposal decided. The decision uses `execution_status: skipped`, has no job ID,
and records an explanatory execution summary. No executor process is launched.

## Error Handling and Display

Proposal errors are specific and actionable, including:

- Missing `execution_agent`.
- Unknown, unavailable, non-executable, or read-only executor.
- Duplicate or missing question IDs.
- Unsupported question type.
- Missing question prompt.
- Missing, empty, or duplicate choice options.
- Missing required answer or answer outside the declared values.

Decision and proposal detail pages display superseded empty answers as
`No answer recorded`, never as an empty success badge. Existing `deferred`
answers remain readable with their historical label.

Decision retries continue to allow executor selection, but the selected agent
must satisfy the same explicit write-capability rule. A skipped decision has no
failed job to retry; further work should originate from a new or revised
proposal.

## Compatibility and Migration

- Existing string-shorthand agent entries normalize without write authority and
  are therefore ineligible for decision implementation until explicitly
  migrated.
- Current and shipped example configurations will mark known implementation
  agents with `capabilities.write: true` and known observational/advisory agents
  with `false`.
- superseded `text` questions remain aliases of `free-response`.
- Existing decisions with blank or `deferred` answers remain displayable.
- New proposal decisions require explicit valid metadata and complete answers.
- Scheduled and manually launched prompts retain their current behavior; write
  capability is scoped to decision implementation eligibility.

## Testing

Focused tests will cover:

1. Proposal schema validation for executor metadata and every question type.
2. Rejection of missing, duplicate, malformed, and optionless questions.
3. Fail-closed capability defaults.
4. Executor filtering and POST rejection for read-only agents.
5. Visible executor override and preservation after validation errors.
6. Required Approve/Decline boolean answers and rejection of `deferred`.
7. Required and optional open-ended answers, including superseded `text`.
8. Single- and multi-choice answer validation.
9. Decision-note persistence, display, and prompt inclusion.
10. Preservation of entered answers, note, and executor on POST errors.
11. Skipped execution when all booleans are declined without other guidance.
12. Execution when choices, open-ended answers, or a note provide guidance even
    when all booleans are declined.
13. Execution for validated questionnaires without boolean questions.
14. superseded blank and deferred answer display.
15. Retry executor capability validation.
16. No decision, proposal mutation, or job submission after validation failure.

## Documentation

Update the data-format and configuration documentation to describe:

- Required proposal `execution_agent` metadata.
- Agent `capabilities.write` and its fail-closed default.
- Supported question types and requiredness.
- Approve/Decline boolean semantics.
- Decision notes.
- Executor override and execution-skipping behavior.
