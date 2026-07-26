# Agency Setup Skill

The `agency-setup` skill owns the one authoritative canonical Agency config. After the user chooses a project folder and supported AI integration, the skill takes over group naming, storage paths, blueprint source, explicit instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write. It accepts only the canonical config shape, creates the config when absent, and reports validation errors directly. It does not create runtime-native identities, physical agent directories, memory files, prompt schedules, or conversion surfaces.

Every generated config uses `schema_version: 3` and requires `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store`. Each group has both `workspace_path` (the execution workspace and source repository) and `path` (the Agency-owned group root). The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`; durable jobs live in `agency.memory_store/.jobs`, and operation locks live in `<group.path>/locks`.

## Agency Data Root

After read-only project inspection, the first question asks for one Agency data root. This is a separate home for reusable agent blueprints, disposable compiled projections, semantic memory and durable jobs, and per-group records. The project workspace remains source code and execution context, while `config.yaml` remains at its authoritative path.

The root may be an existing directory or a new absolute path whose nearest existing parent is a writable real directory that can safely create it. For a root at `C:/Agency` and group ID `example`, setup derives:

```text
C:/Agency/
|-- agent-library/       -> C:/Agency/agent-library
|-- compiled-agents/     -> C:/Agency/compiled-agents
|-- memory/              -> C:/Agency/memory
`-- groups/example/      -> C:/Agency/groups/example
```

Users may enter home syntax such as `~/Agency`; setup expands it to the user's home directory before deriving and storing the canonical paths.

Setup then asks `Customize the derived storage paths?` once. Declining keeps the complete derived layout without individual path questions. Accepting opens one grouped review of all four paths. Nothing is created until the consolidated path summary is approved.

## Install

### Claude Code on Linux

```text
mkdir -p ~/.claude/skills
ln -s /path/to/agency/skills/agency-setup ~/.claude/skills/agency-setup
```

### GitHub Copilot on Windows

Expose the canonical skill at `.github\skills\agency-setup`. A junction keeps one source copy:

```powershell
New-Item -ItemType Directory -Force .github\skills | Out-Null
New-Item -ItemType Junction `
  -Path .github\skills\agency-setup `
  -Target C:\path\to\agency\skills\agency-setup | Out-Null
```

## Run

Invoke `agency-setup` from the project workspace after the first-run page launches it with the project folder, exact authoritative config path, and supported AI integration. The skill uses that exact config path and selected integration unless the user explicitly approves another registered integration. If no config exists, it builds the complete candidate first and performs one revision-checked atomic write after approval and validation. If a candidate is invalid or superseded, report validation errors and stop; never invoke another skill or convert old layouts. The skill:

1. Inspects project instructions, source, tests, deployment, and available integrations without asking setup questions.
2. Asks for the Agency data root as the first question and derives the canonical global paths.
3. Proposes reusable roles and asks how many agents to create plus which roles to create for the first team.
4. Approves the group ID, derives `groups/<group-id>`, and offers one optional grouped path override.
5. Plans Agent Skills, schedules, runtime policy, workspaces, and semantic memory for approval.
6. Resolves exactly one canonical config with only the supported root sections (`agency`, `memory`, and `groups`) and requires `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store`.
7. Writes each approved blueprint with global `AGENTS.md` source. Blueprints may contain zero or more standard Agent Skills. For each approved routine capability, writes `.agents/skills/<skill>/SKILL.md`. Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities.
8. Registers explicit group-owned instances and every approved group workspace. Every instance pins a blueprint and integration; routines select skills and semantic memory selectors.
9. Validates group naming, storage paths, integrations, cross-references, and revision safety, performs one atomic config write, reparses from disk, and optionally verifies the singleton dispatcher.

## Result

After setup, the Agents page lists the configured group instances. Agent Detail provides `Profile/Blueprint/Runtime/Routines/Memory/Activity`; identity is the config display name, title, and emoji. Agent Library owns reusable instructions and Agent Skills. Memory Channels own named shared memory. Group Settings continues to manage defaults only.

The skill reports the Agency data root, effective storage paths, blueprint keys, instance names, routines, memory scopes and channels, the authoritative config path, and scheduler status.
