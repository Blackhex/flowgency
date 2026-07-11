# Agency Setup Skill

Agency ships with an interactive skill that bootstraps a fully functional agent team for
an existing codebase. It currently generates one of two host profiles:

| Profile | Identity | Dispatch | Workspace |
|---|---|---|---|
| Claude on Linux | `CLAUDE.md` | Bash and user systemd | tmux panes |
| GitHub Copilot on Windows | `AGENTS.md` plus `.copilot/` | PowerShell and Task Scheduler | Windows Terminal tabs |

## Install

### Claude Code on Linux

```bash
# Create the skills directory if it doesn't exist
mkdir -p ~/.claude/skills

# Symlink the skill, replacing the path with wherever you cloned Agency
ln -s /path/to/agency/skills/agency-setup ~/.claude/skills/agency-setup
```

### GitHub Copilot on Windows

Expose the canonical skill directory at `.github\skills\agency-setup` in the project
where Copilot should discover it. This repository already includes that discovery link.
For another local project, a directory junction avoids duplicating the skill:

```powershell
New-Item -ItemType Directory -Force .github\skills | Out-Null
New-Item -ItemType Junction `
  -Path .github\skills\agency-setup `
  -Target C:\path\to\agency\skills\agency-setup | Out-Null
```

## Usage

From the project directory, invoke `agency-setup` in the active agent chat. In Claude
Code, run:

```
/agency-setup
```

On Windows, the skill detects PowerShell and the installed Copilot CLI automatically.
It asks only when multiple runtimes make the intended profile ambiguous.

## What It Does

1. **Analyzes your codebase** - language, framework, structure, tests, deployment, and purpose
2. **Proposes a lean agent team** tailored to the project - you approve or revise ownership
3. **Generates everything Agency needs:**
   - Agent role definitions and memory files
   - `shared/` folder with observations, proposals, decisions, logs, prompts
   - Dispatch prompts with project-specific observation tasks
   - Platform-native dispatcher, scheduler installer, and runtime workspace
4. **Registers the new group** with Agency (if installed)
5. **Enables the dispatch timer** so agents start running on schedule

## Verification and Safety

The skill verifies generated agent detection and, on Windows, resolves and starts the
real `copilot.exe` rather than accepting package-manager wrappers. Registration uses a
YAML parser and atomic replacement, preserves unrelated settings, and reparses the file
after dashboard reload so concurrent writes or normalization drift cannot pass unnoticed.

Generated dispatchers enforce a 15-run daily limit, event-marker deduplication,
per-agent logs, and a 300-second timeout. Scheduler installation is optional and uses
the current user without storing credentials or weakening execution policy.
