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
groups/
`-- <group-id>/
    |-- observations/
    |-- proposals/
    |-- decisions/
    |-- locks/
    `-- logs/
```

The Agent Library follows `AGENTS.md` and Agent Skills standards. It has no Agency manifest and no mutable memory. Compiled output is disposable and immutable. Memory directories are internal hash addresses for semantic selectors such as `scope: routine` or `scope: channel`; config and UI show semantic names, not hashes.

The project workspace belongs to the group as `workspace_path`. The Agency-owned group root is `path`; configured instances run from the workspace and do not own physical subdirectories. Optional tmux, IDE, or Windows Terminal launchers also start from this group workspace and never become configuration authority. The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `memory/.jobs`, and operation locks live in `<group.path>/locks`.

## Superseded layouts

Directory-coupled agent folders, sidecars, and per-agent memory can remain in repository history, but runtime does not consult them.
