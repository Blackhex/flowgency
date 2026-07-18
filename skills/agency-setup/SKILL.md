---
name: agency-setup
description: Use when creating or registering a new Agency agent team for a codebase.
user_invocable: true
---

# Agency Setup

The `agency-setup` skill owns the one authoritative canonical Agency config. After the user chooses a project folder and supported AI integration, the skill takes over group naming, storage paths, blueprint source, explicit instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write. It accepts only the canonical config shape, creates it when absent, and reports validation errors directly. It does not create runtime-native identities, physical per-agent runtime directories, memory files, prompt schedules, or conversion surfaces.

## 1. Inspect

Read project instructions, README, dependency manifests, source layout, tests, deployment files, and recent git history. Detect the host OS and available agent CLI. Summarize the project, then propose three to five distinct roles. Exactly one builder normally receives write capability; observational roles remain fail-closed.

Ask the user to approve the team, each role's routine tasks, runtime integration, schedules, workspace paths, and any shared memory channels.

## 2. Resolve Agency

Find one authoritative config in this order: a valid `AGENCY_CONFIG`, the current project's config, then common user-level Agency locations. Parse YAML and accept only a mapping where the required `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store` paths are present.

If no config exists, create the canonical config at the authoritative path. If a candidate is invalid or superseded, report validation errors and stop; never invoke another skill, never scan or convert superseded authority, and never convert old layouts. If multiple canonical configs remain, ask the user which is authoritative; never choose implicitly.

Load the config revision before editing. Re-read and compare the revision immediately before replacement, preserve unrelated keys and groups, validate the complete config result, write atomically, then parse and verify the file from disk.

## 3. Build The Agent Library

Use the configured global `agency.agent_library`; do not place blueprints under the project workspace. For each approved role, create:

```text
{agent_library}/{blueprint}/
|-- AGENTS.md
`-- .agents/skills/
		`-- {skill}/
				`-- SKILL.md
```

Write `{agent_library}/{blueprint}/AGENTS.md` from `references/templates.md`. Convert every scheduled task into standard Agent Skills at `{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md`. Skill frontmatter must contain a directory-matching `name` and a trigger-only `description`. Put supporting scripts, references, and assets inside the skill directory.

Blueprint files contain reusable instructions only. They do not contain identity, integration, schedules, runtime policy, or mutable memory. Do not create runtime-native `CLAUDE.md` or `GEMINI.md`; Agency's projector creates disposable native layouts in `agency.compilation_cache`.

## 4. Register Instances

Upsert one group that points `path` at the project workspace. Preserve existing group workspaces and unrelated settings. Every instance explicitly pins a blueprint and integration. Runtime defaults belong to the group; instance roots are additive and an instance tool policy is a complete override.

Use this canonical shape:

```yaml
agency:
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
    path: C:/Projects/example
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
```

Record each approved Phase 2 routine assignment under that instance's `routines`. A routine selects one standard skill, one schedule (`at`, `every`, or supported condition), optional arguments, and optional semantic memory. Never write prompt filenames or per-agent dispatch maps.

Set `capabilities.write: true` only for an explicitly approved implementation role and `capabilities.write: false` otherwise. Never infer write authority for an existing agent; ask the user when a newly generated role is ambiguous.

## 5. Verify And Schedule

Validate every blueprint and Agent Skill, config cross-reference, explicit integration, effective root union, complete tool override, routine skill, channel, workspace, group naming, and storage path. Write one complete configuration atomically. Then parse the final config from disk and confirm it is still the revision just written. Then offer the singleton scheduler setup:

```text
christag-agency dispatch install --config "{config_path}"
christag-agency dispatch status --config "{config_path}"
```

There must be exactly one Agency dashboard and one singleton scheduler; do not create a fallback project scheduler.

Report blueprint keys, instance IDs, routines, semantic memory scopes/channels, authoritative config path, and scheduler status.
