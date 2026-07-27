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

Map those derived paths to the schema version 4 fields and keep the execution workspace separate from Agency-owned state:

```yaml
schema_version: 4
agency:
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
  prompt_store: C:/Agency/prompts
groups:
  example:
    workspace_path: C:/Projects/example
    path: C:/Agency/groups/example
```

workspace_path is project source and execution. path is Agency-owned group state. Agency never loads or creates <workspace_path>/shared. durable jobs live in agency.memory_store/.jobs, and operation locks live in <group.path>/locks.

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

Use standard `scripts/`, `references/`, and `assets/` subdirectories when needed. Routines select a scoped prompt rather than a skill.

## Standard Task Prompt

Create each routine task as a portable prompt document at `{agent_library}/{blueprint}/.agents/prompts/{prompt}.prompt.md`:

```markdown
---
name: {prompt}
description: {ONE_LINE_PURPOSE}
argument-hint: {OPTIONAL_ARGUMENT_SUMMARY}
---

# {Prompt Title}

{TASK_INSTRUCTIONS}
```

Agency rejects any prompt document that breaks this contract:

- The file lives at `.agents/prompts/{prompt}.prompt.md` under the blueprint root. No other location is accepted.
- The file is encoded as UTF-8.
- The YAML frontmatter opens with `---` on its own line and is terminated by `---` on its own line before the body.
- `name` exactly equals the file slug.
- The slug uses 1 to 64 lowercase letters, digits, and single hyphen separators, with no leading or trailing hyphen.
- `description` is a non-empty, non-whitespace-only string of at most 1024 characters.
- `argument-hint` is optional and, when present, a string. Omit the key entirely when the prompt takes no arguments.
- No keys other than `name`, `description`, and `argument-hint` are permitted.
- The markdown body after the closing `---` is non-empty.
