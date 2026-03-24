# Proposal Questions — Design Spec

> **Date:** 2026-03-24
> **Status:** Draft
> **Author:** Product Agent

## Problem

Proposals currently support only a single interaction: approve, defer, or reject. This forces agents into a narrow pattern — write a recommendation, hope the human agrees. There's no way to ask structured questions ("which color?"), request open-ended input ("describe the image you want"), or batch multiple independent questions into one proposal. Agents that need human input must create separate proposals for each question, waiting hours between dispatch cycles.

## Solution

Add a **questions** layer inside proposals. Each proposal contains a list of typed questions in its YAML frontmatter. The human answers all questions at once, creating a decision file with structured answers. This replaces the old approve/defer/reject model entirely.

## Data Model

### Proposal Frontmatter

```yaml
origin_agent: product
date: 2026-03-24
status: proposed           # investigating | feedback | proposed | decided | archived
observations:
  - obs1.md
feedback_requested: []
feedback_received: []
ttl_days: 14
questions:
  - id: direction
    type: choice
    prompt: "Which design direction should we go?"
    options:
      - label: "Minimal — clean, whitespace-heavy"
      - label: "Dense — information-rich dashboard"
      - label: "Hybrid — cards with expandable detail"
    multi: false
  - id: hero_image
    type: free-response
    prompt: "Describe the hero image you'd like for the landing page"
  - id: proceed
    type: boolean
    prompt: "Should we proceed with implementing this redesign?"
```

### Question Types

| Type | Fields | Answer Format |
|------|--------|---------------|
| `boolean` | `id`, `type`, `prompt` | `approved` / `deferred` / `rejected` |
| `choice` | `id`, `type`, `prompt`, `options` (list of `{label}`), `multi` (bool, default false) | Selected label string, or list of strings if `multi: true` |
| `free-response` | `id`, `type`, `prompt` | Free text string |

### Question Field Rules

- **`id`** — short, descriptive, snake_case. Used as key in decision answers.
- **`prompt`** — the question text shown to the human. Can include hints and context.
- **`options`** — only for `choice` type. Each option has a `label` string.
- **`multi`** — only for `choice` type. Defaults to `false`. When `true`, human can select multiple options.
- Every proposal must have at least one question.

### Decision Frontmatter

```yaml
proposal: 2026-03-24-redesign.md
decided_by: admin
date: 2026-03-24
answers:
  direction: "Dense — information-rich dashboard"
  hero_image: "A mountain landscape at sunset with warm orange and purple tones"
  proceed: approved
```

- **`answers`** — dict keyed by question `id`.
- Boolean answers: `approved`, `deferred`, or `rejected`.
- Choice answers: the selected label string, or list of label strings if multi-select.
- Free-response answers: the text string.
- The old `decision` field is removed.
- The old `execution` block format is removed.

### Status Changes

- **`decided`** replaces `approved`, `deferred`, and `rejected` as the terminal proposal status.
- `decided` gets a **green** badge (means "resolved, no more human action needed").
- Valid proposal statuses: `investigating`, `feedback`, `proposed`, `decided`, `archived`.

## UI Design

### Proposal Detail — Question Form (Unanswered)

Questions render as **stacked cards** below the proposal body. Each card contains:

- The question prompt
- The appropriate input control:
  - **boolean** — three buttons: Approve (green), Defer (yellow), Reject (red)
  - **choice (single)** — radio buttons, one per option, styled as selectable rows with borders
  - **choice (multi)** — checkboxes, same row styling
  - **free-response** — textarea
- A single **"Submit All Answers"** button at the bottom of all cards

The form POSTs to `/{group}/proposals/{slug}/decide`. Form fields are keyed as `answer_{question_id}` (e.g., `answer_direction`, `answer_proceed`). Multi-select checkboxes send multiple values for the same key.

### Proposal Detail — Decided State

Same card layout as the form, but read-only. Input controls are replaced with the selected answer highlighted in a green-bordered box. The decided status badge and date appear above the answers section.

