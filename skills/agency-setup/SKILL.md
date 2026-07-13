---
name: agency-setup
description: >
  Set up a fully functional agent team for any codebase with Agency-compatible
  structure. Use when 'agency setup', 'set up agents', 'create agent team',
  'bootstrap agents', 'add agents to this project', or setting up agent
  infrastructure for a repository. Creates agents/, shared/, prompts, runtime
  workspace, and optionally registers with Agency dashboard.
user_invocable: true
---

# Agency Setup

Interactive, assistant-led skill that analyzes a codebase and sets up a fully functional
agent team. The assistant does most of the suggesting — the user approves with "ok" or
tweaks.

## Runtime and Platform Selection

Before Phase 1, detect the host OS, active shell, and available agent CLI. Do not ask
when these are unambiguous. Use these profiles throughout generation:

| Profile | Identity file | Agent command | Workspace |
|---------|---------------|---------------|-----------|
| Claude/Linux | `CLAUDE.md` | `claude --dangerously-skip-permissions` | tmux |
| Copilot/Windows | `AGENTS.md` | `copilot --autopilot --experimental` | Windows Terminal/PowerShell |

- On Linux, preserve the existing Claude/Linux behavior unless another runtime is
  explicitly requested.
- On Windows with GitHub Copilot available, use the Copilot/Windows profile.
- If both or neither CLI is available and the intended runtime is unclear, ask one
  concise question before generating files.
- Never emit Bash commands as instructions to run in PowerShell. Use PowerShell cmdlets
  and Windows paths for the Windows profile.

## Phase 1: Analyze the Codebase

Gather context automatically (no user input). Read whichever of these exist:

1. **Project identity**: CLAUDE.md, AGENTS.md, .github/copilot-instructions.md,
   README.md, README, docs/
2. **Language/framework**: package.json, pyproject.toml, go.mod, Cargo.toml, Gemfile,
   requirements.txt, pom.xml, build.gradle, Makefile
3. **Structure**: list the project root and glob for key patterns (`src/`, `lib/`,
   `app/`, `tests/`, `scripts/`, `templates/`, `config/`). Use `Get-ChildItem` in
   PowerShell and `ls` on Linux.
4. **Git context**: `git log --oneline -15` for recent activity, `git remote -v` for origin
5. **Existing agents**: Check if `agents/` already exists (abort if fully populated)
6. **Deployment**: Check for Dockerfile, Containerfile, docker-compose, systemd units,
   Windows service/task scripts, and CI/CD configs (.github/workflows/,
   .gitlab-ci.yml)

From this, determine:
- **Language** and **framework** (e.g., Python/FastAPI, TypeScript/Next.js, Go/stdlib)
- **Project purpose** (web app, CLI tool, library, API, pipeline, etc.)
- **Complexity** (file count, directory depth, number of modules)
- **Deployment model** (container, systemd, serverless, library/package)
- **Testing setup** (test framework, test directory, CI)

Present a 3-4 sentence summary of what you found. Then proceed to Phase 2.

## Phase 2: Propose Agent Team

Based on the analysis, propose 3-5 agents. Present as a table:

```
| Agent | Role | Owns | Permissions | Dispatch |
|-------|------|------|-------------|----------|
| product | PM & Developer | codebase, features | edit code | evening |
| maintainer | Upkeep & Ops | service health, config | read-only | morning, cleanup |
| strategist | Vision & Direction | product roadmap | read-only | morning |
```

Below the table, give a 1-2 sentence rationale for each agent explaining WHY this
project needs this role specifically.

**Guidelines for agent design:**

- Every project needs a **builder** agent (can edit code)
- Most projects benefit from a **maintainer** agent (health checks, quality)
- Projects with a product direction benefit from a **strategist/advisor** agent
- Large projects may need domain specialists (e.g., frontend + backend, or data + API)
- Keep the team lean — 3 agents is often enough, 5 is the practical max
- Each agent must have a distinct, non-overlapping domain
- Only ONE agent should have code-edit permissions (the builder)

**End Phase 2 by asking:** "Does this team look right? You can add, remove, or rename
agents, or say 'ok' to proceed."

## Phase 3: Quick Customization

For each agent, ask ONE question:

> "{Agent name} will observe {default tasks}. Any specific observation tasks to add?
> (Enter to use defaults)"

