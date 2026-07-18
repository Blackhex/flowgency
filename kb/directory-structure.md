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

memory-store/
`-- <selector-hash>/
    `-- memory.md

project-workspace/
`-- shared/
    |-- observations/
    |-- proposals/
    |-- decisions/
    |-- jobs/
    `-- logs/
```

The Agent Library follows `AGENTS.md` and Agent Skills standards. It has no Agency manifest and no mutable memory. Compiled output is disposable and immutable. Memory directories are internal hash addresses for semantic selectors such as `scope: routine` or `scope: channel`; config and UI show semantic names, not hashes.

The project workspace belongs to the group. Configured instances run there and do not own physical subdirectories. Optional tmux, IDE, or Windows Terminal launchers also start from this group workspace and never become configuration authority.

## Superseded layouts

Directory-coupled agent folders, sidecars, and per-agent memory can remain in repository history, but runtime does not consult them.
