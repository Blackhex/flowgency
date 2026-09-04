# Schema V6 Team Terminology

**Date:** 2026-09-04
**Status:** Approved (design)

## Problem

Agency uses `group` for its agent-team domain across configuration, models, CLI,
routes, jobs, memory, templates, setup, docs, and tests. The product will use
`team` consistently, including persisted and public contracts, as a strict
breaking rename with no compatibility behavior.

## Goals

1. Make `team` the sole Agency domain term for a collection of agents.
2. Introduce `schema_version: 6` with root `teams` and `agency.default_team`.
3. Rename internal, persisted, CLI, URL, template, setup, documentation, test,
   and default filesystem terminology from group to team.
4. Preserve behavior except renamed formats and intentional old-format rejection.

## Non-Goals

- Supporting v5 or earlier configuration, job, memory, URL, CLI, or worker
  formats.
- Providing a migration command, converter, alias, dual read, fallback, redirect,
  or deprecated flag.
- Changing authorization, scheduling, execution, storage safety, configuration
  authority, or setup behavior beyond terminology and emitted v6 shape.
- Renaming Python regex APIs (`match.group()` and `match.groups()`), Tailwind
  `group` utilities, HTML `<optgroup>`, or v4-to-v5 migrator internals that
  describe historical input.
- Rewriting historic docs, existing records/logs, or custom user path values.

## Strict Version Policy

`schema_version: 6` is the sole accepted config version. Root keys are exactly
`schema_version`, `agency`, `memory`, and `teams`. `groups`, `default_group`,
and v5 config fail validation with v6/manual-rewrite diagnostics. There is no
conversion.

Delete v4-to-v5 migration implementation, tests, CLI parser branch, help, docs,
and hints. A migrator that emits rejected config would offer false recovery.

Job schema advances from v4 to v5. Job YAML serializes `team_key`/`team_root`
and accepts only version 5/current field names. Earlier jobs are unreadable.
Worker launcher/parser use `--team-id` atomically.

Memory selector scope `group` becomes `team`, and canonical selector JSON uses
`team` for scope/context. Earlier team-, agent-, and routine-scoped hashes become
unreachable intentionally. Run/channel semantics remain unchanged.

## Canonical V6 Shape

```yaml
schema_version: 6
agency:
  title: Agency
  default_team: newsletter
  ai_backend: copilot
  jobs:
    pool: 4
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
  prompt_store: C:/Agency/prompts
teams:
  newsletter:
    name: Newsletter
    workspace_path: C:/Projects/newsletter
    path: C:/Agency/teams/newsletter
    default_integration: copilot
    runtime:
      permissions:
        mode: restricted
        rules:
          - path: C:/Projects/newsletter
            tools: [read, search]
    agents: []
```

`teams.<team-id>.path` is Agency-owned team state;
`teams.<team-id>.workspace_path` is source/execution workspace. Generated
defaults become `<root>/teams/<team-id>`. Custom configured paths remain
authoritative and are never mechanically rewritten.

## Domain Rename

### Configuration, Models, And Validation

Rename `GroupConfig`, `GroupRuntime`, `GroupDispatch`, `ResolvedGroupPaths`,
`config.groups`, `default_group`, and domain symbols/helpers such as `group_id`,
`group_key`, `group_root`, `group_path`, `_groups`, `_group`,
`resolve_group_paths`, and `runtime_group` to team equivalents. Validators,
diagnostics, form/query names, template context, and tests use `teams.*`, team
error codes, and team terminology.

### Public Interfaces

Only these public forms remain:

```text
/admin/teams
/admin/teams/{team}/...
/{team}/...
--team <team-id>
--team-id <team-id>
```

Old `/admin/groups`, `/admin/orgs`, `/{group}/...`, `--group`, and
`--group-id` forms are absent without redirects or aliases.

### Runtime And Persistence

Rename team-scoped job, queue, lock, authority, dispatch, reconciliation,
worker, prompt-store, instance-service, permission, record, memory-publication,
recovery, and workspace fields/parameters from `group` to `team` where they
denote the Agency domain.

`JobRequest.group_key`, `JobSpec.group_key`, `JobSpec.group_root`,
`resolved_group_root`, and `JobAuthorityRef.group_id` become team equivalents.

Memory selectors use:

```text
scope: team
criteria: {"version", "scope", "team"}
```

Agent/routine criteria use `team` too. New hash paths derive only from the new
canonical JSON.

### Setup, App, Docs, And Examples

Setup prompts and the canonical `agency-setup` skill use team display name, team
ID, team workspace, team root, team approval, and team configuration language.
Exact config literals become v6 `teams`, `default_team`, and
`teams.<team-id>`. Generated setup paths use `<root>/teams/<team-id>`.

Rename app/UI navigation, routes, forms, redirects, template context, active
README/KB documents, `AGENTS.md`, service/config examples, examples, setup
assets, UI fixtures, and tests. Historic specs/plans retain historic vocabulary.

## Behavior Preservation

This is a terminology/serialization migration only. Configuration remains sole
authority; team workspace and team root retain their source/state roles;
permissions retain effective-policy semantics; jobs, scheduling, memory,
records, logs, observations, proposals, decisions, workers, and setup atomic
writes retain behavior. No native directory loader or old-state reader is added.

## Errors

- Non-v6 config says `schema_version must be 6` and identifies `teams` and
  `default_team` as current shape.
- v5 `groups` config, old CLI flags/URLs/worker flags, old job fields/versions,
  and `scope: group` selectors fail normally; none is converted, redirected, or
  searched.

## Testing And Verification

- Parse v6 `teams` and reject v5, `groups`, and `default_group`; rename models,
  patches, validators, paths, policy, and eligibility without semantic change.
- Serialize only v5 jobs with `team_key`/`team_root`; accept only `--team-id`.
- Serialize only `scope: team` and team selector criteria; reject old state.
- Test `--team`, team URLs/admin routes/forms/navigation, and absence of old
  flags/URLs.
- Update active docs/examples/setup/UI fixtures and scan active domain tokens.
- Explicitly preserve regex APIs, Tailwind utilities, HTML `<optgroup>`, and
  historic-format descriptions as exclusions.
- Run focused config/job/memory/CLI/route/UI/setup/doc scans, full Python suite,
  Playwright suite, and CLI/dashboard smoke tests with a clean v6 config.

## Acceptance Criteria

1. Only v6 `teams` configuration is accepted.
2. `default_team` replaces domain `default_group` state.
3. Domain models, APIs, paths, jobs, memory, prompts, records, workspaces,
   dispatch, and permissions use team naming.
4. Public CLI and URLs use team; group counterparts are absent.
5. Jobs use current schema 5 and team fields only.
6. Memory selectors use `scope: team` and team criteria only.
7. Generated defaults use `<root>/teams/<team-id>`.
8. Setup, UI, docs, examples, fixtures, and tests present teams.
9. Old config/job/memory/CLI/route forms receive no compatibility handling.
10. Behavior is unchanged beyond renamed terms, versions, serialized fields,
    defaults, and old-format rejection.
11. Regex, Tailwind, HTML, historic docs, and historic raw-format descriptions
    remain intact.

## Rejected Alternatives

### Display-Only Rename

Visible copy alone leaves conflicting config and public terminology.

### Compatibility Or Migration

Aliases, dual reads, redirects, old flags, or converters conflict with the
requested strict break.

### Renaming Non-Domain Tokens

Regex APIs, CSS utilities, and `<optgroup>` are unrelated external contracts.
