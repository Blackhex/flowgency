# Data Formats

Agency is a read/write dashboard for managing AI agents. Agents write observations, proposals, and logs to the `shared/` directory as markdown files with YAML frontmatter. Agency reads those files and presents them in the UI. When decisions are made, Agency dispatches agents to act on the answers via their configured integration.

## Observation Format

```yaml
---
agent: researcher
date: 2025-01-15T10:30:00
category: data-quality
status: open
float: false
linked_observations: []
linked_proposal: ~
ttl_days: 14
---

Found inconsistency in the source dataset — three entries have duplicate IDs
but different content. This may affect downstream analysis.
```

### Observation Fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent` | yes | Source agent name |
| `date` | yes | ISO 8601 datetime |
| `category` | no | Domain category for filtering |
| `status` | yes | `open`, `connected`, `dismissed`, `archived` |
| `float` | no | `true` promotes to "Floated Signals" in the inbox |
| `linked_observations` | no | List of related observation filenames |
| `linked_proposal` | no | Filename of the proposal this observation led to |
| `ttl_days` | no | Days before auto-archive (see TTL below) |

## Proposal Format

```yaml
---
origin_agent: researcher
date: 2025-01-15
status: proposed
observations: [duplicate-ids-found.md, data-drift-detected.md]
feedback_requested: []
feedback_received: []
ttl_days: 30
execution_agent: builder
questions:
  - id: approach
    type: choice
    prompt: "Which deduplication strategy?"
    options:
      - label: "Pre-processing pass"
      - label: "Real-time dedup at ingest"
    multi: false
    required: true
  - id: approve
    type: boolean
    prompt: "Proceed with implementing this?"
    required: true
---

Recommend implementing a deduplication pass before the analysis pipeline runs.
Two related observations suggest this is a systemic issue, not a one-off.
```

### Proposal Fields

| Field | Required | Description |
|-------|----------|-------------|
| `origin_agent` | yes | Agent that proposed this |
| `date` | yes | ISO 8601 date |
| `status` | yes | `investigating`, `feedback`, `proposed`, `decided`, `archived` |
| `observations` | no | List of source observation filenames |
| `feedback_requested` | no | Agents asked for input |
| `feedback_received` | no | Agents that responded |
| `ttl_days` | no | Days before auto-archive |
| `questions` | yes | List of typed questions (see below) |
| `execution_agent` | yes | Agent that should implement decisions on this proposal. Must be an agent with `capabilities.write: true`. Omitting this field, or naming an agent that is unavailable, non-executable, or lacks write permission, blocks the decide form and POST until corrected. |

### Question Types

Each question has an `id`, `type`, and `prompt`. The three types:

| Type | Extra Fields | Answer Format | Notes |
|------|-------------|---------------|-------|
| `boolean` | `required` (bool, default true) | `approved` or `declined` | `deferred` and `rejected` are not valid answer values |
| `choice` | `options` (list of `{label}` or bare strings), `multi` (bool), `required` (bool, default true) | Selected label string, or list if multi | `options` is mandatory |
| `free-response` or `text` | `required` (bool, default true) | Free text string | Both type names are accepted |

## Decision Format

Decisions are created when you answer a proposal's questions:

```yaml
---
proposal: deduplication-pass.md
decided_by: admin
date: 2025-01-16
answers:
  approach: "Pre-processing pass"
  approve: approved
execution_status: complete
execution_summary: Added deduplication pass to the pre-processing pipeline. 3 duplicate entries resolved.
execution_agent: builder
decision_note: Prioritise the pre-processing approach for simplicity.
---
```

### Decision Fields

| Field | Required | Description |
|-------|----------|-------------|
| `proposal` | yes | Linked proposal filename |
| `decided_by` | yes | Who made the decision |
| `date` | yes | ISO 8601 date |
| `answers` | yes | Dict of question id → answer value |
| `execution_status` | no | `pending`, `running`, `complete`, `failed`, `skipped` |
| `execution_summary` | no | Agent's report of what it did |
| `execution_agent` | no | Agent selected to implement this decision. Must have `capabilities.write: true`. Set when the decision is created or retried; no origin-agent fallback. |
| `execution_job_id` | no | ID of the current (or most recent) durable job submitted for this decision |
| `execution_job_history` | no | IDs of prior jobs superseded by retries, oldest first |
| `decision_note` | no | Free-text context or guidance for the executing agent |

### Execution Intent and `execution_status: skipped`

Agency validates the executor and evaluates execution intent before creating a decision:

| Condition | Result |
|-----------|--------|
| `execution_agent` is missing, invalid, non-executable, or non-writable | Decide form and POST are blocked until corrected — no decision is created |
| Questionnaire has no `boolean` questions | `pending` (job submitted after validation) |
| At least one `boolean` answer is `approved` | `pending` (job submitted) |
| All `boolean` answers are `declined` AND no substantive non-boolean input (non-whitespace choice selection, open-ended answer, or decision note) | `skipped` (no job submitted) |
| All `boolean` answers are `declined` AND at least one substantive non-boolean input is present | `pending` (job submitted) |

`skipped` is a terminal status — no job is submitted and no retry is offered unless the
decision is re-opened. The executor dropdown on the decide form lists only agents with
`capabilities.write: true`; agents without this flag do not appear.

### Execution

When you answer a proposal's questions, you select which agent implements the decision
from the executor dropdown (only agents with `capabilities.write: true` are listed).
Agency validates the executor and evaluates execution intent before creating the
decision — see the table above. When a decision executes, Agency submits a durable job
for the executor with an immutable snapshot of the proposal body and your answers
embedded in the prompt — the agent never needs to re-read the proposal or decision
files. Failed executions can be retried from the decision detail page; retrying keeps
the prior `execution_job_id` in `execution_job_history` and lets you change the
executing agent.

## TTL Enforcement

Observations and proposals with a `ttl_days` field are automatically archived when `date + ttl_days` passes. Items already in terminal states (`archived`, `dismissed`, `decided`) are not affected. TTL is checked on each page load.

## Pipeline Relationships

Agency tracks the full chain across the pipeline:

- An **observation** can link to a proposal via `linked_proposal`
- A **proposal** links back to its source observations via `observations`
- A **decision** links to its proposal via `proposal`
- A missing, invalid, or non-writable `execution_agent` blocks the decide form and POST until corrected; execution is `skipped` only when all `boolean` answers are `declined` with no substantive non-boolean input (choice selection, open-ended answer, or decision note)

The UI renders these as clickable pipeline banners on each detail page, showing the full path from observation to action to execution.
