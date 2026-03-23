# Agency — Agent Management Dashboard

> **What this is:** A FastAPI web app that manages multiple groups of AI agents across any LLM tool. It's the unified control plane for monitoring agent observations, reviewing proposals, editing memory/prompts, and managing agent infrastructure — regardless of whether your agents use Claude Code, Codex, Gemini, Aider, Goose, or custom scripts.

## Architecture

- **Framework:** FastAPI + Jinja2 + Tailwind CSS (CDN, no build step)
- **Database:** None — entirely filesystem-based. Reads markdown files with YAML frontmatter from agent directories.
- **Config:** `config.yaml` — defines agent groups, Agency settings. Written atomically (temp + rename).
- **Integrations:** Plugin system (`agency/integrations/`) translates between LLM tools and Agency's internal model. Each agent declares which integration it uses.
- **Dispatch:** Python-based scheduler (`agency/dispatch/run.py`) with platform-native timers (systemd on Linux, launchd on macOS).
- **Deployment:** User-level systemd service (`agency.service`) on port 8500.

## Project Structure

```
~/dev/agency/
├── agency/                    # Python package
│   ├── app.py                 # Main FastAPI app (~2500 lines)
│   ├── config.py              # Shared config utilities (normalize_agents, agent_names)
│   ├── __init__.py
│   ├── integrations/          # LLM integration plugin system
│   │   ├── __init__.py        # BaseIntegration, registry, sidecar helpers
│   │   ├── claude_code.py     # Claude Code CLI (CLAUDE.md)
│   │   ├── codex.py           # OpenAI Codex CLI (AGENTS.md)
│   │   ├── gemini.py          # Google Gemini CLI (GEMINI.md)
│   │   ├── aider.py           # Aider (.aider.conf.yml, CONVENTIONS.md)
│   │   ├── goose.py           # Goose (.goosehints)
│   │   ├── script.py          # Custom script (user command template)
│   │   └── sdk.py             # File-contract-only (no execution)
│   ├── dispatch/              # Dispatch system
│   │   ├── run.py             # Python dispatch runner (replaces dispatch.sh)
│   │   ├── install.py         # Platform-native timer installer
│   │   └── __init__.py
│   └── templates/             # 26 Jinja2 templates
│       ├── base.html          # Layout: sidebar + main content
│       ├── home.html          # Inbox (open clues, decisions)
│       ├── agents.html        # Agent list with health dots + integration badges
│       ├── agent_profile.html # Agent profile: identity, integration, timeline, schedule
│       ├── clues.html         # Clue list with filters
│       ├── clue_detail.html   # Single clue + pipeline chain + status change
│       ├── curiosities.html   # Curiosity list
│       ├── curiosity_detail.html # Curiosity + pipeline chain + decide form
│       ├── decisions.html     # Decision list
│       ├── decision_detail.html # Single decision + pipeline chain
│       ├── documents.html     # Agent documents browser
│       ├── document_view.html # View/edit markdown, CSV, HTML
│       ├── logs.html          # Execution logs by date
│       ├── log_view.html      # Single log file
│       ├── prompts.html       # Dispatch prompts with agent assignments + schedule editing
│       ├── prompt_detail.html # View/edit prompt content
│       ├── memory.html        # Agent memory list
│       ├── memory_view.html   # View/edit memory
│       ├── admin.html         # Admin: redirects to settings
│       ├── admin_settings.html # Admin: app settings + installed integrations table
│       ├── admin_dispatch.html # Admin: dispatch timer management
│       ├── admin_groups.html  # Admin: agent group list + management
│       ├── admin_org_edit.html # Create/edit org + dispatch schedule + default integration
│       ├── admin_agent_detail.html # Admin agent detail view
│       ├── setup.html         # First-run wizard
│       └── tmux_config.html   # Tmux session config viewer
├── tests/                     # Test suite (78 tests)
│   ├── conftest.py            # Shared fixtures
│   ├── test_integrations.py   # Registry, detection, base classes
│   ├── test_integration_claude_code.py
│   ├── test_integration_sidecar.py  # Codex, Gemini, Aider, Goose
│   ├── test_integration_script.py
│   ├── test_integration_sdk.py
│   ├── test_config_normalization.py
│   ├── test_dispatch_run.py
│   └── test_dispatch_install.py
├── kb/                        # User-facing documentation
├── docs/                      # Specs and plans
├── config.yaml                # Group registry + Agency settings
├── pyproject.toml             # Dependencies
├── .venv/                     # Python virtual environment
└── CLAUDE.md                  # This file
```

