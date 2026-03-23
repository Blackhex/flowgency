# Agency Setup Skill

Agency ships with a Claude Code skill that can bootstrap a fully functional agent team for any codebase.

## Install

```bash
# Create the skills directory if it doesn't exist
mkdir -p ~/.claude/skills

# Symlink the skill, replacing the path with wherever you cloned Agency
ln -s /path/to/agency/skills/agency-setup ~/.claude/skills/agency-setup
```

## Usage

From any project directory in Claude Code, run:

```
/agency-setup
```

## What It Does

1. **Analyzes your codebase** — language, framework, structure, and purpose
2. **Proposes 3-5 agents** tailored to the project — you approve or tweak the list
3. **Generates everything Agency needs:**
   - Agent role definitions and memory files
   - `shared/` folder with observations, proposals, decisions, logs, prompts
   - Dispatch prompts with project-specific observation tasks
   - Tmux launch script with color-coded agent panes
4. **Registers the new group** with Agency (if installed)
5. **Enables the dispatch timer** so agents start running on schedule
