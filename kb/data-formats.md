# Data Formats

Flowgency tracks work as tickets moving through configurable ticket workflows. A reusable blueprint defines the states, fields, and transitions; a team attaches named workflow instances that bind a blueprint to a Local ticket storage root. Agents advance tickets through the live ticket tools, not by writing Markdown records into a workspace. `workspace_path` is the execution workspace and source repository; `path` is the Flowgency-owned team root, which holds `locks/` and `logs/` and is automatically available to restricted agents. Flowgency never loads or creates `<workspace_path>/shared`. Durable jobs live in `flowgency.memory_store/.jobs`, and operation locks live in `<team.path>/locks`. Ticket records live under the workflow instance's Local storage root, keyed by the workflow binding.

## Workflow Blueprint Format

A blueprint is one validated `workflow.yaml` under `flowgency.workflow_library/<blueprint-id>/`. It deserializes into an immutable `WorkflowDefinition` — the states, a top-level `fields` catalog, and transitions that reference the catalog. The current source is validated on every read; there is no persisted last-good definition and no native-file conversion. One invalid blueprint never erases the rest of the listing, and an invalid definition blocks only the transitions it affects.

```yaml
schema_version: 1
id: software-delivery
name: Software delivery
description: End-to-end delivery for verified software work
initial_state: backlog
states:
  - id: backlog
    name: Backlog
    color: '#a9b0bd'
  - id: in-progress
    name: In progress
    color: '#7ab7d7'
  - id: review
    name: Review
    color: '#ebc77c'
  - id: done
    name: Done
    color: '#7ad7bf'
fields:
  - id: review-notes
    label: Review notes
    type: text
  - id: approved
    label: Approved
    type: boolean
transitions:
  - id: approve
    name: Approve
    from_state: review
    to_state: done
    inputs:
      - field_id: approved
        required: true
      - field_id: review-notes
        required: true
    outputs: []
    preconditions:
      - field_id: approved
        operator: equals
        value: true
    criteria:
      - id: work-complete
        description: Deliverable is complete and ready for review
```

### Definition Fields

| Field | Required | Description |
|-------|----------|-------------|
| `schema_version` | yes | Must be `1` |
| `id` | yes | Stable blueprint slug; must equal the directory name |
| `name` | yes | Display label; label equality alone does not establish identity |
| `description` | yes | Human-readable summary |
| `initial_state` | yes | State id a new ticket starts in; must be a declared state |
| `states` | yes | Declared states (see below) |
| `fields` | yes | The field catalog owning all field ids, labels, and types |
| `transitions` | yes | Declared transitions (see below) |

Identifiers (`id`) are stable technical keys; labels (`name`, `label`) are mutable display text. IDs are generated for user-created definitions rather than typed by hand; changing a label never changes identity. Shipped reusable blueprint IDs are stable and can be referenced by configured instances.

### State Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable state id |
| `name` | yes | Display name |
| `color` | yes | `#rrggbb` hex colour |

### Field Catalog

Each entry declares an `id`, a `label`, and a `type`. The types are `text`, `number`, `boolean`, and `artifact`. Transitions reference catalog entries by id through a `FieldUse` (`field_id`, `required`) rather than duplicating field definitions, so a field's label or type is defined in exactly one place.

### Transition Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable transition id |
| `name` | yes | Display name |
| `from_state` | yes | Source state id |
| `to_state` | yes | Destination state id |
| `inputs` | yes | `FieldUse` list an agent may or must supply |
| `outputs` | yes | `FieldUse` list the transition records |
| `preconditions` | yes | Field checks that must hold before the transition |
| `criteria` | yes | Agent criteria requiring a qualitative assessment |

A `Precondition` names an input `field_id`, an `operator` (`equals`, `not_equals`, or `is_present`), and a comparison `value` for the equality operators. Its `field_id` must be declared as an input of the same transition. An `AgentCriterion` has an `id` and a `description`; there is no separate `evidence_required` flag. When a transition carries criteria, the agent must submit a `CriterionAssessment` (`criterion_id`, `satisfied`, non-blank `reasoning`, and `supporting_fields`) for each one. Required inputs and each criterion assessment are enforced; only an accepted transition changes ticket state.

