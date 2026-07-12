# Dispatch

Dispatch is Agency's built-in agent scheduler. Agency supports one dashboard config and one user-level dispatcher per OS user. The dispatcher runs as a platform-native timer that calls a Python dispatch script on a regular heartbeat. The script evaluates schedule rules for each enabled group and submits qualifying agent work.

## Setup

Go to **Dispatch** (`/admin/dispatch`) and click **Install Dispatch**. Agency detects your platform and installs the appropriate timer:

**Linux (systemd):**
- `~/.config/systemd/user/agency-dispatch.service` + `agency-dispatch.timer`
- No root/sudo needed (user-level services)

**macOS (launchd):**
- `~/Library/LaunchAgents/com.agency.dispatch.plist`
- No root/sudo needed (user-level agents)

**Windows (Task Scheduler):**
- `AgencyDispatch` scheduled task
- No admin elevation needed (user-level task)

The timer is enabled and started immediately. Default heartbeat is every 15 minutes. You can change the interval on the Settings page (`/admin/`) after installation.

You can also manage dispatch from the CLI:

```text
christag-agency dispatch install --config C:\path\to\config.yaml
christag-agency dispatch status --config C:\path\to\config.yaml
christag-agency dispatch uninstall --config C:\path\to\config.yaml
```

## Enabling per Group

Dispatch is configured per group in the group edit form (`/admin/orgs/{group}/edit`):

- **Enable/disable** — checkbox to opt the group into dispatch
- **Timeout** — max seconds per agent run (default 300)
- **Daily limit** — max total agent runs per day for the group (default 20)

`dispatch.enabled: true` means a group's schedule is configured; it does not prove the host dispatcher is active. The global dispatcher checks every enabled group every 15 minutes by default.

Each agent in the group can have one or more schedule rules. Rules are added inline on the group edit page, per agent.

## Schedule Rules

Each rule has a **prompt** (a file from `shared/prompts/`) and a timing condition:

### `at` — run once per day at a specific time

```yaml
- prompt: morning-report.md
  at: "09:00"
```

The agent runs when the heartbeat fires within the window around the target time. Once it runs, a marker file prevents it from running again that day.

### `every` — run on a recurring interval

```yaml
- prompt: check-health.md
  every: "6h"
```

Valid units are `m` (minutes) and `h` (hours). The dispatcher checks a marker file's mtime to determine whether enough time has elapsed since the last run.

An agent can have multiple rules with different prompts and schedules.

### `condition` — code-triggered rules

```yaml
- prompt: quality-gate.md
  at: "06:00"
  condition: pre-send
```

Rules with a `condition` field are **skipped by the Python dispatcher** and displayed as read-only in the UI. Condition rules remain skipped by the Python heartbeat. External event automation may submit corresponding work, but it is outside Agency's managed scheduler and must not create another Agency dispatcher.

## How It Works

1. The platform timer fires every N minutes (default 15, configurable in Settings).
2. `agency/dispatch/run.py` reads `config.yaml` and finds groups with `dispatch.enabled: true`.
3. For each enabled group, it iterates agents and their schedule rules.
4. For `at` rules: checks if the current time is within the heartbeat window of the target. Skips if an event marker exists for today.
5. For `every` rules: checks if enough time has elapsed since the last-run marker's mtime.
6. Condition rules are skipped with an info log.
7. Qualifying work is submitted via the job system with a snapshot of the prompt content.
8. Output goes to `shared/logs/YYYY-MM-DD/{agent}-{prompt}-{HHMMSS}.out` (and `.err`).
9. After each run, the daily limit is re-checked. If reached, the group stops.

## Integration-Aware Execution

The dispatcher resolves each agent's integration from config before running. This means:

- Claude Code agents are run with `claude --dangerously-skip-permissions -p`
- Codex agents are run with `codex exec --yolo`
- Aider agents are run with `aider --message-file`
- Script agents use their configured command template
- SDK agents are skipped (externally managed)

Different agents in the same group can use different integrations.

## Config Format

Dispatch settings live in two places in `config.yaml`:

```yaml
agency:
  dispatch:
    interval: 15          # Heartbeat interval in minutes (5-120)

groups:
  my-project:
    dispatch:
      enabled: true
      timeout: 300
      daily_limit: 20
      agents:
        researcher:
          - prompt: morning-scan.md
            at: "09:00"
          - prompt: check-feeds.md
            every: "6h"
        writer:
          - prompt: draft-review.md
            at: "14:00"
```

## Installed Files

**Linux:**

| File | Purpose |
|------|---------|
| `~/.config/systemd/user/agency-dispatch.service` | Systemd service unit |
| `~/.config/systemd/user/agency-dispatch.timer` | Systemd timer unit |

**macOS:**

| File | Purpose |
|------|---------|
| `~/Library/LaunchAgents/com.agency.dispatch.plist` | launchd agent plist |

**Windows:**

| File | Purpose |
|------|---------|
| `AgencyDispatch` task | Windows Task Scheduler task |

## Monitoring

- **Agent list** — integration badges show which tool each agent uses. Health dots show recent activity.
- **Agent profile** — schedule pills show each rule (`at 09:00`, `every 6h`) with the associated prompt.
- **Logs** — dispatch output lands in the Logs section under the run date, one file per agent per run.
- **Settings** — shows whether the timer is active, the detected platform, and the current heartbeat interval.
