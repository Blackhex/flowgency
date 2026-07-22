# Group Storage Redesign

**Date:** 2026-07-22
**Status:** Approved (design)

## Problem

Agency currently gives `groups.<id>.path` two unrelated responsibilities:

1. the source project and agent execution workspace;
2. the parent of Agency-owned pipeline records under `shared/`.

This coupling causes Agency to create `shared/jobs` and `shared/logs` inside source
repositories. It also makes the ownership boundary unclear: global blueprint,
compiled, memory, and durable-job data live outside the project, while group
records and operational state live inside it.

The `shared/jobs` name is additionally misleading. Authoritative durable jobs live
under `agency.memory_store/.jobs`; the project-local directory contains an
operation lock rather than job records.

## Goals

1. Separate the execution workspace from Agency-owned group state.
2. Keep all Agency-generated group records outside source repositories.
3. Give every group an explicit, mandatory state root.
4. Remove the `shared` path segment and all implicit project-local record paths.
5. Centralize group path derivation so consumers do not construct paths ad hoc.
6. Preserve direct agent access to group pipeline records.
7. Keep blueprint source, compiled projections, semantic memory, and authoritative
   jobs in their existing distinct global stores.
8. Fail closed on invalid, overlapping, or unsafe paths.

## Non-Goals

- Supporting previous configuration shapes.
- Reading records from both old and new locations.
- Automatically or explicitly migrating old configuration or `shared` data.
- Deleting old project-local `shared` directories.
- Combining semantic memory with observations, proposals, or decisions.
- Moving authoritative durable jobs out of `agency.memory_store/.jobs`.
- Redesigning pipeline document formats or integration behavior.

## Canonical Configuration

The redesigned canonical schema is identified by `schema_version: 3`.

```yaml
schema_version: 3
agency:
  title: Agency
  default_group: atreides
  ai_backend: copilot
  agent_library: C:/Projekty/Agents/agent-library
  compilation_cache: C:/Projekty/Agents/compiled-agents
  memory_store: C:/Projekty/Agents/memory
groups:
  atreides:
    name: House of Atreides
    workspace_path: C:/Projekty/christag-agency
    path: C:/Projekty/Agents/groups/atreides
    default_integration: copilot
    runtime:
      timeout: 1800
      sandbox:
        mode: restricted
        roots: []
      tools:
        mode: allowlist
        names: [read, search]
    dispatch:
      enabled: true
      daily_limit: 20
    agents: []
```

Group path fields have one responsibility each:

- `workspace_path` is the source project and execution workspace.
- `path` is the Agency-owned group-state root.

Both fields are mandatory. There is no default, inferred location, or fallback to
`<workspace_path>/shared`.

Relative global and group paths resolve against the directory containing the
authoritative configuration. The resolved model exposes absolute paths.

Configurations without `schema_version: 3`, `workspace_path`, or `path` are
invalid. Agency does not reinterpret an old `path` as `workspace_path`.

## Storage Domains

The recommended installation layout keeps four disjoint domains:

```text
C:\Projekty\Agents\
|-- agent-library\
|-- compiled-agents\
|-- memory\
|   `-- .jobs\
`-- groups\
    `-- atreides\
        |-- observations\
        |-- proposals\
        |-- decisions\
        |-- locks\
        `-- logs\
```

The domains have distinct authority:

- `agent-library` contains reusable `AGENTS.md` and Agent Skills source.
- `compiled-agents` contains disposable immutable integration projections.
- `memory` contains hash-addressed semantic memory and authoritative durable jobs.
- `groups/<group>` contains human-visible pipeline records and operational output
  for one configured group.

`C:/Projekty/Agents/groups` is an organizational convention, not a global
configuration value. Each group explicitly configures its exact `path`.

## Resolved Group Paths

Add one central typed path model for every resolved group:

```text
workspace_root = groups.<id>.workspace_path
group_root     = groups.<id>.path
observations   = group_root / observations
proposals      = group_root / proposals
decisions      = group_root / decisions
locks          = group_root / locks
logs           = group_root / logs
```

The model is the only source of derived group paths. Web routes, CLI commands,
dispatch, job submission, workers, status collection, templates, and workspace
administration consume it instead of appending `shared` or record directory names
to the execution workspace.

The operation lock lives at:

```text
<group_root>/locks/.operations.lock
```

Authoritative job records remain at:

```text
<memory_store>/.jobs/<group-id>/<job-id>.yaml
```

The group root has no `jobs` child. Prompt snapshots and execution output belong
under the dated log directory for the run:

```text
<group_root>/logs/YYYY-MM-DD/<run-stem>.prompt
<group_root>/logs/YYYY-MM-DD/<run-stem>.out
<group_root>/logs/YYYY-MM-DD/<run-stem>.err
```

Running and recurrence markers remain direct children of `logs`:

```text
<group_root>/logs/.running-<agent>
<group_root>/logs/.last-<agent>-<routine>
```

## Path Validation

Validation resolves paths without following an unsafe runtime directory shape and
reports structured issues before services start or configuration is replaced.

`workspace_path` must:

- exist;
- be a real directory rather than a file, symlink, or unsafe reparse point;
- be readable and writable;
- not overlap the group's `path`;
- not overlap any global control-plane store.

The group `path` may be absent when its nearest existing parent is a writable real
directory. If it exists, it must be a readable and writable real directory.

Every group `path` must be disjoint from:

- every configured `workspace_path`;
- every other group `path`;
- `agency.agent_library`;
- `agency.compilation_cache`;
- `agency.memory_store`.

Configured sandbox roots and agent additional roots must not expose a global
control-plane store. The automatic inclusion of `workspace_path` and group `path`
in the effective runtime policy is intentional and is not reported as overlap.

