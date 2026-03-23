# Data Formats

Agency is a read/write dashboard for managing AI agents. Agents write clues, curiosities, and logs to the `shared/` directory as markdown files with YAML frontmatter. Agency reads those files and presents them in the UI. When decisions are approved, Agency dispatches agents to execute them via their configured integration.

## Clue Format

```yaml
---
agent: researcher
date: 2025-01-15T10:30:00
category: data-quality
status: open
float: false
linked_clues: []
linked_curiosity: ~
ttl_days: 14
---

Found inconsistency in the source dataset — three entries have duplicate IDs
but different content. This may affect downstream analysis.
```

### Clue Fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent` | yes | Source agent name |
| `date` | yes | ISO 8601 datetime |
| `category` | no | Domain category for filtering |
| `status` | yes | `open`, `connected`, `dismissed`, `archived` |
| `float` | no | `true` promotes to "Floated Signals" in the inbox |
| `linked_clues` | no | List of related clue filenames |
| `linked_curiosity` | no | Filename of the curiosity this clue led to |
| `ttl_days` | no | Days before auto-archive (see TTL below) |

## Curiosity Format

```yaml
---
origin_agent: researcher
date: 2025-01-15
status: proposed
clues: [duplicate-ids-found.md, data-drift-detected.md]
feedback_requested: []
feedback_received: []
ttl_days: 30
---

Recommend implementing a deduplication pass before the analysis pipeline runs.
Two related clues suggest this is a systemic issue, not a one-off.
```

### Curiosity Fields

| Field | Required | Description |
|-------|----------|-------------|
| `origin_agent` | yes | Agent that proposed this |
| `date` | yes | ISO 8601 date |
| `status` | yes | `investigating`, `feedback`, `proposed`, `approved`, `deferred`, `rejected` |
| `clues` | no | List of source clue filenames |
| `feedback_requested` | no | Agents asked for input |
| `feedback_received` | no | Agents that responded |
| `ttl_days` | no | Days before auto-archive |

## Decision Format

Decisions are created through the UI when you approve, defer, or reject a curiosity:

```yaml
---
curiosity: deduplication-pass.md
decided_by: admin
date: 2025-01-16
decision: approved
execution:
  status: success
  agent: researcher
  started_at: 2025-01-16T14:00:00+00:00
  completed_at: 2025-01-16T14:03:22+00:00
  summary: Added deduplication pass to the pre-processing pipeline. 3 duplicate entries resolved.
---

Go ahead with the deduplication pass. Run it as a pre-processing step.
```

### Decision Fields

| Field | Required | Description |
|-------|----------|-------------|
| `curiosity` | yes | Linked curiosity filename |
| `decided_by` | yes | Who made the decision |
| `date` | yes | ISO 8601 date |
| `decision` | yes | `approved`, `deferred`, `rejected` |
| `execution` | no | Auto-added for approved decisions (see below) |

### Execution Block

When a curiosity is approved, Agency dispatches the origin agent to execute the proposed action using the agent's configured integration. The `execution` block tracks progress:

| Field | Description |
|-------|-------------|
| `status` | `pending` → `executing` → `success`, `success_with_exceptions`, or `failed` |
| `agent` | Agent that was dispatched |
| `started_at` | ISO 8601 timestamp when execution began |
| `completed_at` | ISO 8601 timestamp when execution finished |
| `summary` | Agent's report of what it did (or why it failed) |

Failed executions can be retried from the decision detail page. The integration used depends on the agent's configuration — Claude Code agents are run with `claude -p`, Codex with `codex exec`, etc.

## TTL Enforcement

Clues and curiosities with a `ttl_days` field are automatically archived when `date + ttl_days` passes. Items already in terminal states (`archived`, `dismissed`, `approved`, `rejected`, `deferred`) are not affected. TTL is checked on each page load.

## Pipeline Relationships

Agency tracks the full chain across the pipeline:

- A **clue** can link to a curiosity via `linked_curiosity`
- A **curiosity** links back to its source clues via `clues`
- A **decision** links to its curiosity via `curiosity`
- An approved **decision** triggers **execution**, dispatching the origin agent via its integration to carry out the proposed action

The UI renders these as clickable pipeline banners on each detail page, showing the full path from observation to action to execution.