## Integration System

Agency uses a plugin system to support multiple LLM tools. Each integration is a Python class that handles:

1. **Execution** — how to invoke the tool, pass a prompt, capture output
2. **Identity translation** — map the tool's native file to Agency's agent identity model
3. **Detection** — identify whether an agent directory belongs to this tool
4. **AI backbone** — optionally provide LLM access for Agency's own AI features

### Shipped Integrations

| Integration | Native File | Detect Signal | Execution | AI Backend |
|-------------|------------|---------------|-----------|------------|
| `claude-code` | `CLAUDE.md` | CLAUDE.md exists | `claude -p` | Yes |
| `codex` | `AGENTS.md` | AGENTS.md exists | `codex exec --yolo` | Yes |
| `gemini` | `GEMINI.md` | GEMINI.md exists | `gemini -p` | Yes |
| `aider` | `CONVENTIONS.md` | .aider.conf.yml exists | `aider --message-file` | No |
| `goose` | `.goosehints` | .goosehints exists | `goose run` | Yes |
| `script` | `agent.md` | Never (explicit config) | User command template | No |
| `sdk` | `agent.md` | agent.md exists (fallback) | None (external) | No |

### Integration Resolution

When Agency needs to interact with an agent, it resolves the integration in this order:

1. **Filesystem detection** — check what identity file exists on disk (CLAUDE.md, AGENTS.md, etc.)
2. **Config** — fall back to the agent's `integration` field in config.yaml
3. **Group default** — fall back to the group's `default_integration`
4. **Global default** — fall back to `claude-code`

This ensures an agent with CLAUDE.md is always handled correctly, even if the group default is different.

### Sidecar Metadata

Tools whose native files don't support YAML frontmatter (Codex, Gemini, Aider, Goose) store Agency metadata in `.agency-meta.yaml`:

```yaml
display_name: Product Manager
title: Content Strategy Lead
emoji: "📦"
```

### Adding New Integrations

1. Create `agency/integrations/your_tool.py`
2. Subclass `BaseIntegration`, implement all methods
3. Call `_register(YourIntegration())` at module level
4. Import in `agency/integrations/__init__.py`

## Config Format

```yaml
agency:
  title: Agency                    # App title shown in sidebar + page titles
  default_group: newsletter        # Group to redirect to from /
  ai_backend: claude-code          # Integration Agency uses for its own AI features
  dispatch:
    installed: true                # Set after first dispatch init
    interval: 15                   # Heartbeat interval in minutes

groups:
  newsletter:
    name: Newsletter Agents        # Display name
    path: /path/to/agents          # Filesystem path to agent directories
    default_integration: claude-code  # Default integration for agents in this group
    agents:                        # List of agents (string shorthand or dict form)
    - product                      # Shorthand: inherits group default_integration
    - editorial
    - name: custom-bot             # Dict form: explicit integration
      integration: script
      integration_config:
        command: "./run.sh {prompt_file}"
    dispatch:                      # Per-group dispatch config (optional)
      enabled: true
      timeout: 300                 # Seconds per agent run
      daily_limit: 20              # Max runs per day for this group
      agents:                      # Per-agent schedule rules
        product:
          - prompt: morning.md
            at: "09:00"
          - prompt: routine.md
            every: 6h
          - prompt: quality-gate.md
            at: "06:00"
            condition: pre-send    # Code-triggered (read-only in UI)
```

The `agency`, `dispatch`, `default_integration`, and `ai_backend` sections are optional — missing keys fall back to defaults.

### Agent List Format

Agents can be specified as bare strings (shorthand) or dicts (full form):

- `"product"` → `{"name": "product", "integration": "<group default>"}`
- `{"name": "bot", "integration": "script", "integration_config": {...}}` → explicit integration

Config normalization happens at load time. The shorthand is never rewritten to disk.

### Dispatch Rule Fields