Default observation tasks by archetype:
- **Builder**: code quality scan, documentation drift, template/route consistency
- **Maintainer**: service health, config validation, dependency audit, log errors
- **Strategist**: product review, feature gap analysis, landscape comparison
- **Domain specialist**: domain-specific metrics and patterns

If the user presses Enter or says "ok"/"defaults", use the defaults. This phase should
take seconds, not minutes.

## Phase 4: Generate Everything

Read the templates from `references/` before generating:
- `references/templates.md` — `CLAUDE.md`/`AGENTS.md`, memory.md, shared memory
- `references/dispatch-templates.md` — Bash/systemd/tmux and
  PowerShell/Task Scheduler/Windows Terminal templates
- `references/observation-system-steps.md` — universal observation pipeline steps

### 4.1 Directory Structure

Create the same directories on every platform. Use the commands for the selected
profile.

Claude/Linux:

```bash
mkdir -p agents/{agent1,agent2,...}
mkdir -p agents/shared/{observations,proposals,decisions,logs,prompts}
```

Copilot/Windows (PowerShell):

```powershell
$agentNames = @('{agent1}', '{agent2}')
$agentNames | ForEach-Object {
  New-Item -ItemType Directory -Force "agents/$_" | Out-Null
  New-Item -ItemType Directory -Force "agents/$_/.copilot" | Out-Null
}
@('observations', 'proposals', 'decisions', 'logs', 'prompts') |
  ForEach-Object { New-Item -ItemType Directory -Force "agents/shared/$_" | Out-Null }
```

### 4.2 Agent Files

For each agent, generate from the templates in `references/templates.md`:
- Claude/Linux: `agents/{agent}/CLAUDE.md`
- Copilot/Windows: `agents/{agent}/AGENTS.md`
- Copilot/Windows: `agents/{agent}/.copilot/` - required detection marker that
  distinguishes Copilot from Codex, which also uses `AGENTS.md`.
- Fill the selected identity file with project-specific context. Adapt template labels,
  host details, commands, path separators, and boundaries to the selected profile; do
  not claim the host is Fedora or the runtime is Claude when generating for Windows.
- `agents/{agent}/memory.md` — Standard memory template

Plus shared files:
- `agents/shared/memory.md` — Project context, preferences, learned facts
- `agents/shared/prompts/_observation-system-steps.md` — Copy from `references/observation-system-steps.md`

After generation, verify every Copilot agent is detectable. When Agency's Python
package is importable, assert `detect_integration(agent_dir).name == "copilot"` for
each agent directory. Otherwise verify both `agents/{agent}/.copilot/` and
`agents/{agent}/AGENTS.md` exist for every Copilot agent.

For Copilot/Windows, also verify the real executable before declaring generation
complete. Enumerate all command candidates rather than accepting the first wrapper:

```powershell
$copilotExe = @(Get-Command copilot -All -ErrorAction SilentlyContinue) |
  Where-Object {
    $_.Source -and [System.IO.Path]::GetExtension($_.Source) -ieq '.exe'
  } |
  Select-Object -First 1
if (-not $copilotExe) { throw 'GitHub Copilot CLI copilot.exe was not found on PATH.' }
& $copilotExe.Source --version
if ($LASTEXITCODE -ne 0) { throw 'GitHub Copilot CLI executable validation failed.' }
```

Do not treat a `.ps1`, `.bat`, or `.cmd` result as successful executable validation;
multiple package-manager wrappers may appear on `PATH` before the real binary.

### 4.3 Dispatch Prompts

For each agent, generate a dispatch prompt at `agents/shared/prompts/{agent}-routine.md`.
Follow the structure in `references/dispatch-templates.md`:
- Section 0: Load memory
- Numbered observation tasks (from Phase 3)
- Pre-approved actions
- Boundaries
- Update memory
- Observation system steps reference

If the maintainer/cleanup agent exists, also generate `{agent}-cleanup.md`.

### 4.4 Schedule Definitions

Do not generate a dispatcher, Task Scheduler installer, systemd unit, launchd
plist, or project-specific scheduler artifact. Agency's global 15-minute
heartbeat runs schedule rules stored in the singleton dashboard config.

Record each approved Phase 2 dispatch assignment for Phase 4.7:

- `morning` creates the routine rule at `"07:00"`.
- `evening` creates the routine rule at `"21:00"`.
- `morning, evening` creates both routine rules.
- A generated cleanup prompt creates an additional cleanup rule at `"21:00"`.
- All `at` values use the scheduler host's local time.

