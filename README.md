# Agency

Agency is a FastAPI control plane for reusable AI agent blueprints, group-owned agent instances, scheduled routines, semantic memory, durable jobs, and an observation-to-decision pipeline. It supports multiple LLM integrations without making runtime-native agent folders authoritative.

## Install

Agency requires Python 3.11 or newer.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m agency.app
```

On POSIX, use `.venv/bin/python`. The dashboard listens on `http://127.0.0.1:8500` by default. Set `AGENCY_CONFIG` to select the one authoritative config.

## Strict canonical model

Agency accepts only `schema_version: 2`. `config.yaml` owns groups, explicit instances, runtime policy, routines, integration selection, identity, capabilities, and semantic memory selectors. See [config.yaml.example](config.yaml.example).

Global paths under `agency` separate reusable and mutable data:

- `agent_library` contains standards-based blueprints.
- `compilation_cache` contains disposable immutable runtime projections.
- `memory_store` contains semantic mutable Markdown memories.

Each immediate Agent Library child is a blueprint with `AGENTS.md` and optional standard Agent Skills under `.agents/skills/<skill>/SKILL.md`. A group instance explicitly selects one blueprint and one integration. Runtime projectors may relocate source files into native layouts, but source bytes remain unchanged.

Group runtime values are defaults. Agent `runtime.sandbox.additional_roots` are additive to group roots. A present agent tool policy is a complete override, with mode `all`, `allowlist`, or `none`. It is never merged with the group tool list.

Routines replace prompt-file schedules. Each routine has a stable ID, selects a standard Agent Skill, defines one schedule, and may select semantic memory. Supported memory scopes are `run`, `routine`, `agent`, `group`, and globally declared `channel`.

## Surfaces

- Agent Library manages reusable `AGENTS.md` and Agent Skills.
- Agents is the sole group roster; Agent Detail is the sole instance editor.
- Memory Channels and Agent Detail expose semantic memory.
- Jobs shows queued, waiting, running, completed, failed, and cancelled execution.
- Observations, proposals, decisions, logs, and workspaces remain group-scoped.

Workspace launchers are optional convenience frontends. They operate from the configured group workspace and configured instance list; they do not own agent configuration.

## Migration

Runtime startup never parses or rewrites superseded configuration. Preview and review a standalone migration plan before applying it:

```text
python tools/migrate_agent_model.py preview --config config.yaml --plan migration-plan.yaml
python tools/migrate_agent_model.py apply --plan migration-plan.yaml
python tools/migrate_agent_model.py verify --config config.yaml
python tools/migrate_agent_model.py rollback --plan migration-plan.yaml
```

The migration utility alone may read superseded native definitions, prompt assignments, per-agent memory, `tmux_config`, and superseded `.agency-meta.yaml` source history. It copies source data and leaves superseded directories untouched.

## Development

```text
.venv/Scripts/python -m pytest tests/ -q
```

Use `christag-agency dispatch install --config <path>` to install the singleton platform scheduler and `christag-agency dispatch status --config <path>` to verify it.<p align="center">
  <img src="screenshots/logo.svg" width="80" height="80" alt="Agency logo">
</p>

<h1 align="center">Agency</h1>

<p align="center">
  One dashboard for all your AI agents — no matter what tool they run on.
</p>

<p align="center">
  <a href="https://github.com/christag/agency/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg" alt="AGPL-3.0 License"></a>
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/tests-471%20passing-brightgreen.svg" alt="471 tests passing">
  <img src="https://img.shields.io/badge/database-none-orange.svg" alt="No database">
  <img src="https://img.shields.io/badge/docker-not%20required-orange.svg" alt="No Docker required">
</p>

---

Agency is an open-source management dashboard for AI agent teams. It connects to 9 LLM tools — Claude Code, Codex, Gemini, Aider, Goose, OpenCode, Pi, custom scripts, and SDK agents — and gives you a unified pipeline to see what your agents observe, review their proposals, make decisions, and confirm the outcomes. It currently manages 21 agents across 3 groups in production. Everything is stored as markdown files with YAML frontmatter. No database, no Docker, no build step. 471 tests passing.

<p align="center">
  <img src="screenshots/inbox.png" width="800" alt="Agency mission control dashboard with fleet status, pipeline pulse, attention queue, and activity feed">
</p>

## Who is this for?

Agency is for people who treat AI agents like team members on a project — not disposable tools you spin up and throw away.

If you have a codebase where an agent handles docs, another watches for quality issues, and a third manages releases, those agents need persistent identities, accumulated knowledge, and a structured way to surface what they've found. Agency gives them that. Each agent has a name, a role, a memory, and a history. They live in your project long-term, like virtual employees with specific responsibilities.

