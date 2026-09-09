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
workflow-library/
`-- <blueprint-id>/
    `-- workflow.yaml
tickets/
`-- <binding>/<workflow>/<ticket-id>/
teams/
`-- <team-id>/
    |-- locks/
    `-- logs/
```

The Agent Library follows `AGENTS.md`, Agent Skills, and shared prompt standards under `.agents/prompts`. It has no Flowgency manifest and no mutable memory. Compiled output is disposable and immutable. Memory directories are internal hash addresses for semantic selectors such as `scope: routine` or `scope: channel`; config and UI show semantic names, not hashes.

The prompt store contains canonical instance-private saved prompts selected by config. Runtime-native prompt locations inside compiled integrations are generated output only.

The `flowgency.workflow_library` root holds reusable ticket workflow blueprints, one `workflow.yaml` per blueprint id. Each configured team workflow instance names a blueprint and a Local ticket storage root; ticket records, history, and receipts live under that storage root, keyed by the workflow binding. An unavailable or unreadable storage root is not an empty ticket set, and one invalid blueprint never erases the rest of the listing. Ticket storage is external operational data, not control-plane authority.

The project workspace belongs to the team as `workspace_path`. The Flowgency-owned team root is `path`, holding locks and logs; configured instances run from the workspace and do not own physical subdirectories. Optional tmux, IDE, or Windows Terminal launchers also start from this team workspace and never become configuration authority. The team root is automatically available to restricted agents. Flowgency never loads or creates `<workspace_path>/shared`. Durable jobs live in `memory/.jobs`, and operation locks live in `<team.path>/locks`.

## Superseded layouts

Directory-coupled agent folders, sidecars, per-agent memory, and file-based observation/proposal/decision records can remain in repository history, but runtime does not consult them.
