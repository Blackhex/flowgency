# Agency Data Root Setup

**Date:** 2026-07-23
**Status:** Approved (design)

## Problem

Guided first-run setup currently treats Agency's storage locations as separate
user decisions. New users can be asked individually for the agent library,
compilation cache, memory store, and group-state path even though the recommended
layout places them beneath one Agency-owned root. This makes setup longer and
forces users to understand internal storage domains before they can create their
first team.

## Goals

1. Make the Agency data root the first user-facing setup question.
2. Explain clearly what Agency stores there and how it differs from the project
   workspace and authoritative configuration file.
3. Derive all canonical storage paths from the selected root by default.
4. Keep advanced path overrides available without exposing them in the default
   flow.
5. Preserve schema version 3, path authority boundaries, validation, and the one
   revision-checked atomic configuration write.

## Non-Goals

- Adding an `agency.data_root` configuration field.
- Adding a data-root field to the browser setup page.
- Moving, migrating, or cleaning up existing Agency data.
- Changing runtime storage ownership or path-validation rules.
- Placing Agency-owned data beneath the project workspace.

## Root-First Experience

After consuming the launch context and performing any read-only project
inspection, `agency-setup` must make the Agency data root its first question. It
must not ask about the group, agents, roles, routines, workspaces, memory
channels, or individual storage paths first.

The question explains that the root is a separate home for reusable agent
blueprints, disposable compiled projections, semantic memory and durable jobs,
and per-group records. It states that the project source remains at the supplied
project workspace and `config.yaml` remains at the separately supplied
authoritative path. It accepts an existing directory or a new absolute path,
including an expanded user-home path. Examples such as `C:\Agency` and
`~/Agency` make the expected input concrete.

The default layout is:

```text
<root>/
|-- agent-library/
|-- compiled-agents/
|-- memory/
`-- groups/
    `-- <group-id>/
```

The paths map to the existing canonical fields:

```text
agency.agent_library          = <root>/agent-library
agency.compilation_cache      = <root>/compiled-agents
agency.memory_store           = <root>/memory
groups.<group-id>.path        = <root>/groups/<group-id>
groups.<group-id>.workspace_path = <project workspace>
```

The root can be collected before the group ID exists. Setup derives the three
global paths immediately and derives the final group path after the group name
and ID are approved.

## Advanced Overrides

The default conversation never asks users to approve or enter each derived path
separately. Once the group ID is known, setup asks one opt-in question:
`Customize the derived storage paths?`

If declined, setup keeps all derived paths. If accepted, setup presents the four
paths together in one grouped review and allows any of them to be replaced. It
does not fall back to a mandatory path-by-path interview.

Both routes end with one consolidated summary showing the project workspace,
authoritative config path, Agency data root, and four effective storage paths.
No directory or blueprint is created until the user approves that summary.

## Components

### First-Run Handoff

`agency.web.setup_flow.build_setup_prompt` explicitly instructs every launchable
integration that the Agency data root is the first user-facing question. The
handoff includes the canonical subfolder names, the optional grouped override
branch, and the requirement to keep Agency-owned data outside the project.

### Canonical Setup Skill

`skills/agency-setup/SKILL.md` owns the root explanation, question ordering,
path derivation, advanced branch, summary approval, validation, directory
creation, and final reporting. The `.github/skills/agency-setup` discovery path
continues to resolve to this canonical source.

The skill's templates and setup documentation use the same root-derived example
so the launch contract, agent instructions, and user guidance cannot describe
different flows.

### Configuration And Runtime

The canonical schema remains unchanged. The selected root is setup input, not
persisted authority. Runtime consumers continue reading the four existing
fields, and `ConfigStore.replace` remains the only final configuration write.

## Data Flow

1. The launcher supplies the project workspace, authoritative config path, and
   selected integration.
2. The setup skill performs read-only inspection and asks for the Agency data
   root as its first question.
3. Setup resolves the root and derives the three global paths without creating
   them.
4. Setup obtains the group name and ID, then derives
   `<root>/groups/<group-id>`.
5. Setup offers the single advanced-override branch and obtains one consolidated
   path approval.
6. Setup validates the effective paths and creates the approved agent library
   so it can write blueprint source and Agent Skills.
7. Setup builds and validates the complete schema version 3 candidate in memory.
8. `ConfigStore.replace(expected_revision, candidate)` rechecks the revision,
   initializes the cache, memory, durable-job, group, record, lock, and log
   directories, and atomically replaces the authoritative config.
9. Setup reparses the written config and reports the root, effective paths,
   blueprints, instances, routines, memory selectors, and scheduler status.

## Validation And Failure Behavior

Before creation, setup resolves every effective path and applies the existing
authority rules. The selected or overridden paths must have writable real
parents, must not be files, symlinks, or unsafe Windows reparse points, and must
remain mutually disjoint and outside the project workspace. A missing root is
valid when its nearest existing parent can safely create it.

Validation errors name the conflicting fields and resolved paths. Setup returns
to the root choice or grouped override review; it never silently chooses a new
location or falls back to project-local storage.

Blueprint creation occurs only after path approval because the agent library
must exist and be readable for final config validation. Revision drift,
validation failure, or directory creation failure stops the final write and
leaves the previous config bytes unchanged. Setup does not automatically remove
approved directories or blueprint source already created before a later failure.

## Documentation

Update the setup guide, canonical template example, and quick-start wording to
describe one Agency data root and the derived layout. Examples continue showing
the schema version 3 fields so users can connect the friendly setup choice to the
stored configuration.

## Testing

### Setup Skill Contract

- Assert that the data-root question is explicitly the first user-facing
  question and precedes agent-count and role-selection questions.
- Assert that the root explanation distinguishes Agency-owned data, project
  source, and the authoritative config path.
- Assert the canonical `agent-library`, `compiled-agents`, `memory`, and
  `groups/<group-id>` derivations and field mappings.
- Assert that a missing root is accepted when safely creatable.
- Assert that advanced overrides require one opt-in question and use one grouped
  review rather than mandatory individual prompts.
- Preserve assertions for canonical config authority, no project-local `shared`,
  blueprint placement, explicit instances, and one atomic write.

### First-Run Handoff

- Assert that `build_setup_prompt` requires the root-first flow and names all
  canonical derived paths.
- Preserve project path, config path, selected integration, schema version,
  validation, and atomic-write assertions.

### Regression Boundary

Run the focused setup-skill and setup-flow tests while iterating, then run the
complete Python suite. No browser test is required because the browser setup
form and request model do not change.

## Acceptance Criteria

1. The first setup question asks for one well-explained Agency data root.
2. A default setup asks no individual storage-path questions.
3. Setup derives and creates the canonical subfolders beneath the approved root.
4. Advanced users can opt into one grouped path override review.
5. The generated config uses only the existing schema version 3 path fields.
6. Project source remains free of Agency-generated control-plane and group-state
   directories.
7. Existing validation and revision-checked atomic-write behavior remains intact.