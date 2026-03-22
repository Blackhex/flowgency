# Agency — Agent Management Dashboard

> **What this is:** A FastAPI web app that manages multiple groups of AI agents. It's the unified control plane for monitoring agent observations, reviewing proposals, editing memory/prompts, and managing agent infrastructure.

## Architecture

- **Framework:** FastAPI + Jinja2 + Tailwind CSS (CDN, no build step)
- **Database:** None — entirely filesystem-based. Reads markdown files with YAML frontmatter from agent directories.
- **Config:** `config.yaml` — defines agent groups, Agency settings. Written atomically (temp + rename).
- **Deployment:** User-level systemd service (`agency.service`) on port 8500.
- **Host:** Fedora Kinoite (immutable OS). Python venv at `.venv/`.

## Project Structure

```
~/dev/agency/
├── agency/                    # Python package
│   ├── app.py                 # Main FastAPI app (~2200 lines)
│   ├── __init__.py
│   ├── dispatch/              # Dispatch system
│   │   ├── dispatch.sh        # Global dispatcher script (installed to ~/.config/agency/)
│   │   └── __init__.py
│   └── templates/             # 23 Jinja2 templates
│       ├── base.html          # Layout: sidebar + main content
│       ├── home.html          # Inbox (open clues, decisions)
│       ├── agents.html        # Agent list with health pulse dots
│       ├── agent_profile.html # Agent profile: identity, timeline, schedule
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
│       ├── prompts.html       # Dispatch prompt list
│       ├── prompt_detail.html # View/edit prompt
│       ├── memory.html        # Agent memory list
│       ├── memory_view.html   # View/edit memory
│       ├── admin.html         # Admin: settings + dispatch + org management
│       ├── admin_org_edit.html # Create/edit org + dispatch schedule config
│       ├── admin_agent_detail.html # Admin agent detail view
│       ├── setup.html         # First-run wizard
│       └── tmux_config.html   # Tmux session config viewer
├── kb/                        # User-facing documentation
├── docs/                      # Specs and plans
├── config.yaml                # Group registry + Agency settings
├── pyproject.toml             # Dependencies
├── .venv/                     # Python virtual environment
└── CLAUDE.md                  # This file
```

## Config Format

```yaml
agency:
  title: Agency                    # App title shown in sidebar + page titles
  default_group: newsletter        # Group to redirect to from /
  dispatch:
    installed: true                # Set after first dispatch init
    interval: 15                   # Heartbeat interval in minutes

groups:
  newsletter:
    name: Newsletter Agents        # Display name
    path: /path/to/agents          # Filesystem path to agent directories
    agents: [agent1, agent2, ...]  # List of agent directory names
    dispatch:                      # Per-group dispatch config (optional)
      enabled: true
      timeout: 300                 # Seconds per agent run
      daily_limit: 20              # Max runs per day for this group
      agents:                      # Per-agent schedule rules
        agent1:
          - prompt: morning.md
            at: "09:00"
          - prompt: routine.md
            every: 6h
```

The `agency` and `dispatch` sections are optional — missing keys fall back to defaults.

## How Agent Groups Work

Each group points to a directory containing agent subdirectories and a `shared/` folder:

```
{group_path}/
├── {agent-name}/
│   ├── CLAUDE.md          # Agent role definition
│   ├── memory.md          # Persistent agent knowledge
│   └── .mcp.json          # MCP config (optional)
├── shared/
│   ├── clues/             # Agent observations (markdown + frontmatter)
│   ├── curiosities/       # Converged proposals
│   ├── decisions/         # User decisions
│   ├── prompts/           # Dispatch routine prompts
│   ├── logs/              # Execution logs (YYYY-MM-DD subdirs)
│   └── memory.md          # Cross-agent shared knowledge
└── (optional: dispatch.sh, _subagents/, etc.)
```

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
| GET | `/{group}/documents` | Browse agent documents |
| GET | `/{group}/documents/view?path=` | View/edit document |
| POST | `/{group}/documents/save` | Save document edits |
| GET | `/{group}/logs` | Execution logs by date |
| GET | `/{group}/logs/view?path=` | View log file |
| GET | `/{group}/prompts` | Dispatch prompt list |
| GET | `/{group}/prompts/{slug}` | View/edit prompt |
| POST | `/{group}/prompts/{slug}/save` | Save prompt edits |
| GET | `/{group}/memory` | Agent memory file list |
| GET | `/{group}/memory/view?path=` | View/edit memory file |
| POST | `/{group}/memory/save` | Save memory edits |

