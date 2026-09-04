# Directory Structure

The current model separates source, generated runtime context, mutable memory, and workspaces.

```text
agent-library/
`-- advisor/
    |-- AGENTS.md
    `-- .agents/skills/
        `-- daily-review/SKILL.md

compiled-agents/
`-- <integration>/<projector-version>/<source-digest>/

memory/
|-- <selector-hash>/
|   `-- memory.md
`-- .jobs/
prompts/
`-- <team>/<instance>/<prompt>.prompt.md
teams/
`-- <team-id>/
    |-- observations/
    |-- proposals/
    |-- decisions/
    |-- locks/
    `-- logs/
```

The Agent Library follows `AGENTS.md`, Agent Skills, and shared prompt standards under `.agents/prompts`. It has no Agency manifest and no mutable memory. Compiled output is disposable and immutable. Memory directories are internal hash addresses for semantic selectors such as `scope: routine` or `scope: channel`; config and UI show semantic names, not hashes.

The prompt store contains canonical instance-private saved prompts selected by config. Runtime-native prompt locations inside compiled integrations are generated output only.

The project workspace belongs to the team as `workspace_path`. The Agency-owned team root is `path`; configured instances run from the workspace and do not own physical subdirectories. Optional tmux, IDE, or Windows Terminal launchers also start from this team workspace and never become configuration authority. The team root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `memory/.jobs`, and operation locks live in `<team.path>/locks`.

## Superseded layouts

Directory-coupled agent folders, sidecars, and per-agent memory can remain in repository history, but runtime does not consult them.
