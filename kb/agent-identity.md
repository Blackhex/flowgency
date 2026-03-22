# Agent Identity

Each agent has an identity file determined by its integration. The file format depends on the tool:

| Integration | Identity File | Supports Frontmatter |
|-------------|--------------|---------------------|
| Claude Code | `CLAUDE.md` | Yes |
| Codex | `AGENTS.md` | No (uses sidecar) |
| Gemini | `GEMINI.md` | No (uses sidecar) |
| Aider | `CONVENTIONS.md` | No (uses sidecar) |
| Goose | `.goosehints` | No (uses sidecar) |
| Script / SDK | `agent.md` | Yes |

## Identity Fields

Agents can have display names, titles, and emoji avatars. For tools that support frontmatter (Claude Code, Script, SDK), these are stored directly:

```yaml
---
display_name: "Researcher"
title: "Senior Research Analyst"
emoji: "🔍"
---
# Research Agent

Your agent's role definition goes here...
```

For tools that don't support frontmatter (Codex, Gemini, Aider, Goose), Agency stores metadata in a `.agency-meta.yaml` sidecar file alongside the native identity file:

```yaml
display_name: Researcher
title: Senior Research Analyst
emoji: "🔍"
```

All identity fields are optional. If omitted, the directory name is used as the display name.

## Integration Detection

Agency automatically detects which integration an agent uses by checking which identity file exists on disk. This takes priority over config settings — an agent with `CLAUDE.md` is always handled by the Claude Code integration, even if the group's default is different.

## Integration Badges

The agent list and profile pages show colored badges indicating which integration each agent uses (e.g., "Claude Code", "Codex", "SDK"). The admin settings page shows a table of all installed integrations.

## Headshots

Upload a headshot image (PNG, JPG, or WebP, max 2MB) through the agent profile page. The image is saved as `headshot.{ext}` in the agent's directory and appears on the agent list and profile views.

## Editing Identity

Identity fields can be edited from the agent profile page in the UI. Changes are written back through the agent's integration — to CLAUDE.md frontmatter for Claude Code agents, to the sidecar file for others.

## Health Pulse

The agent list shows a color-coded dot next to each agent's "last seen" time:

- **Green** — active within the last 24 hours
- **Amber** — 24-48 hours since last activity
- **Red** — 48+ hours or no recorded activity

Health is derived from log file timestamps — no configuration needed.
