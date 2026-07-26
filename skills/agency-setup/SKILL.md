---
name: agency-setup
description: Use when creating or registering a new Agency agent team for a codebase.
user_invocable: true
---

# Agency Setup

The `agency-setup` skill owns the one authoritative canonical Agency config. After the user chooses a project folder and supported AI integration, the skill first asks for one Agency data root, derives the canonical storage paths, and then takes over group naming, blueprint source, explicit instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write. It accepts only the canonical config shape, creates it when absent, and reports validation errors directly. It does not create runtime-native identities, physical per-agent runtime directories, memory files, prompt schedules, or conversion surfaces.

## 1. Inspect And Choose The Data Root

Consume the launch context before asking questions. Read project instructions, README, dependency manifests, source layout, tests, deployment files, and recent git history. Detect the host OS and available agent CLI. Keep this inspection read-only and do not ask about the group, agents, roles, routines, workspaces, memory channels, or individual storage paths yet.

After inspection, ask for the Agency data root as the first user-facing question. Explain that it is a separate home for Agency-owned data: reusable agent blueprints, disposable compiled projections, semantic memory and durable jobs, and per-group records. The project workspace remains the source and execution location, and `config.yaml` remains at the authoritative path supplied by the launcher.

Accept an existing directory or a new absolute path. Expand user-home syntax. A missing root is valid when its nearest existing parent is a writable real directory that can safely create it. Give `C:\Agency` and `~/Agency` as examples. Derive these paths in memory without creating them:

```text
agency.agent_library = <root>/agent-library
agency.compilation_cache = <root>/compiled-agents
agency.memory_store = <root>/memory
groups.<group-id>.path = <root>/groups/<group-id>
groups.<group-id>.workspace_path = <project workspace>
```

The group path remains pending until the group ID is approved. Do not create any derived directory during this section.

## 2. Plan The Team And Resolve Agency

After the root is selected, summarize the project and propose three to five distinct roles. Exactly one builder normally receives write capability; observational roles remain fail-closed.

Before registration, ask the user how many agents to create for the first team and which proposed roles to create now. Do not infer extra instances beyond the approved count and selected roles. Ask the user to approve the group name and ID, team, each role's routine tasks, schedules, workspace definitions, and any shared memory channels. When the launch prompt contains `Selected integration:`, use that registered integration for `group.default_integration` and the initial agent instances unless the user explicitly approves a different registered integration.

After the group ID is approved, derive `groups.<group-id>.path`. Ask exactly once: `Customize the derived storage paths?` If declined, keep every derived path. Do not ask about individual storage paths in the default flow. If accepted, present all four storage paths in one grouped review and allow any of them to be replaced.

Resolve every effective path before creation. Require that each missing effective path's nearest existing parent is a writable real directory that can safely create it, reject files, symlinks, and unsafe Windows reparse points, keep the global stores mutually disjoint, and keep every Agency-owned path disjoint from the project workspace. If validation fails, name the conflicting fields and resolved paths and return to the root choice or grouped review. Never choose a fallback location or project-local storage.

Show one consolidated path summary containing the project workspace, authoritative config path, Agency data root, and four effective storage paths. No directory or blueprint may be created before the user approves this summary.

When the launch prompt contains `Authoritative config:`, use that exact path and do not search for or choose another config. When the skill is invoked manually without an explicit authoritative path, find one config in this order: a valid `AGENCY_CONFIG`, the current project's config, then common user-level Agency locations. Parse YAML and accept only a mapping where the required `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store` paths are present.

If no config exists, record the absent revision and defer creation and replacement until Section 5. Do not write a placeholder or partial config. If an existing candidate is invalid or superseded, report validation errors and stop; never invoke another skill, never scan or convert superseded authority, and never convert old layouts. During manual invocation, if multiple canonical configs remain, ask the user which is authoritative; never choose implicitly.

Load the current revision before editing, or use the absent revision when the file does not exist. Preserve unrelated keys and groups while building the complete candidate in memory. Do not replace the authoritative config during inspection, blueprint creation, or instance registration.

## 3. Build The Agent Library

After the consolidated path summary is approved, create the approved `agency.agent_library` through safe directory operations; do not place blueprints under the project workspace. For each approved role, always create:

