# Agency Setup Skill

The `agency-setup` skill owns the one authoritative canonical Agency config. After the user chooses a project folder and supported AI integration, the skill takes over group naming, storage paths, blueprint source, explicit instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write. It accepts only the canonical config shape, creates the config when absent, and reports validation errors directly. It does not create runtime-native identities, physical agent directories, memory files, prompt schedules, or conversion surfaces.

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

Invoke `agency-setup` from the project workspace after the first-run page launches it with the project folder and supported AI integration. If no config exists, create the canonical config at the authoritative path. If a candidate is invalid or superseded, report validation errors and stop; never invoke another skill or convert old layouts. The skill:

1. Inspects project instructions, source, tests, deployment, and available integrations.
2. Proposes reusable roles, Agent Skills, schedules, runtime policy, workspaces, and semantic memory for approval.
3. Resolves exactly one config and accepts only `schema_version: 2` with `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store`.
4. Writes each approved blueprint as global `AGENTS.md` source plus standard Agent Skills under `.agents/skills/<skill>/SKILL.md`.
5. Registers explicit group-owned instances. Every instance pins a blueprint and integration; routines select skills and semantic memory selectors.
6. Validates group naming, storage paths, cross-references, and revision safety, writes atomically, reparses from disk, and optionally verifies the singleton dispatcher.

## Result

After setup, the Agents page lists the configured group instances. Agent Detail provides `Profile/Blueprint/Runtime/Routines/Memory/Activity`; identity is the config display name, title, and emoji. Agent Library owns reusable instructions and Agent Skills. Memory Channels own named shared memory. Group Settings continues to manage defaults only.

The skill reports blueprint keys, instance names, routines, memory scopes and channels, the authoritative config path, and scheduler status.