| Field | Required | Description |
|-------|----------|-------------|
| `prompt` | Yes | Filename in `shared/prompts/` |
| `at` | One of at/every | Daily time (HH:MM) |
| `every` | One of at/every | Recurring interval (e.g., `6h`, `30m`) |
| `condition` | No | Code condition name — makes rule read-only in UI, skipped by Python dispatcher |

Rules with `condition` are skipped by the Python dispatcher with an info log. Groups that need condition-based dispatch can provide their own `shared/dispatch.sh` script, run independently.

## How Agent Groups Work

Each group points to a directory containing agent subdirectories and a `shared/` folder:

```
{group_path}/
├── {agent-name}/
│   ├── <identity-file>    # Tool-specific: CLAUDE.md, AGENTS.md, GEMINI.md, agent.md, etc.
│   ├── memory.md          # Persistent agent knowledge
│   └── .mcp.json          # MCP config (optional)
├── shared/
│   ├── clues/             # Agent observations (markdown + frontmatter)
│   ├── curiosities/       # Converged proposals
│   ├── decisions/         # User decisions
│   ├── prompts/           # Dispatch routine prompts
│   ├── logs/              # Execution logs (YYYY-MM-DD subdirs)
│   └── memory.md          # Cross-agent shared knowledge
└── (optional: _subagents/, etc.)
```

The identity file depends on the agent's integration. Agency auto-detects it from the filesystem.

The "Initialize" button in admin creates this structure for new groups.

## Route Structure

All org-scoped routes use `/{group}/` prefix. Admin routes are at `/admin/`.

### Org-Scoped Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{group}/` | Inbox — open clues, floated signals, recent decisions |
| GET | `/{group}/clues` | Clue list with agent/status filters |
| GET | `/{group}/clues/{slug}` | Clue detail + status change form |
| POST | `/{group}/clues/{slug}/status` | Update clue status |
| GET | `/{group}/curiosities` | Curiosity list |
| GET | `/{group}/curiosities/{slug}` | Curiosity detail + decide form |
| POST | `/{group}/curiosities/{slug}/decide` | Create decision for curiosity |
| GET | `/{group}/decisions` | Decision list |
| GET | `/{group}/decisions/{slug}` | Decision detail |
| POST | `/{group}/decisions/{slug}/retry` | Retry execution of approved decision |
| GET | `/{group}/documents` | Browse agent documents |
| GET | `/{group}/documents/view?path=` | View/edit document |
| POST | `/{group}/documents/save` | Save document edits |
| GET | `/{group}/logs` | Execution logs by date |
| GET | `/{group}/logs/view?path=` | View log file |
| GET | `/{group}/prompts` | Dispatch prompts with agent assignments |
| GET | `/{group}/prompts/{slug}` | View/edit prompt content |
| POST | `/{group}/prompts/{slug}/save` | Save prompt content edits |
| POST | `/{group}/prompts/dispatch` | Save dispatch assignments from prompts page |
| GET | `/{group}/memory` | Agent memory file list |
| GET | `/{group}/memory/view?path=` | View/edit memory file |
| POST | `/{group}/memory/save` | Save memory edits |
| GET | `/{group}/tmux-config` | Tmux session config viewer |
| POST | `/{group}/tmux-config/save` | Save tmux config edits |

### Agent Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{group}/agents` | Agent list with health dots + integration badges |
| GET | `/{group}/agents/{agent}` | Agent profile: identity, integration, timeline, schedule |
| POST | `/{group}/agents/{agent}/identity` | Save identity fields (display name, title, emoji) |
| POST | `/{group}/agents/{agent}/definition` | Save agent definition body |
| POST | `/{group}/agents/{agent}/upload-headshot` | Upload agent avatar |
| GET | `/{group}/agents/{agent}/headshot` | Serve headshot image |
| POST | `/{group}/agents/{agent}/toggle-subagent` | Toggle regular/subagent status |