**This is not an agent runner.** Tools like [Superset](https://superset.sh) are great at spinning up parallel workspaces, streaming real-time output, and managing active coding sessions. Agency sits above that layer. It's the coordination brain — deciding *what* agents should work on, reviewing *what they found*, keeping a record of *what was decided*, and confirming *whether the outcome actually satisfied the intent*. Agency dispatches intent and governs the result. Your runner of choice executes it.

If you have multiple projects, each with their own agent team, Agency manages all of them from one dashboard. Same pipeline, same governance, separate groups.

## What problem does Agency solve?

You have AI agents. Maybe they use Claude Code. Maybe Codex, Gemini, Aider, OpenCode, Pi, or something custom. They run in different directories, produce output in different ways, and you're alt-tabbing between terminals to figure out what's happening.

Agency gives you a single place to see what your agents are doing, what they've found, and what they need from you.

## What do you get?

### A pipeline that turns agent noise into action

Agents write down what they notice. Agency organizes those observations into a pipeline:

1. **Observe** — an agent spots something and writes it down
2. **Propose** — observations converge into a proposal worth considering
3. **Decide** — you approve, defer, or reject right from the dashboard
4. **Execute** — approved decisions auto-dispatch the agent to do the work
5. **Verify** — confirm the outcome satisfied the proposal, or open a linked follow-up when it didn't

Every item links to what came before and after, so you can always trace how an observation became an action — and whether that action actually resolved what it set out to.

<p align="center">
  <img src="screenshots/proposal-detail.png" width="800" alt="Proposal detail showing the full pipeline chain from observation to decision">
</p>

> Agency's pipeline is inspired by the ship log in Outer Wilds. [Read the design story.](kb/design-inspiration.md)

### A mission control dashboard

The home screen gives you everything at a glance: which agents are healthy, what's moving through the pipeline, what needs your attention right now, and recent activity across your fleet.

Approve, defer, or reject proposals inline — no clicking through to separate pages unless you want the details.

### Which LLM tools does it support?

Agency supports **Claude Code, OpenAI Codex, Google Gemini, Aider, Goose, OpenCode, Pi, custom scripts**, and an SDK mode for agents you run yourself. Different agents in the same group can use different tools — Agency handles the differences.

<p align="center">
  <img src="screenshots/agents.png" width="800" alt="Agent list showing different integration badges">
</p>

### Agent profiles with personality

Each agent gets a profile page with a name, title, emoji avatar, optional headshot, activity timeline, and health status. You can see at a glance who's been active, who's gone quiet, and what each agent has been working on.

<p align="center">
  <img src="screenshots/agent-profile.png" width="800" alt="Agent profile with identity, timeline, and schedule">
</p>

### How does scheduling work?

Set agents to run on schedules — daily at a specific time, or every few hours. Agency installs a lightweight system timer (no Docker, no cron hacks) and handles the rest. Each run gets logged so you can see exactly what happened.

### Agent jobs

Scheduled prompts, manual prompt runs, approved decisions, and decision retries all create durable records under `<group>/shared/jobs/`. On Linux with a user systemd manager, each job runs as a transient user systemd service (`systemd-run --user`), which ensures the worker continues even if the Agency service itself is restarted. On other platforms (macOS, Windows) or when systemd is unavailable, jobs run as detached subprocesses. In both cases, stopping or restarting the dashboard does not stop running agents. Concurrent jobs for one agent are allowed, and proposal authors can set optional `execution_agent` frontmatter. Job records contain prompt snapshots and may contain operational paths; treat the group's `shared/` directory as private application data.

The systemd launch path has its own opt-in integration test, skipped by default since it requires a real Linux user systemd manager:

```
AGENCY_TEST_SYSTEMD=1 .venv/bin/python -m pytest tests/test_job_systemd_integration.py -v
```

### A CLI for terminal people

Everything you can do in the browser, you can do from the terminal:

```
agency inbox           # What needs your attention
agency status          # Fleet overview
agency decide <slug>   # Decide on a proposal
agency jobs            # Execution status, agent, and changed files
agency logs <job_id>   # Tail a job's execution log
```

### Edit everything in the browser

Agent definitions, memory files, dispatch prompts, shared knowledge — all editable directly in the dashboard. No need to SSH in or find the right file.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/christag-agency serve
```

On first run, a setup wizard walks you through pointing Agency at your agent directory. It auto-detects your agents, creates the shared folder structure, and drops you into your dashboard.

Visit `http://localhost:8500`.

For development, start the same server with reload enabled:

```bash
.venv/bin/christag-agency serve --reload
```

Reload mode watches project code, templates, static assets, themes, and `config.yaml`. Saving Agency runtime records under a group's `shared/` directory does not restart the server.

## Add-on: Agency Setup Skill

Agency ships with a skill that can bootstrap a full agent team for any project. Install it and run `/agency-setup` from any project directory. It currently generates native Claude/Linux and GitHub Copilot/Windows profiles.

It analyzes your project, proposes a tailored agent team, generates identities, memory, shared prompts, and an interactive workspace, then atomically registers the group and its schedules with the singleton Agency dashboard. With approval, it verifies Agency's one global user-level dispatcher; it never creates a project-specific scheduler.

See [Setup Skill details](kb/setup-skill.md) for installation and what it creates.

## Tech Stack

Python 3.11+ / FastAPI / Jinja2 / Tailwind CSS CDN / No database / No build step / No Docker required

## Documentation

See the [`kb/`](kb/) folder for detailed guides:

- [Getting Started](kb/getting-started.md) — first-run walkthrough and basic concepts
- [Integrations](kb/integrations.md) — supported LLM tools and how they work together
- [Directory Structure](kb/directory-structure.md) — how agent groups are organized on disk
- [Agent Identity](kb/agent-identity.md) — display names, titles, avatars, and health monitoring
- [Data Formats](kb/data-formats.md) — observation, proposal, and decision file formats
- [Configuration](kb/configuration.md) — config.yaml reference
- [Dispatch](kb/dispatch.md) — agent scheduling system
- [Deployment](kb/deployment.md) — running as a service on Linux, macOS, or Windows
- [Contributing Integrations](kb/contributing-integrations.md) — how to add support for a new LLM tool

## Contributing

Contributions are welcome! Fork the repo, create a branch, and open a PR.

```bash
git clone https://github.com/christag/agency.git
cd agency
pip install -e .
python -m pytest tests/ -v
```

## License

AGPL-3.0 — free to use, modify, and distribute. All derivative works must remain open source under the same license. See [LICENSE](LICENSE) for details.
