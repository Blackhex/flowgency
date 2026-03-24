# Code Review Team Template

A 3-agent team for software projects: automated code review, security scanning, and documentation quality. These agents watch your codebase and surface issues through the observation pipeline.

## Agents

| Agent | Role | What They Do |
|-------|------|-------------|
| **Reviewer** | Code Quality | Reviews PRs and recent changes for bugs, anti-patterns, and maintainability issues |
| **Security** | Security Scanner | Monitors for vulnerabilities, dependency issues, secrets in code, and OWASP risks |
| **Docs** | Documentation Guard | Watches for doc drift, missing docs on new features, and broken examples |

## Setup

1. Copy this directory into your project or a standalone location:
   ```bash
   cp -r examples/code-review-team /path/to/your/review-agents
   ```

2. Add the group to your Agency `config.yaml`:
   ```yaml
   groups:
     review:
       name: Code Review Team
       path: /path/to/your/review-agents
       default_integration: claude-code
       agents:
       - reviewer
       - security
       - docs
   ```

3. Edit each agent's `CLAUDE.md` to reference your project's specific tech stack, coding conventions, and security requirements.

4. Customize the dispatch prompts in `shared/prompts/` for your review cadence.

5. Restart Agency and your new group appears in the sidebar.

## Dispatch Schedule (Suggested)

```yaml
dispatch:
  agents:
    reviewer:
      - prompt: review-recent.md
        every: 6h
    security:
      - prompt: security-scan.md
        at: "06:00"
    docs:
      - prompt: doc-check.md
        at: "10:00"
```

## Adapting This Template

- **Monorepo:** Add per-package Reviewer agents, keep Security and Docs shared
- **API project:** Add an API Contract agent that watches for breaking changes
- **Open-source project:** Add a Triage agent that reviews new issues and PRs