### Admin Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/` | Admin settings page + installed integrations table |
| GET | `/admin/dispatch` | Dispatch timer management page |
| GET | `/admin/groups` | Agent group list + management |
| POST | `/admin/settings` | Update Agency title, default group, AI backend, dispatch interval |
| POST | `/admin/dispatch/install` | Install platform-native dispatch timer |
| GET | `/admin/orgs/new` | New org form |
| POST | `/admin/orgs/create` | Create org (writes config, optionally initializes) |
| GET | `/admin/orgs/{org}/edit` | Edit org form + dispatch schedule + default integration |
| POST | `/admin/orgs/{org}/save` | Save org changes (including default_integration) |
| POST | `/admin/orgs/{org}/dispatch` | Save dispatch config for group |
| POST | `/admin/orgs/{org}/delete` | Remove org from config |
| POST | `/admin/orgs/{org}/initialize` | Create shared/ folder structure |
| POST | `/admin/orgs/{org}/autodetect` | Scan path for directories with recognized definition files |
| GET | `/admin/orgs/{org}/agents/{agent}` | Admin agent detail view |
| POST | `/admin/orgs/{org}/agents/{agent}/save` | Save agent definition + per-agent integration |
| POST | `/admin/orgs/{org}/agents/create` | Create new agent in org |
| POST | `/admin/orgs/{org}/agents/{agent}/rename` | Rename agent directory |
| POST | `/admin/orgs/{org}/agents/{agent}/delete` | Delete agent from org |

### Other Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Redirect to default group |
| GET | `/setup` | First-run setup wizard |
| POST | `/setup` | Process setup wizard |
| POST | `/tips/dismiss` | Dismiss a single tip |
| POST | `/tips/hide-all` | Hide all tips |

## Data Model

All data is markdown with YAML frontmatter. No SQL, no migrations.

### Clue Frontmatter

```yaml
agent: infrastructure          # Source agent
date: 2026-03-20T06:00:00-04:00
category: container-health     # Domain category
status: open                   # open, connected, dismissed, archived
float: false                   # true = promoted to "Floated Signals"
linked_clues: []               # Related clue filenames
linked_curiosity: ~            # Promoted curiosity filename
ttl_days: 14                   # Days before auto-archive
```

### Curiosity Frontmatter

```yaml
origin_agent: infrastructure
date: 2026-03-20
status: investigating          # investigating, feedback, proposed, approved, deferred, rejected
clues: [clue1.md, clue2.md]
feedback_requested: []         # Agents asked for input
feedback_received: []          # Agents that responded
ttl_days: 30
```

### Decision Frontmatter

```yaml
curiosity: slug.md             # Linked curiosity
decided_by: admin
date: 2026-03-20
decision: approved             # approved, deferred, rejected
```

## Key Implementation Details

### Config Management
- `load_config()` reads config.yaml fresh
- `save_config(config)` writes atomically (temp file + `os.replace`)
- `reload_groups()` updates the global `GROUPS` dict after changes, normalizes agent lists
- `get_agency_config()` returns Agency settings with backward-compatible defaults
- `normalize_agents()` (in `agency/config.py`) converts bare string agent lists to dicts with integration info

### Integration System
- `agency/integrations/__init__.py` — `BaseIntegration` base class, `REGISTRY`, `get_integration()`, `detect_integration()`
- `get_agent_integration(g, agent_name)` — resolves integration: filesystem detection first, then config, then group default
- `parse_agent_identity(agent_dir, integration)` — reads identity via integration's native file
- `save_agent_identity()` / `save_agent_definition()` — writes via integration, preserving native format
- Identity files are filtered from the document browser automatically

### Dispatch System
- Python dispatcher at `agency/dispatch/run.py` — called by OS-native timer
- Platform installer at `agency/dispatch/install.py` — supports systemd (Linux) and launchd (macOS)
- `get_dispatch_status()` checks platform-native timer state
- `install_dispatch()` delegates to the platform installer
- Schedule rules: `at` (daily at specific time) and `every` (recurring interval)
- Condition rules are skipped by the Python dispatcher (require per-group scripts)
- TTL-style marker files for dedup: `.event-*` for `at` rules, `.last-*` for `every` rules

### Agent Profiles
- `build_agent_timeline()` interleaves logs and clues chronologically
- `agent_health_status()` returns green/amber/red based on last seen time
- Schedule pills shown from `config.dispatch.agents.{name}` rules
- Integration badge shown next to agent name

### Pipeline Relationships
- Clue detail resolves `linked_curiosity` → curiosity → decision chain
- Curiosity detail shows originating clues + resulting decision
- Decision detail traces back through curiosity to source clues
- All rendered as clickable pipeline banners with color-coded steps