### Agent Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{group}/agents` | Agent list with health pulse dots |
| GET | `/{group}/agents/{agent}` | Agent profile: identity, timeline, schedule |
| POST | `/{group}/agents/{agent}/identity` | Save identity fields (display name, title, emoji) |
| POST | `/{group}/agents/{agent}/definition` | Save CLAUDE.md body |
| POST | `/{group}/agents/{agent}/upload-headshot` | Upload agent avatar |
| GET | `/{group}/agents/{agent}/headshot` | Serve headshot image |
| POST | `/{group}/agents/{agent}/toggle-subagent` | Toggle regular/subagent status |

### Admin Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/` | Settings dashboard — Agency settings + dispatch + org list |
| POST | `/admin/settings` | Update Agency title, default group, dispatch interval |
| POST | `/admin/dispatch/install` | Install global dispatch timer/service |
| GET | `/admin/orgs/new` | New org form |
| POST | `/admin/orgs/create` | Create org (writes config, optionally initializes) |
| GET | `/admin/orgs/{org}/edit` | Edit org form + dispatch schedule config |
| POST | `/admin/orgs/{org}/save` | Save org changes |
| POST | `/admin/orgs/{org}/dispatch` | Save dispatch config for group |
| POST | `/admin/orgs/{org}/delete` | Remove org from config |
| POST | `/admin/orgs/{org}/initialize` | Create shared/ folder structure |
| POST | `/admin/orgs/{org}/autodetect` | Scan path for directories with CLAUDE.md |

### Other Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Redirect to default group |
| GET | `/setup` | First-run setup wizard |
| POST | `/setup` | Process setup wizard |

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
- `reload_groups()` updates the global `GROUPS` dict after changes
- `get_agency_config()` returns Agency settings with backward-compatible defaults

### Dispatch System
- Global dispatcher at `agency/dispatch/dispatch.sh` — installed to `~/.config/agency/` during init
- Uses project's `.venv/bin/python3` to parse config.yaml (PyYAML → JSON)
- `get_dispatch_status()` checks systemd timer state
- `install_dispatch()` creates conf/script/service/timer and enables the timer
- Schedule rules: `at` (daily at specific time) and `every` (recurring interval)
- TTL-style marker files for dedup: `.event-*` for `at` rules, `.last-*` for `every` rules

### Agent Profiles
- `build_agent_timeline()` interleaves logs and clues chronologically
- `agent_health_status()` returns green/amber/red based on last seen time
- Schedule pills shown from `config.dispatch.agents.{name}` rules

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
- No auth — assumes local network / trusted access
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

## Example Agent Groups

### Example: Newsletter Agents
- **Agents:** product, editorial, design, sales, engineering, etc.
- **Dispatch:** Systemd timer-triggered event dispatcher
- **Focus:** Multi-agent newsletter production pipeline

### Example: Personal Agents
- **Agents:** life-manager, infrastructure, home, etc.
- **Subagents:** troubleshooter, gaming, calendar-advisor (in `_subagents/`)
- **Dispatch:** 2-hour systemd timer with scheduled windows
- **Focus:** Personal life management and system administration

## Development

### Running Locally

```bash
cd ~/dev/agency
.venv/bin/python3 app.py
# Serves at http://127.0.0.1:8500
```

### Dependencies

```
fastapi, uvicorn[standard], jinja2, markdown, pyyaml, markupsafe
```

Install: `.venv/bin/pip install -r` or from pyproject.toml.

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
- `{{ text | render_md }}` — markdown → HTML
- `{{ dt | relative_time }}` — datetime to "5m ago", "2h ago", etc.

## Coding Conventions

- **Routes:** Async FastAPI handlers, grouped by feature (clues, curiosities, decisions, documents, logs, prompts, memory, admin)
- **Helpers:** Pure functions that take a group dict `g` and return data
- **Templates:** Jinja2 with Tailwind utility classes. All org-scoped links use `{{ group }}` prefix.
- **Forms:** Standard HTML forms with POST + 303 redirect pattern
- **Config writes:** Always atomic (temp + rename). Always call `reload_groups()` after.
- **Security:** Always validate file paths against group's root directory before reading/writing

## System Environment

- **OS:** Fedora Kinoite 43 (immutable, rpm-ostree)
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