## Artifact References

An `artifact` field holds an immutable `ArtifactRef`, either `{kind: id, value: ...}` (no path separators) or `{kind: url, value: https://...}` (HTTPS host, no embedded credentials). Recorded artifacts are immutable.


## Ticket Records

A `TicketRecord` is the operational record for one ticket. It carries no blueprint pin: its `state_id` and `field_values` are interpreted against the current board context, so an external edit to the blueprint is reflected the next time the ticket is read. A record also tracks its `title`, `assignee`, and monotonically increasing `revision`.

There is one ownership concept: assignment. Assigning a ticket sets its `assignee` to an instance's stable `name`; assignment is persistent ownership, not an expiring lease, and there is no second per-ticket claim. Only agents move tickets between states. Sign-off is optional and is not a completion rule. One agent run may work several tickets, and taking another ticket does not create a new durable job.

Every agent mutation carries a `TicketVersion` (`ref`, `revision`, `workflow_digest`, `context_digest`) so a stale write is rejected. A `TicketRef` (`binding_id`, `team_id`, `workflow_id`, `ticket_id`) locates a ticket within its binding. A `TicketOperation` carries a unique `operation_id` and a canonical request digest; a committed operation persists a receipt, so a replay returns the original `TicketMutationResult` (`replayed: true`) instead of applying twice. The ticket operation is atomic; the external project it describes is not.

## Workflow Bindings and Storage

A `WorkflowBinding` is the configured selection for one team workflow: `team_id`, `workflow_id`, `blueprint_id`, a `StorageBinding`, and a `context_digest` over the relevant configuration. Display-name edits are excluded from that digest. A `StorageBinding` names the storage `integration`, its canonical validated `config`, the `team_id`, the `workflow_id`, and a computed `binding_id`.

Flowgency ships the Local storage provider only. A Local instance names a filesystem `root`:

```yaml
workflows:
  delivery:
    name: Delivery
    blueprint: software-delivery
    integration: local
    integration_config:
      root: C:/Flowgency/tickets
```

An unavailable or unreadable storage root is not an empty ticket set — it is surfaced as an issue rather than silently showing zero tickets. Switching a workflow's storage does not transfer, rewrite, or delete existing tickets or history in either root, and the prior binding's data is cleaned up on its own terms; no existing ticket or history is transferred.

## Durable Jobs

`flowgency ticket run` submits a durable job for an assigned ticket. The job is the unit that runs an agent against the ticket; it drains through the shared job pool alongside routine jobs. Assignment and transitions are recorded on the ticket itself, not on the job.

## CLI

Inspect and mutate tickets from the CLI:

```text
flowgency workflows --team <team>
flowgency tickets --team <team> --workflow <workflow> [--state <id>] [--assignee <name>]
flowgency ticket show <ticket-id> --workflow <workflow> --team <team>
flowgency ticket create --workflow <workflow> --title <title> [--description <text>]
flowgency ticket assign <ticket-id> --workflow <workflow> --agent <name>
flowgency ticket unassign <ticket-id> --workflow <workflow>
flowgency ticket run <ticket-id> --workflow <workflow>
```

`workflows` and `tickets` are read-only board views. `ticket run` queues a durable job for an assigned ticket. Read commands do not change config, cache, memory, or ticket storage.

## Lifecycle

Flowgency records each ticket's path through its workflow: creation, assignment changes, and every accepted transition with the field inputs and criterion assessments supplied. Label equality alone does not establish identity — the stable `id` does. Because a `TicketRecord` holds no blueprint pin, the board renders each ticket against the current definition; a missing or invalid transition is surfaced as an issue on the board rather than silently dropped, and only the affected transitions are blocked.