### Dashboard Attention Queue

Proposals show a **"N questions"** pill badge (purple). No inline answering — always click through to the proposal detail page. This applies uniformly regardless of question count or type.

### Proposals List Page

No major changes. The status badge shows `decided` (green) instead of `approved`/`deferred`/`rejected`. The card preview continues to show agent badge, title, body preview, and date.

## Route & Handler Changes

### `proposal_detail()` — GET `/{group}/proposals/{slug}`

- Parse `questions` from frontmatter.
- If a decision file exists, load its `answers` dict and merge into the template context.
- Template renders the question form (if undecided) or the read-only answered cards (if decided).

### `proposal_decide()` — POST `/{group}/proposals/{slug}/decide`

- Read the proposal's `questions` list from frontmatter.
- For each question, read the answer from form data (`answer_{id}`).
- Validate that every question has an answer.
- Build the `answers` dict.
- Create the decision file with `answers` in frontmatter.
- Update the proposal status to `decided`.
- Trigger execution: dispatch the origin agent to read the decision file and act on the answers.
- Redirect to `/{group}/decisions/{slug}`.

### Decision Detail Page

- Display the answers from the decision frontmatter, rendered as read-only cards matching the proposal's questions.
- The decision detail page needs access to the proposal's `questions` list to render prompts and option labels alongside the answers.

## Pipeline Changes

### `build_pipeline_stats()`

- Terminal proposal states change from `approved`/`deferred`/`rejected` to `decided`.
- Pipeline stage counts updated accordingly.

### `status_badge()`

- Add `decided` with green color treatment.
- Remove `approved`, `deferred`, `rejected` badge colors (no longer used on proposals — they only appear as boolean answer values now).

### `build_activity_feed()`

- No structural changes. Decided proposals appear in the feed with the `decided` status.

### Execution

- Every decision triggers a dispatch to the origin agent.
- The agent is instructed to read the decision file and act on the answers.
- This applies regardless of what the answers contain — including fully rejected proposals, where the agent should close the loop gracefully.
- This replaces the old model where only `approved` decisions triggered execution.

## Agent Instructions

### `_observation-system-steps.md` Updates

**Step 5 (Promote to Proposal)** — Updated frontmatter template:

```yaml
---
origin_agent: {agent-name}
date: {YYYY-MM-DD}
status: investigating
observations:
  - {observation-file.md}
feedback_requested: []
feedback_received: []
ttl_days: 14
questions:
  - id: {short_snake_case_id}
    type: {boolean|choice|free-response}
    prompt: "{question text}"
    # For choice type, add:
    # options:
    #   - label: "Option A"
    #   - label: "Option B"
    # multi: false
---
```

**Step 6 (Finalize Proposals)** — Changed from "write the Recommendation section" to "ensure questions are well-formed and set status to proposed."

**Agent guidance on question types:**

- Every proposal must have at least one question.
- Use `boolean` for go/no-go decisions.
- Use `choice` when presenting discrete options already identified by the agent.
- Use `free-response` when the agent needs open-ended human input.
- Batch independent questions into one proposal rather than creating separate proposals.
- Question `id` should be short, descriptive, snake_case.
- The proposal body (Evidence, Investigation, Agent Feedback) provides context for the questions.

## Migration

One-time migration of existing files (no backward compatibility layer):

1. **Existing proposals** — Add `questions` field with a single boolean question (`id: approve`, prompt derived from the Recommendation section or generic "Approve this proposal?"). Change terminal statuses (`approved`/`deferred`/`rejected`) to `decided`.
2. **Existing decisions** — Convert `decision: approved` to `answers: { approve: approved }`. Remove the old `decision` field and `execution` block.
3. **`_observation-system-steps.md`** — Replace the proposal template and finalization instructions.

## Out of Scope

- Conversational threads / multi-round back-and-forth within a single proposal (agents create new proposals for follow-ups).
- Conditional questions (showing question B only if question A is answered a certain way).
- Dashboard inline answering (always click through).
- Backward compatibility with old proposal format.
