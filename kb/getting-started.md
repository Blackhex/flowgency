# Getting Started

## Install

```bash
git clone https://github.com/your-org/agency.git
cd agency
pip install -e .
```

## First Run

```bash
agency serve
```

Open `http://localhost:8500`. On first run, the setup wizard will guide you through:

1. **Name your group** — give your agent team a display name
2. **Point to a directory** — the folder where your agent subdirectories live
3. **Choose a default integration** — which LLM tool most of your agents use (Claude Code, Codex, Gemini, etc.)
4. **Auto-detect agents** — Agency scans the directory for recognized identity files and finds your agents automatically

The wizard creates the `shared/` folder structure (observations, proposals, decisions, logs, prompts) and drops you into your dashboard.

## Basic Concepts

### Agents

An agent is a subdirectory containing an identity file — `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, `GEMINI.md` for Gemini, and so on. Agency detects the tool automatically from the file that exists.

### Groups

A group is a collection of agents that work together. Each group points to a directory on your filesystem. You can have multiple groups for different projects, and each can use different LLM tools.

### The Pipeline

Agency organizes agent work into a four-stage pipeline:

- **Observations** — things an agent noticed (written as markdown files)
- **Proposals** — actionable suggestions that emerge from observations
- **Decisions** — your answers to the proposal's questions (approve/defer/reject, choose from options, or free-text input)
- **Execution** — Agency dispatches the agent to act on your answers

Every item in the pipeline links to its neighbors, so you can always trace the full chain.

### Dispatch

Dispatch is the optional scheduling system. It runs agents on a timer — daily at a specific time or every N hours — using your OS's native scheduler (systemd on Linux, launchd on macOS). You configure schedules through the admin panel.

## Next Steps

- **Add agent identities** — give agents display names, titles, and avatars from their profile pages
- **Set up dispatch** — configure schedules in the admin panel under your group's settings
- **Explore the CLI** — run `agency --help` to see terminal commands
- **Read the docs** — the rest of the `kb/` folder covers integrations, configuration, and deployment
