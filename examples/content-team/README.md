# Content Team Template

A 3-agent team for content-driven projects: blogs, newsletters, documentation sites, or any project where writing is a core output.

## Agents

| Agent | Role | What They Do |
|-------|------|-------------|
| **Writer** | Content Producer | Drafts new content, rewrites existing pieces, maintains voice consistency |
| **Editor** | Quality Gate | Reviews drafts for clarity, accuracy, tone, and structure. Catches issues before publish |
| **Researcher** | Intelligence Gatherer | Monitors competitors, finds trends, surfaces data that informs content strategy |

## Setup

1. Copy this directory into your project or a standalone location:
   ```bash
   cp -r examples/content-team /path/to/your/content-agents
   ```

2. Add the group to your Agency `config.yaml`:
   ```yaml
   groups:
     content:
       name: Content Team
       path: /path/to/your/content-agents
       default_integration: claude-code  # or whichever tool you use
       agents:
       - writer
       - editor
       - researcher
   ```

3. Edit each agent's `CLAUDE.md` (or your tool's identity file) to match your project's context — what you publish, where, and your voice/tone guidelines.

4. Customize the dispatch prompts in `shared/prompts/` for your publishing cadence.

5. Restart Agency and your new group appears in the sidebar.

## Dispatch Schedule (Suggested)

```yaml
dispatch:
  agents:
    researcher:
      - prompt: research-scan.md
        every: 12h
    writer:
      - prompt: content-review.md
        at: "09:00"
    editor:
      - prompt: quality-check.md
        at: "14:00"
```

## Adapting This Template

- **Newsletter team:** Rename Writer to Drafter, add a Distribution agent
- **Documentation site:** Rename Researcher to Codebase Monitor, focus Editor on technical accuracy
- **Marketing team:** Add a Social agent for cross-posting, focus Researcher on competitor monitoring