### TTL Enforcement
- `check_ttl_expired()` and `enforce_ttl()` auto-archive stale items
- Called from `list_clues()` and `list_curiosities()` on every page load
- Rewrites status to "archived" in the markdown file frontmatter
- Skips items already in terminal states

### Security
- Path traversal protection: `fpath.resolve().relative_to(g["path"].resolve())` — validates file access is within the specific group's directory
- No auth — assumes local network / trusted access. Use a reverse proxy (Traefik, nginx) for auth.
- Delete operations require JS confirm()

### Template Context
Every org-scoped template gets via `group_context(g)`:
- `group` — current group key
- `group_name` — display name
- `groups` — dict of all group keys → names (for switcher)
- `agency_title` — from config
- `nav_open_clues`, `nav_actionable`, `nav_agent_count` — sidebar counts

### Initialize Workflow
Creates the standard agent group folder structure. Idempotent — only creates missing dirs/files:
- `shared/clues/`, `shared/curiosities/`, `shared/decisions/`, `shared/prompts/`, `shared/logs/`
- `shared/memory.md` with default header
- `shared/prompts/_clue-system-steps.md` (copies from first existing group that has one)
- Per-agent directories from the agents list

## Development

### Running Locally

```bash
cd ~/dev/agency
.venv/bin/python3 -m agency.app
# Serves at http://127.0.0.1:8500
```

### Dependencies

```
fastapi<0.116, starlette<1.0, uvicorn[standard], jinja2, markdown, pyyaml, markupsafe, python-multipart
```

Install: `.venv/bin/pip install -e .` from pyproject.toml.

### Running Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

### Service Management

```bash
# Status
systemctl --user status agency.service

# Restart (after code changes)
systemctl --user restart agency.service

# Logs
journalctl --user -u agency.service -f

# Enable on boot
systemctl --user enable agency.service
```

### Adding Features

1. Add route in `app.py`
2. Create template in `templates/`
3. Add nav link in `base.html` sidebar (if top-level page)
4. Restart service

Templates use Tailwind CSS via CDN — no build step needed. Custom prose styles for markdown rendering are in `base.html` `<style>` block.

### Template Filters

Available in all templates:
- `{{ status | status_badge }}` — colored pill for clue/curiosity status
- `{{ agent | agent_badge }}` — colored pill for agent name
- `{{ name | integration_badge }}` — colored pill for integration name
- `{{ text | render_md }}` — markdown → HTML
- `{{ dt | relative_time }}` — datetime to "5m ago", "2h ago", etc.

## Coding Conventions

- **Routes:** Async FastAPI handlers, grouped by feature (clues, curiosities, decisions, documents, logs, prompts, memory, admin)
- **Helpers:** Pure functions that take a group dict `g` and return data
- **Integrations:** Each integration is a Python class in `agency/integrations/` implementing `BaseIntegration`
- **Templates:** Jinja2 with Tailwind utility classes. All org-scoped links use `{{ group }}` prefix.
- **Forms:** Standard HTML forms with POST + 303 redirect pattern
- **Config writes:** Always atomic (temp + rename). Always call `reload_groups()` after.
- **Security:** Always validate file paths against group's root directory before reading/writing
- **Identity resolution:** Always detect from filesystem first, fall back to config

## System Environment

- **OS:** Fedora Kinoite 43 (immutable, rpm-ostree) — but Agency runs on any OS with Python 3.11+
- **Python:** System python in venv (no dnf/yum — this is immutable)
- **Systemd:** User-level services only (`~/.config/systemd/user/`). System-level services cannot access user home directories on Fedora Kinoite — always use user-level.
- **Port:** 8500 (hardcoded in `app.py main()`)

## Future Ideas

- Real-time updates (WebSocket or SSE for live clue/decision notifications)
- Unified inbox across all groups (`/all/` route)
- Event-based dispatch conditions (`if` rules — e.g., "run if unread email > 20")
- Clue analytics (trends over time, agent activity heatmaps)
- Dark mode toggle
- Mobile-optimized decision workflow
- MCP config viewing/editing on agent profile page
- Skills CRUD + SkillsMCP marketplace integration
- Per-agent integration change from profile page dropdown
- Windows Task Scheduler support for dispatch
