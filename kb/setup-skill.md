# Agency Setup Skill

The `agency-setup` skill owns the one authoritative canonical Agency config. Guided first-run setup supplies an approved Agency data root, authoritative config path, and supported AI integration; the guided conversation asks for the project workspace as its first question. Without guided context, the skill asks for the data root and then the workspace explicitly. The skill takes over group naming, storage paths, blueprint source, explicit instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write. It accepts only the canonical config shape, creates the config when absent, and reports validation errors directly. It does not create runtime-native identities, physical agent directories, memory files, prompt schedules, or conversion surfaces. Generated native prompt files remain derived output.

Every generated config uses `schema_version: 5` and requires `agency.agent_library`, `agency.compilation_cache`, `agency.memory_store`, and `agency.prompt_store`. Each group has both `workspace_path` (the execution workspace and source repository) and `path` (the Agency-owned group root). The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`; durable jobs live in `agency.memory_store/.jobs`, and operation locks live in `<group.path>/locks`.

## Agency Data Root

In guided mode, the data root is pre-supplied by the launcher; the project workspace is the first question. In manual mode, the skill asks for the data root first and then the project workspace. Read-only inspection follows workspace selection in both modes. The data root is a separate home for reusable agent blueprints, disposable compiled projections, semantic memory and durable jobs, and per-group records. The project workspace remains source code and execution context, while `config.yaml` remains at its authoritative path.

The root may be an existing directory or a new absolute path whose nearest existing parent is a writable real directory that can safely create it. For a root at `C:/Agency` and group ID `example`, setup derives:

```text
C:/Agency/
|-- agent-library/       -> C:/Agency/agent-library
|-- compiled-agents/     -> C:/Agency/compiled-agents
|-- memory/              -> C:/Agency/memory
|-- prompts/             -> C:/Agency/prompts
`-- groups/example/      -> C:/Agency/groups/example
```

Users may enter home syntax such as `~/Agency`; setup expands it to the user's home directory before deriving and storing the canonical paths.

Setup then asks `Customize the derived storage paths?` once. Declining keeps the complete derived layout without individual path questions. Accepting opens one grouped review of all five paths. Nothing is created until the consolidated path summary is approved.

## Install

### Claude Code on Linux

```text
mkdir -p ~/.claude/skills
ln -s /path/to/agency/skills/agency-setup ~/.claude/skills/agency-setup
```

### GitHub Copilot

The first-run launcher exposes the package-owned `agency-setup` skill to
Copilot automatically. A normal editable or wheel installation does not need a
project-local junction or user-global skill installation.

## Run

Invoke `agency-setup` after the first-run page launches it from the selected data root with the exact authoritative config path and supported AI integration. The skill uses that exact config path and selected integration unless the user explicitly approves another registered integration. If no config exists, it builds the complete candidate first and performs one revision-checked atomic write after approval and validation. If a candidate is invalid or superseded, report validation errors and stop; never invoke another skill or convert old layouts. The skill:

1. In guided mode, receives the approved data root from the launcher and asks for the project workspace as the first question. In manual mode, asks for the data root first and then the project workspace.
2. Inspects project instructions, source, tests, deployment, and available integrations without asking setup questions, then derives the canonical global paths.
3. Proposes reusable roles and asks how many agents to create plus which roles to create for the first team.
4. Approves the group ID, derives `groups/<group-id>`, and offers one optional grouped path override.
5. Plans Agent Skills, schedules, runtime policy, workspaces, and semantic memory for approval.
6. Resolves exactly one canonical config with only the supported root sections (`agency`, `memory`, and `groups`) and requires `agency.agent_library`, `agency.compilation_cache`, `agency.memory_store`, and `agency.prompt_store`.
7. Writes each approved blueprint with global `AGENTS.md` source. Blueprints may contain zero or more standard Agent Skills. For each approved routine capability, writes `.agents/skills/<skill>/SKILL.md`. Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities.
8. Registers explicit group-owned instances and every approved group workspace. Every instance pins a blueprint and integration; routines select scoped saved prompts and semantic memory selectors, and approved private prompts are registered for the instance when needed.
9. Validates group naming, storage paths, integrations, cross-references, and revision safety, performs one atomic config write, reparses from disk, and optionally verifies the singleton dispatcher.

## Result

After setup, the Agents page lists the configured group instances. Agent Detail provides `Profile/Blueprint/Runtime/Routines/Prompts/Memory/Activity`; identity is the config display name, title, and emoji. Agent Library owns reusable instructions and Agent Skills. Memory Channels own named shared memory. Group Settings continues to manage defaults only.

The skill reports the Agency data root, effective storage paths, blueprint keys, instance names, routines, memory scopes and channels, the authoritative config path, and scheduler status.
