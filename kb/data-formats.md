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
questions:
  - id: approach
    type: choice
    prompt: "Which deduplication strategy?"
    options:
      - label: "Pre-processing pass"
      - label: "Real-time dedup at ingest"
    multi: false
  - id: approve
    type: boolean
    prompt: "Proceed with implementing this?"
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

### Question Types

Each question has an `id`, `type`, and `prompt`. The three types:

| Type | Extra Fields | Answer Format |
|------|-------------|---------------|
| `boolean` | — | `approved`, `deferred`, or `rejected` |
| `choice` | `options` (list of `{label}`), `multi` (bool) | Selected label string, or list if multi |
| `free-response` | — | Free text string |

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
---
```

### Decision Fields

| Field | Required | Description |
|-------|----------|-------------|
| `proposal` | yes | Linked proposal filename |
| `decided_by` | yes | Who made the decision |
| `date` | yes | ISO 8601 date |
| `answers` | yes | Dict of question id → answer value |
| `execution_status` | no | `pending`, `running`, `complete`, `failed` |
| `execution_summary` | no | Agent's report of what it did |

### Execution

Every decision triggers a dispatch to the origin agent, regardless of the answers. The agent reads the decision file and acts on the answers — implementing approved actions, closing out rejected ones, or using choice/free-response answers to guide its work. Failed executions can be retried from the decision detail page.

## TTL Enforcement

Observations and proposals with a `ttl_days` field are automatically archived when `date + ttl_days` passes. Items already in terminal states (`archived`, `dismissed`, `decided`) are not affected. TTL is checked on each page load.

## Pipeline Relationships

Agency tracks the full chain across the pipeline:

- An **observation** can link to a proposal via `linked_proposal`
- A **proposal** links back to its source observations via `observations`
- A **decision** links to its proposal via `proposal`
- Every **decision** triggers **execution**, dispatching the origin agent via its integration to act on the answers

The UI renders these as clickable pipeline banners on each detail page, showing the full path from observation to action to execution.
