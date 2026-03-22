<p align="center">
  <svg width="80" height="80" viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="80" height="80" rx="12" fill="#1e2a5e"/>
    <line x1="20" y1="60" x2="29" y2="42" stroke="white" stroke-width="2" opacity="0.3"/>
    <line x1="29" y1="42" x2="40" y2="18" stroke="white" stroke-width="2" opacity="0.3"/>
    <line x1="60" y1="60" x2="51" y2="42" stroke="white" stroke-width="2" opacity="0.3"/>
    <line x1="51" y1="42" x2="40" y2="18" stroke="white" stroke-width="2" opacity="0.3"/>
    <line x1="29" y1="42" x2="51" y2="42" stroke="white" stroke-width="2" opacity="0.3"/>
    <line x1="20" y1="60" x2="60" y2="60" stroke="white" stroke-width="1.5" opacity="0.15"/>
    <line x1="29" y1="42" x2="60" y2="60" stroke="white" stroke-width="1.5" opacity="0.12"/>
    <circle cx="40" cy="18" r="4.5" fill="white"/>
    <circle cx="29" cy="42" r="3.5" fill="white" opacity="0.8"/>
    <circle cx="51" cy="42" r="3.5" fill="white" opacity="0.8"/>
    <circle cx="20" cy="60" r="3.5" fill="white" opacity="0.6"/>
    <circle cx="60" cy="60" r="3.5" fill="white" opacity="0.6"/>
  </svg>
</p>

# Agency

A dashboard for managing AI agents that communicate through markdown files on your local machine.

**No database. No Docker. No build step.** Python + FastAPI + Tailwind CDN.

## Quick Start

```bash
pip install -e .
agency
```

On first run, a setup wizard walks you through pointing Agency at your agent directory. It auto-detects agents, creates the shared folder structure, and drops you into your Inbox.

Visit `http://localhost:8500`.

## How It Works

Your agents write observations to the filesystem as markdown files with YAML frontmatter. Agency reads those files and presents a pipeline:

1. **Clues** — agents observe something and write it down
2. **Curiosities** — observations converge into proposals worth considering
3. **Decisions** — you approve, defer, or reject through the UI
4. **Execution** — approved decisions auto-dispatch the proposing agent to do the work, with status tracking (success, success with exceptions, failed) and retry

Every item in the pipeline links to its upstream and downstream neighbors, so you can trace how an observation became an action.

## Features

- **Dispatch scheduling** — one-click setup, then configure `at` (daily) and `every` (recurring) rules per agent
- **Agent profiles** with identity, activity timeline, and health monitoring (green/amber/red pulse)
- **Inbox** that surfaces what needs your attention across all agents
- **Pipeline tracking** with clickable clue/curiosity/decision chains and auto-execution on approval
- **TTL enforcement** that auto-archives stale items
- **Document, memory, and prompt editing** in the browser
- **Light/dark mode** with system preference detection and persistent toggle
- **Multi-group support** for separate agent directories
- **Admin panel** for managing groups, agents, and dispatch schedules

## Add-on: Agency Setup Skill

Agency ships with a [Claude Code skill](skills/agency-setup/) that can bootstrap a fully functional agent team for **any** codebase. If you use Claude Code, install the skill and run `/agency-setup` from any project directory.

### Install

```bash
# Symlink into your Claude Code skills directory
ln -s /path/to/agency/skills/agency-setup ~/.claude/skills/agency-setup
```

### What it does

1. **Analyzes** your codebase — language, framework, structure, purpose
2. **Proposes** 3-5 agents tailored to the project (you approve or tweak)
3. **Generates** everything Agency needs to manage them:
   - Agent `CLAUDE.md` role definitions and `memory.md` files
   - `shared/` folder with clues, curiosities, decisions, logs, prompts
   - Dispatch prompts with project-specific observation tasks
   - `dispatch.sh` + systemd timer/service for automated runs
   - Tmux launch script with color-coded agent panes
4. **Registers** the new group with Agency (if Agency is installed)
5. **Enables** the dispatch timer so agents start running

The whole process is interactive but Claude-led — you can fly through it by saying "ok" at each step.

## Tech Stack

Python 3.11+ / FastAPI / Jinja2 / Tailwind CSS CDN / 6 dependencies / No JS framework

## Documentation

See the [`kb/`](kb/) folder:

- [Directory Structure](kb/directory-structure.md) — expected agent group layout
- [Agent Identity](kb/agent-identity.md) — display names, titles, avatars
- [Data Formats](kb/data-formats.md) — clue, curiosity, and decision frontmatter
- [Configuration](kb/configuration.md) — config.yaml reference
- [Deployment](kb/deployment.md) — running as a systemd service
- [Dispatch](kb/dispatch.md) — automatic agent scheduling

## License

MIT
