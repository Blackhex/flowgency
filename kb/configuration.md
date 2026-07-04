# Configuration

Agency uses a `config.yaml` file in your working directory:

```yaml
agency:
  title: Agency                    # App title shown in sidebar
  default_group: my-project        # Group to show on startup
  decided_by: admin                # Default name for decisions
  ai_backend: claude-code          # Integration used for Agency's own AI features
  dispatch:
    installed: true                # Set after first dispatch init
    interval: 15                   # Heartbeat interval in minutes

groups:
  my-project:
    name: My Project Agents
    path: /path/to/your/agents
    default_integration: claude-code  # Default integration for agents in this group
    agents:
    - researcher                   # Shorthand: inherits group default_integration
    - writer
    - name: custom-bot             # Dict form: explicit integration
      integration: script
      integration_config:
        command: "./run.sh {prompt_file}"
    workspaces:                    # Optional — runtime frontend configs
      - name: Terminal Grid
        type: tmux
        config:
          script_path: /path/to/tmux-session.sh
    dispatch:                      # Optional — see kb/dispatch.md
      enabled: true
      timeout: 300
      daily_limit: 20
      agents:
        researcher:
          - prompt: morning-scan.md
            at: "09:00"
```

## Agency Settings

| Key | Default | Description |
|-----|---------|-------------|
| `title` | `Agency` | App title in sidebar and page titles |
| `default_group` | first group | Group to redirect to from `/` |
| `decided_by` | `admin` | Default name attached to decisions |
| `ai_backend` | `claude-code` | Integration used for Agency's own AI features |
| `dispatch.installed` | `false` | Set automatically after dispatch init |
| `dispatch.interval` | `15` | Heartbeat interval in minutes (5-120) |

## Group Settings

| Key | Required | Description |
|-----|----------|-------------|
| `name` | yes | Display name for the group |
| `path` | yes | Filesystem path to the agent directory |
| `agents` | yes | List of agents (strings or dicts — see below) |
| `default_integration` | no | Default integration for agents (default: `claude-code`) |
| `workspaces` | no | List of workspace configs (see below) |
| `dispatch.enabled` | no | Enable dispatch scheduling for this group |
| `dispatch.timeout` | no | Seconds per agent run (default 300) |
| `dispatch.daily_limit` | no | Max agent runs per day (default 20) |
| `dispatch.agents` | no | Per-agent schedule rules (see [Dispatch](dispatch.md)) |

### Sandbox root (`sandbox_root`)

Optional per-group filesystem scope for the agent **runtime** (not the dashboard).

- **Unset (default):** sandbox-capable runtimes launch in full-access mode — the
  agent can read/write anywhere the OS user can. For GitHub Copilot this means
  the agent runs from its own directory with `--autopilot --allow-all-paths
  --allow-all-tools`.
- **Set:** the runtime runs **from the sandbox root as its working directory**,
  which scopes native file access to that tree. For Copilot this means launching
  with `cwd = <sandbox_root>` plus `--autopilot --allow-tool=read/write/shell`.
  Use this to grant an agent nested in a larger repository access to the repo
  root (for shared memory, output folders, etc.). Copilot grants native file
  access to paths under the working directory, so running from the root puts the
  whole tree in scope — the agent still loads its own identity file because the
  dispatch prompt reads it by path.

Absolute paths are used as-is; relative paths resolve against the group `path`.

Only runtimes that support sandboxing honor this setting. Runtimes that do not
(shown with a warning in the admin UI) always run with their default access.

This setting does **not** change the Agency dashboard's file browsers, which
remain scoped to the group path.

## Agent List Format

Agents can be specified in two forms:

- **Shorthand (string):** `"researcher"` — inherits the group's `default_integration`
- **Full form (dict):** `{"name": "bot", "integration": "script", "integration_config": {"command": "..."}}`

You can mix both in the same list. The shorthand is never rewritten to disk — it stays compact.

## Per-Agent Integration

Each agent can use a different integration. Set it in the full dict form:

```yaml
agents:
  - name: alpha
    integration: claude-code      # Uses Claude Code
  - name: beta
    integration: codex            # Uses Codex
  - gamma                         # Uses group default
```

The integration determines which identity file the agent uses, how it's executed, and how its profile is displayed.

## Managing Groups

Groups can be added, edited, and removed from the admin panel at `/admin/`. The admin panel also provides:

- **Initialize** — creates the `shared/` folder structure for a group
- **Auto-detect Agents** — scans the group path for directories containing any recognized agent definition file (CLAUDE.md, AGENTS.md, GEMINI.md, .goosehints, .aider.conf.yml, agent.md)
- **Agent CRUD** — create, rename, and delete individual agents
- **Default Integration** — set the default LLM tool for new agents in the group
- **AI Backend** — choose which integration Agency uses for its own AI features (in app settings)

Config writes are atomic (temp file + rename) and the group registry is reloaded after every change.

## Workspaces

Workspaces define how you interact with an agent group at runtime — tmux grids, IDE windows, chat channels, etc. Each group can have multiple workspaces:

```yaml
workspaces:
  - name: Terminal Grid
    type: tmux
    config:
      script_path: /path/to/tmux-session.sh
  - name: Cursor IDE
    type: cursor
    config:
      project_path: /path/to/project
```

Available workspace types: `tmux`, `cursor`, `superset`, `ide`, `chat`, `custom`. Workspaces are configured per group in the admin panel or directly in config.yaml.

Legacy `tmux_config` (a single path string) is auto-migrated to the `workspaces` list at config load time.
