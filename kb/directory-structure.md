# Directory Structure

Agency expects each agent group to follow this layout:

```
your-agents/
├── agent-one/
│   ├── CLAUDE.md          # Claude Code agent definition
│   ├── memory.md          # Persistent agent knowledge
│   └── headshot.png       # Optional avatar (png, jpg, webp)
├── agent-two/
│   ├── AGENTS.md          # Codex agent definition
│   └── .agency-meta.yaml  # Agency metadata (display name, etc.)
├── agent-three/
│   └── agent.md           # SDK/Script agent definition
├── _subagents/            # Optional — agents called by other agents
│   └── helper-agent/
│       └── CLAUDE.md
└── shared/
    ├── clues/             # Agent observations (markdown + YAML frontmatter)
    ├── curiosities/       # Converged proposals
    ├── decisions/         # Your decisions
    ├── prompts/           # Dispatch routine prompts
    ├── logs/              # Execution logs (YYYY-MM-DD subdirectories)
    └── memory.md          # Cross-agent shared knowledge
```

## Identity Files

The identity file depends on the agent's integration:

| Integration | File | Notes |
|-------------|------|-------|
| Claude Code | `CLAUDE.md` | YAML frontmatter + markdown body |
| Codex | `AGENTS.md` | Plain markdown; metadata in `.agency-meta.yaml` |
| Gemini | `GEMINI.md` | Plain markdown; metadata in `.agency-meta.yaml` |
| Aider | `CONVENTIONS.md` | Detected via `.aider.conf.yml`; metadata in `.agency-meta.yaml` |
| Goose | `.goosehints` | Plain text; metadata in `.agency-meta.yaml` |
| Script / SDK | `agent.md` | YAML frontmatter + markdown body (Agency's native format) |

Agency auto-detects which integration an agent uses by checking which files exist. A group can contain agents using different integrations.

The **Initialize** button in the admin panel creates the `shared/` structure for you. Agent directories are created automatically when you add agents through the admin panel or auto-detect them.

## Subagents

Agents in the `_subagents/` directory are treated as secondary — they appear in a collapsible section on the agents page and are excluded from dispatch lists and auto-detect. You can toggle any agent between regular and subagent status from its profile page. Subagents follow the same integration model as regular agents.

## Logs

Execution logs are organized by date in `shared/logs/YYYY-MM-DD/` subdirectories. Log files follow the naming pattern `{agent-name}-{description}.out` or `.err`. Agency uses these to determine when an agent was last active and to build per-agent activity timelines.

## Sidecar Files

For integrations whose native files don't support YAML frontmatter, Agency stores its metadata in `.agency-meta.yaml` alongside the native file:

```yaml
display_name: Product Manager
title: Content Strategy Lead
emoji: "📦"
```

These sidecar files are created automatically when you edit identity fields in the UI.
