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

## Standard Agent Skill

Create each routine capability at `{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md`:

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
