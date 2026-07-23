# Agent Library Templates

## Blueprint AGENTS.md

Create this reusable source at `{agent_library}/{blueprint}/AGENTS.md`:

```markdown
# {ROLE_NAME}

You are a {ROLE_NAME} for projects that use {LANGUAGE_OR_DOMAIN}.

## Mission

{REUSABLE_ROLE_MISSION}

## Responsibilities

- {RESPONSIBILITY}
- {RESPONSIBILITY}

## Working Method

- Inspect project-local instructions and tests before acting.
- Use the selected Agent Skills for named tasks.
- Respect the runtime roots, tools, and write capability supplied by Agency.

## Boundaries

- Do not expand runtime authority or edit Agency configuration.
- Do not push, publish, or use destructive commands without approval.
```

Keep project-specific identity, integration, runtime policy, schedules, and mutable memory out of this file.

## Canonical Group Registration
Default setup starts from one user-selected Agency data root. For an Agency data root at `C:/Agency` and a group ID of `example`, derive:

```text
C:/Agency/
|-- agent-library/
|-- compiled-agents/
|-- memory/
`-- groups/
    `-- example/
```

Map those derived paths to the unchanged schema version 3 fields and keep the execution workspace separate from Agency-owned state:

```yaml
schema_version: 3
agency:
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
groups:
  example:
    workspace_path: C:/Projects/example
    path: C:/Agency/groups/example
```

The project workspace stays at `groups.<group-id>.workspace_path`, while the derived root-backed fields remain Agency-owned storage.

## Standard Agent Skill

Create each routine capability as a standard Agent Skill at `{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md`:

```markdown
---
name: {skill}
description: Use when {CONCRETE_TRIGGER_CONDITION}.
---

# {Skill Title}

## Outcome

{EXPECTED_RESULT}

## Steps

1. Read the relevant project files and current semantic memory supplied by Agency.
2. Perform {TASK} with project-appropriate commands.
3. Record observations or proposals through the project's configured pipeline.
4. Update memory only with durable facts.

## Boundaries

- {TASK_SPECIFIC_BOUNDARY}
```

Use standard `scripts/`, `references/`, and `assets/` subdirectories when needed. A routine selects the skill by its stable `name`; there are no prompt files.