````markdown
After the consolidated path summary is approved, create the approved `agency.agent_library` through safe directory operations; do not place blueprints under the project workspace. For each approved role, always create:

```text
{agent_library}/{blueprint}/
`-- AGENTS.md
```

Write `{agent_library}/{blueprint}/AGENTS.md` from `references/templates.md`. Blueprints may contain zero or more standard Agent Skills. For each approved routine capability, create `{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md`. Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities. Skill frontmatter must contain a directory-matching `name` and a trigger-only `description`. Put supporting scripts, references, and assets inside the skill directory.
````

Blueprint files contain reusable instructions only. They do not contain identity, integration, schedules, runtime policy, or mutable memory. Do not create runtime-native `CLAUDE.md` or `GEMINI.md`; Agency's projector creates disposable native layouts in `agency.compilation_cache`.

## 4. Register Instances

Upsert one group whose `workspace_path` points to the project workspace and whose `path` points to the Agency-owned group-state root. `workspace_path` is the execution workspace and source repository; `path` is the Agency-owned group root. The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `agency.memory_store/.jobs`, and operation locks live in `<group.path>/locks`. Preserve existing group workspaces and unrelated settings. Every instance explicitly pins a blueprint and integration. Runtime defaults belong to the group; instance roots are additive and an instance tool policy is a complete override.

Use this canonical shape:

```yaml
schema_version: 3
agency:
  title: Agency
  default_group: example
  ai_backend: copilot
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
memory:
  channels:
    project-strategy:
      display_name: Project Strategy
groups:
  example:
    name: Example
    workspace_path: C:/Projects/example
    path: C:/Agency/groups/example
    default_integration: copilot
    runtime:
      timeout: 1800
      sandbox:
        mode: restricted
        roots: [C:/Projects/example]
      tools:
        mode: allowlist
        names: [read, search]
    dispatch:
      enabled: true
      daily_limit: 15
    agents:
      - name: builder
        blueprint: builder
        integration: copilot
        identity:
          display_name: Builder
          title: Implementation Lead
        capabilities:
          write: true
        runtime:
          sandbox:
            additional_roots: []
          tools:
            mode: allowlist
            names: [read, search, write]
        default_memory:
          scope: agent
        routines:
          - id: morning-review
            skill: morning-review
            schedule:
              at: "07:00"
            memory:
              scope: routine
          - id: strategy-review
            skill: strategy-review
            schedule:
              at: "21:00"
            memory:
              scope: channel
              channel: project-strategy
      - name: advisor
        blueprint: advisor
        integration: copilot
        capabilities:
          write: false
    workspaces:
      - name: Main workspace
        type: ide
        config:
          ide_name: VS Code
          project_path: C:/Projects/example
```

Record each approved Phase 2 routine assignment under that instance's `routines`. A routine selects one standard skill, one schedule (`at`, `every`, or supported condition), optional arguments, and optional semantic memory. Never write prompt filenames or per-agent dispatch maps.

Set `capabilities.write: true` only for an explicitly approved implementation role and `capabilities.write: false` otherwise. Never infer write authority for an existing agent; ask the user when a newly generated role is ambiguous.

Write every approved workspace under the group's `workspaces` list. For a new group, do not omit the list after the user approves a workspace. Keep workspace configuration group-owned and non-authoritative.

## 5. Verify And Schedule

Validate every blueprint and Agent Skill, config cross-reference, registered explicit integration, effective root union, complete tool override, routine skill, channel, workspace, group naming, and storage path. Re-read the authoritative config revision and stop on drift. Write one complete configuration atomically. Use Agency's revision-checked `ConfigStore.replace(expected_revision, complete_candidate)` for that single write; it initializes the approved cache, memory, durable-job, group, record, lock, and log directories. On revision drift, validation failure, or filesystem failure, stop without replacing the previous config and do not automatically remove approved directories or blueprint source. Then parse the final config from disk and confirm it is still the revision just written. Then offer the singleton scheduler setup:

```text
christag-agency dispatch install --config "{config_path}"
christag-agency dispatch status --config "{config_path}"
```

There must be exactly one Agency dashboard and one singleton scheduler; do not create a fallback project scheduler.

Report the Agency data root, effective storage paths, blueprint keys, instance IDs, routines, semantic memory scopes/channels, authoritative config path, and scheduler status.