Validation rejects ancestor, descendant, and equal-path overlap in both
directions. On case-insensitive platforms, comparison uses normalized case.

## Initialization

After the complete configuration passes schema and path validation, Agency
initializes:

- `agency.compilation_cache`;
- `agency.memory_store`;
- `agency.memory_store/.jobs`;
- each configured group `path`;
- each group's `observations`, `proposals`, `decisions`, `locks`, and `logs`
  directories.

Initialization validates each created or existing component as a real directory.
It fails explicitly on permission errors, files, symlinks, unsafe reparse points,
or races that replace a validated directory.

Agency never creates or modifies anything beneath `workspace_path` merely because
a group is configured or services start.

## Runtime Access

For a restricted sandbox, the mandatory base roots are:

1. `workspace_path`;
2. group `path`.

Configured group sandbox roots are additional roots. Agent-level
`additional_roots` remain additive. Tool policy semantics do not change.

This preserves the current collaboration model: agents may directly read and
write observations, proposals, and other group records while working against the
source workspace. It also makes that access explicit in the effective runtime
policy instead of relying on records being nested beneath the workspace.

Unrestricted sandboxes require no root injection, but the resolved runtime context
still carries both `workspace_root` and `group_root`.

## Data Flow

Pipeline and execution flow use the separated roots:

```text
workspace_path ---------------------> integration execution and change capture

group path / observations ----------> dashboard, CLI, agents
group path / proposals -------------> dashboard, CLI, agents
group path / decisions -------------> dashboard, CLI, workers, agents
group path / locks -----------------> revision-bound group operations
group path / logs ------------------> dispatcher, workers, status, log viewer

memory_store / .jobs ---------------> authoritative job submission and execution
memory_store / selector hashes -----> semantic memory selection and publication
```

Job submission resolves and persists both roots in the immutable execution
context where required. Change capture remains tied to `workspace_path`. Pipeline
projection and logs use `group_root`. Semantic memory publication continues to
use `memory_store`.

Any stored or displayed log path is validated against the resolved `logs`
directory. Decision and proposal references are validated against their resolved
group directories. Moving records outside the project must not weaken traversal,
symlink, or reparse-point protections.

## Setup And Administration

First-run setup and group administration present two distinct fields:

- **Workspace path:** project source and execution location.
- **Group path:** Agency records and operational state.

For a project at `C:/Projekty/christag-agency` and group ID `atreides`, setup may
recommend:

```text
workspace_path: C:/Projekty/christag-agency
path: C:/Projekty/Agents/groups/atreides
```

The recommendation requires user approval. Setup does not infer a group path from
the workspace or silently place it under a global store.

Configuration forms validate both paths and all overlap rules before an atomic
revision-checked write. Diagnostics identify the conflicting fields and resolved
paths.

Agent, group, job, log, observation, proposal, and decision surfaces use
`workspace_path` when linking to project execution context and `path` when linking
to group records.

## Failure Behavior

Agency fails closed when:

- either mandatory group path field is absent;
- a workspace does not exist or is inaccessible;
- a group root cannot be created safely;
- authority boundaries overlap;
- a derived record directory is not a safe directory;
- a consumer receives a path outside its resolved authority root.

Startup does not report services ready after partial group initialization.
Configuration replacement does not succeed when the candidate cannot be safely
initialized. Errors are surfaced through existing structured validation issues and
setup diagnostics rather than broad exception suppression or fallback paths.

## Compatibility Policy

This is a green-field canonical redesign:

- previous schema versions are rejected;
- `groups.<id>.path` is never interpreted as a workspace;
- `<workspace>/shared` is never read;
- old operation locks and logs are never discovered;
- no migration, conversion, compatibility alias, dual-read, or cleanup command is
  provided.

Users who need old records must preserve them outside Agency and create a fresh
canonical configuration. Agency does not delete old files.

## Documentation Changes

Repository guidance must consistently describe:

- `workspace_path` as the execution workspace;
- group `path` as the group-state root;
- the four global/group storage domains;
- authoritative jobs under `memory_store/.jobs`;
- operation locks under `group path/locks`;
- the absence of project-local `shared` records.

Examples, setup prompts, templates, configuration documentation, data-format
documentation, and directory diagrams must use the canonical schema and layout.

## Testing

### Configuration and paths

- Accept a complete `schema_version: 3` configuration.
- Reject missing `workspace_path` or group `path`.
- Reject prior schema and field shapes.
- Resolve relative paths against the configuration directory.
- Reject equal, ancestor, descendant, and case-normalized overlaps.
- Reject files, symlinks, unsafe reparse points, and inaccessible paths.
- Permit creation only through a writable safe parent.

### Initialization

- Create every global and group-owned directory after validation.
- Remain idempotent for an already initialized configuration.
- Fail without reporting readiness after partial or unsafe initialization.
- Assert that initialization never creates `<workspace_path>/shared`.

### Runtime policy

- Include `workspace_path` and group `path` as mandatory restricted-sandbox roots.
- Preserve configured group roots and agent additional roots as additions.
- Reject global control-plane stores exposed through configured runtime roots.
- Keep tool-policy override behavior unchanged.

### Consumers

- Read and write observations, proposals, and decisions under `group_root`.
- Write locks only under `group_root/locks`.
- Write logs, prompts, and markers only under `group_root/logs`.
- Keep authoritative jobs only under `memory_store/.jobs`.
- Capture source changes from `workspace_root`.
- Validate log and pipeline paths against the correct resolved roots.
- Update dashboard, CLI, dispatcher, workers, status, and admin routes together.

### Repository boundary

- Assert no application path construction contains the `shared` segment.
- Assert startup, setup, administration, dispatch, and job execution leave the
  project workspace free of Agency-generated record directories.
- Update reload tests so external group-state writes do not trigger code reloads.
