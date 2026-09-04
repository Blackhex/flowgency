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

2. Add the team to your Agency `config.yaml`:
   ```yaml
   schema_version: 6
   agency:
     prompt_store: /path/to/agency/prompts
   teams:
     content:
       name: Content Team
       workspace_path: /path/to/your/project
       path: /path/to/agency/teams/content
       default_integration: claude-code  # or whichever tool you use
       agents:
       - name: writer
         blueprint: writer
         integration: claude-code
         capabilities:
           write: true
       - name: editor
         blueprint: editor
         integration: claude-code
         capabilities:
           write: false
       - name: researcher
         blueprint: researcher
         integration: claude-code
         capabilities:
           write: false
   ```

3. Edit each agent's `CLAUDE.md` (or your tool's identity file) to match your project's context — what you publish, where, and your voice/tone guidelines.

4. Assign saved prompts and schedules under each instance's `routines` in `config.yaml`, and register any instance-private prompts you want to launch from the roster.

5. Restart Agency and your new team appears in the sidebar.

## Routine Schedule (Suggested)

```yaml
teams:
  content:
    agents:
      - name: researcher
        routines:
          - id: research-scan
            prompt: {scope: blueprint, name: research-scan}
            schedule: {every: 12h}
      - name: writer
        routines:
          - id: content-review
            prompt: {scope: blueprint, name: content-review}
            schedule: {at: "09:00"}
      - name: editor
        routines:
          - id: quality-check
            prompt: {scope: blueprint, name: quality-check}
            schedule: {at: "14:00"}
```

## Adapting This Template

- **Newsletter team:** Rename Writer to Drafter, add a Distribution agent
- **Documentation site:** Rename Researcher to Codebase Monitor, focus Editor on technical accuracy
- **Marketing team:** Add a Social agent for cross-posting, focus Researcher on competitor monitoring
