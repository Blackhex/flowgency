---
name: agency-setup
description: >
  Set up a fully functional agent team for any codebase with Agency-compatible
  structure. Use when 'agency setup', 'set up agents', 'create agent team',
  'bootstrap agents', 'add agents to this project', or setting up agent
  infrastructure for a repository. Creates agents/, shared/, dispatch, runtime
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

| Profile | Identity file | Agent command | Scripts | Scheduler | Workspace |
|---------|---------------|---------------|---------|-----------|-----------|
| Claude/Linux | `CLAUDE.md` | `claude --dangerously-skip-permissions` | Bash (`.sh`) | user systemd | tmux |
| Copilot/Windows | `AGENTS.md` | `copilot --autopilot --experimental` | PowerShell (`.ps1`) | Task Scheduler | Windows Terminal/PowerShell |

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

### 4.4 Dispatch Infrastructure

Generate the selected profile from `references/dispatch-templates.md`. Both profiles
must provide morning and evening events, a 15-run daily limit, event-marker
deduplication, per-agent stdout/stderr logs, dry-run support, a 300-second agent
timeout, proposal routing, and an idempotent scheduler setup.

| Profile | Dispatcher | Scheduler artifacts | Agent invocation |
|---------|------------|---------------------|------------------|
| Claude/Linux | `agents/shared/dispatch.sh` | `{project}-dispatch.timer` and `{project}-dispatch.service` | `claude --dangerously-skip-permissions -p "$prompt"` |
| Copilot/Windows | `agents/shared/dispatch.ps1` | `install-dispatch.ps1` registering `{project}-dispatch` | `copilot -p $prompt --autopilot --experimental` |

Use the project directory name as `{project}`. Apply these profile-specific execution
requirements:

- **Claude/Linux:** Generate the user-systemd timer and oneshot service for 7am and
  9pm ET. Make `dispatch.sh` executable with `chmod +x`.
- **Copilot/Windows:** Use PowerShell/.NET path and filesystem APIs. Invoke the real
  `copilot.exe` rather than a wrapper during headless dispatch. Use `Start-Process`
  with redirected output and timeout enforcement; never use `Invoke-Expression`.
  Windows does not use executable mode bits.

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
one to use. If none are valid, skip registration silently and do not create a new Agency
config file.

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
- Write agents in dict form with each agent's selected `integration`
- Upsert the generated `workspaces` entry without deleting unrelated entries. Use type
  `tmux` with the absolute `tmux-agents.sh` path on Linux. On Windows, use type `custom`
  with `config_path` set to the absolute `start-agents.ps1` path, `language: text`, and
  `launch_cmd` set to a safely quoted per-process PowerShell invocation of that script.
- **Write dispatch config** derived from the generated platform dispatch script's event
  handlers:
  - Set `dispatch.enabled: true`, `dispatch.timeout: 300`, `dispatch.daily_limit: 15`
  - For each agent→prompt mapping in the generated dispatch script, add a rule under
    `dispatch.agents`:
    ```yaml
    dispatch:
      enabled: true
      timeout: 300
      daily_limit: 15
      agents:
        agent-name:
        - prompt: agent-name-routine.md
          at: "07:00"        # From the morning event handler time
        - prompt: agent-name-cleanup.md
          at: "21:00"        # From the evening event handler time
    ```
  - The `at` time should match the midpoint of the dispatch script's time window for
    that event
  - If an assignment has a code condition (e.g., only runs when a DB check passes), add
    `condition: condition-name` to the rule — these display as read-only in the UI
  - This keeps config.yaml in sync with the platform dispatch script so the Agency
    dashboard shows accurate schedules
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

### 4.8 Scheduler Setup

For Claude/Linux, ask: "Enable dispatch timer? This will run agents at 7am and 9pm ET
daily. (Y/n)"

If yes:
- Symlink timer and service to `~/.config/systemd/user/`
- Run `systemctl --user daemon-reload`
- Run `systemctl --user enable --now {project}-dispatch.timer`
- Show the next fire time

For Copilot/Windows, ask: "Enable dispatch scheduling? This will run agents at 7am and
9pm daily. (Y/n)"

If yes:
- Explain that Task Scheduler uses the Windows host's local time zone. If the host is
  not in Eastern Time, ask whether `07:00`/`21:00` should be local time or converted
  from ET before registering triggers.
- Run `agents/shared/install-dispatch.ps1` in PowerShell without requesting elevation.
- Register the task for the current user only and do not store or request credentials.
- Show the registered task and its next run time with `Get-ScheduledTask` and
  `Get-ScheduledTaskInfo`.
- If ScheduledTasks cmdlets are unavailable or registration requires elevation, leave
  the generated installer in place and report the exact manual command instead of
  weakening execution policy globally.

## Phase 5: Summary

Print a summary of everything created:
- Number of agents and their names
- File count
- Dispatch schedule (if enabled)
- Agency registration status
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