Use `dispatch.timeout: 300` and `dispatch.daily_limit: 15`. Marker
deduplication, logs, timeout enforcement, and job lifecycle belong to Agency's
Python dispatcher and job system.

### 4.5 Runtime Workspace

Generate one surface per agent plus one plain terminal using the selected profile:

| Profile | Launcher | Surface | Agent command |
|---------|----------|---------|---------------|
| Claude/Linux | `agents/shared/tmux-agents.sh` | One labeled, color-coded tmux pane per agent plus a terminal pane | `claude --dangerously-skip-permissions` |
| Copilot/Windows | `agents/shared/start-agents.ps1` | One Windows Terminal tab per agent plus a PowerShell tab | `copilot --autopilot --experimental` |

- **Claude/Linux:** Calculate the tmux grid from the pane count and make the launcher
  executable with `chmod +x`.
- **Copilot/Windows:** Set each tab's working directory to `agents/{agent}`. If
  `wt.exe` is unavailable, open separate PowerShell processes. Use argument arrays and
  safe path quoting; do not interpolate untrusted text into commands. Resolve the real
  `copilot.exe` in the launcher process and pass its absolute path to each child via a
  safely encoded PowerShell command; do not rely on a child shell or an existing Windows
  Terminal process inheriting the current `PATH`.

### 4.6 Gitignore

Add `agents/` to `.gitignore` if not already present:

Claude/Linux:

```bash
grep -qxF 'agents/' .gitignore 2>/dev/null || echo 'agents/' >> .gitignore
```

Copilot/Windows (PowerShell): read `.gitignore` when present and append `agents/` only
when no line exactly matches it. Use `Get-Content` and `Add-Content`; do not overwrite
existing entries.

### 4.7 Agency Registration

Agency supports exactly one Agency dashboard and one authoritative `config.yaml`
per OS user. `$AGENCY_CONFIG` wins when valid. If more than one remaining valid
candidate exists, ask which config is authoritative; never register or schedule
all candidates.

Build an ordered, de-duplicated list of Agency config candidates:
1. `$AGENCY_CONFIG`, when set (explicit override)
2. `{project_root}/config.yaml` (the project being set up may itself be Agency)
3. Common platform paths:
   - Linux: `~/agency/config.yaml`
   - Windows: `$HOME\agency\config.yaml` and `$HOME\Projects\agency\config.yaml`

Do not assume that every `config.yaml` belongs to Agency. Parse each existing candidate
with a YAML parser, not regex or line editing. A workspace-local candidate is valid only
when the document is a mapping with a top-level `groups` mapping and either a top-level
`agency` mapping or at least one group containing both `name` and `path`. Skip malformed
or unrelated candidates. The explicit `$AGENCY_CONFIG` wins when valid; otherwise prefer
the valid workspace-local config. If multiple remaining candidates are valid, ask which
one to use. If the user declines, skips, or supplies an invalid selection, skip
registration and scheduler setup; never pick one implicitly. If none are valid, skip
registration silently and do not create a new Agency config file.

If found, ask: "Register this agent group with Agency dashboard at `{config_path}`? (Y/n)"

If yes:
- Resolve `{project_root}/agents` and all configured group paths to canonical absolute
  paths. If a group already points to that agents directory, update that group in place
  and preserve its key and unrelated settings. This makes registration idempotent and
  replaces stale agent lists rather than creating duplicate groups.
- Otherwise derive a group key from the project directory name (lowercase, hyphens) and
  add it under `groups:`. If that key already points elsewhere, do not overwrite it;
  ask for a different key.
- Set the group's `name`, absolute `path` to `{project_root}/agents`, and complete agents
  list. Preserve unrelated top-level config and unrelated groups.
- Set `default_integration: claude-code` for Claude/Linux or
  `default_integration: copilot` for Copilot/Windows
- Write agents in dict form with each agent's selected `integration` and explicit
  `capabilities.write`. Implementation/builder roles set `capabilities.write: true`;
  observational/advisory/sentinel roles set `capabilities.write: false`. Never infer
  write authority for an existing agent — preserve its current explicit value if it has
  one. For newly generated roles that are ambiguous (neither clearly a builder nor
  clearly read-only), ask the user when a newly generated role is ambiguous before
  writing the config. Example:
  ```yaml
  agents:
    - name: builder
      integration: claude-code
      capabilities:
        write: true
    - name: advisor
      integration: claude-code
      capabilities:
        write: false
    - name: sentinel
      integration: claude-code
      capabilities:
        write: false
  ```
- Upsert the generated `workspaces` entry without deleting unrelated entries. Use type
  `tmux` with the absolute `tmux-agents.sh` path on Linux. On Windows, use type `custom`
  with `config_path` set to the absolute `start-agents.ps1` path, `language: text`, and
  `launch_cmd` set to a safely quoted per-process PowerShell invocation of that script.
- **Write dispatch config** directly from Phase 2 dispatch assignments:
  - Set `dispatch.enabled: true`, `dispatch.timeout: 300`, `dispatch.daily_limit: 15`
  - For each agent with a Phase 2 dispatch assignment, add rules under `dispatch.agents`:
    ```yaml
    dispatch:
      enabled: true
      timeout: 300
      daily_limit: 15
      agents:
        morning-agent:
        - prompt: morning-agent-routine.md
          at: "07:00"
        cleanup-agent:
        - prompt: cleanup-agent-routine.md
          at: "21:00"
        - prompt: cleanup-agent-cleanup.md
          at: "21:00"
    ```
  - Use `"07:00"` for morning assignments and `"21:00"` for evening assignments
  - Preserve assignment order and de-duplicate identical prompt/time pairs
  - If an assignment has a code condition (e.g., only runs when a DB check passes), add
    `condition: condition-name` to the rule. A rule with `condition` is skipped by
    Agency's Python dispatcher and runs only when triggered by external code; it remains
    read-only in the UI.
- Write the parsed config atomically (temporary file plus replace), then parse it again
  and verify every generated agent name, integration, workspace, and dispatch rule.
  Immediately before replace, detect whether the source file changed since it was read.
  If it changed, re-read the latest document and reapply only the intended group merge to
  preserve concurrent changes; never overwrite with a stale pre-reload object.
- If a running Agency dashboard uses this config, reload or restart it with its existing
  non-elevated service/process mechanism and verify that the group page shows the expected
  agent count. Do not terminate an unknown process; if a safe reload mechanism cannot be
  identified, report that a dashboard restart is required and provide the exact command.
- After dashboard reload and HTTP verification, parse the config from disk again and
  re-verify dict-form agent integrations, workspace configuration, and every dispatch
  rule. A rendered page is insufficient because normalized shorthand can look equivalent.
  If generated fields drifted, re-read the latest config, reapply only those fields while
  preserving unrelated changes, atomically replace it, then reload and verify once more.
  If the second verification still drifts, stop and report the competing writer instead
  of retrying indefinitely.

### 4.8 Singleton Scheduler Setup

Only offer scheduler setup after registration and on-disk verification succeed.
If no authoritative Agency config was found, report that registration and
scheduling were not completed and do not create a fallback project scheduler.

Ask: "Enable the global Agency dispatcher? It checks all enabled groups every 15
minutes. (Y/n)"

If yes:

1. Resolve the selected config to a canonical absolute path.
2. Run `christag-agency dispatch install --config "{config_path}"` as the current user.
3. Run `christag-agency dispatch status --config "{config_path}"`.
4. Treat only exit status 0 as verified active scheduling.
5. If install reports another config, ask before rerunning with `--replace`.
6. Never request credentials, elevation, or a weaker execution policy.
7. If the CLI is unavailable, report the exact command to run after Agency is
   installed; do not generate another scheduler implementation.

## Phase 5: Summary

Print a summary of everything created:
- Number of agents and their names
- File count
- Agency registration status (config path if registered)
- Global dispatcher status (active/inactive/not configured)
- Launch command: `agents/shared/tmux-agents.sh` on Claude/Linux or
  `.\agents\shared\start-agents.ps1` on Copilot/Windows

## Important Notes

- If `agents/` already exists with content, warn the user and ask before overwriting
- All generated files use the project's actual context (language, framework, paths)
- Agent identity files should reference the applicable root instructions: `CLAUDE.md`
  for Claude Code, or `AGENTS.md` and `.github/copilot-instructions.md` for Copilot
- Dispatch prompts should use project-appropriate commands (npm test vs pytest vs go test)
- The builder agent's boundaries should match the project's actual structure
- Preserve CRLF when modifying existing Windows-native scripts; new Markdown files may
  use the repository's existing line-ending convention
