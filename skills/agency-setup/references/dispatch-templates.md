# Dispatch Infrastructure Templates

## Dispatch Prompt Template

Generate one prompt per agent per dispatch event. Filename: `agents/shared/prompts/{agent}-routine.md`

```markdown
# {AGENT_DISPLAY_NAME} — {EVENT_NAME} Routine

You are the {AGENT_DISPLAY_NAME} running your {event_name} routine.
{One sentence about what this routine focuses on.}

### 0. Load Memory
Read `agents/{AGENT_NAME}/memory.md` and `agents/shared/memory.md`.
Apply any corrections or preferences. {For maintainer: "Skip known non-issues from memory."}

## Observation Tasks

### 1. {TASK_NAME}
{Specific, actionable observation task with exact commands or files to check.
Use project-appropriate commands — npm/pytest/go test/cargo, not generic placeholders.}

### 2. {TASK_NAME}
{Another observation task.}

### 3. {TASK_NAME}
{Another observation task. 3-5 tasks is the sweet spot.}

## Pre-Approved Actions
{Copy from agent's CLAUDE.md, scoped to what makes sense during dispatch.}
- Read any file in the project
- Write and update observation/proposal files in `agents/shared/`

## Boundaries
{Copy from agent's CLAUDE.md boundaries, plus dispatch-specific restrictions like:}
- Do NOT edit source code during routine — save fixes for dedicated sessions
{For the builder agent's routine, code edits should wait for interactive sessions.}

### Update Memory
If you discovered stable facts worth remembering across runs (not ephemeral
observations — those go in observations), update `agents/{AGENT_NAME}/memory.md`.

## Observation System Steps
Follow the standard observation-system steps in `agents/shared/prompts/_observation-system-steps.md`.
Read that file now and follow each step after completing your observation tasks above.
```

## Cleanup Prompt Template

For the maintainer/cleanup agent. Filename: `agents/shared/prompts/{agent}-cleanup.md`

```markdown
# {AGENT_DISPLAY_NAME} — Nightly Cleanup

You are the {AGENT_DISPLAY_NAME} running the nightly cleanup routine.

## Cleanup Tasks

### 1. Archive Expired Observations
Read all files in `agents/shared/observations/` (not archive/).
For each observation:
- Parse the `date` and `ttl_days` from frontmatter
- If `date + ttl_days < today` AND `status` is `open`:
  - Set `status: abandoned` in the frontmatter
  - Move the file to `agents/shared/observations/archive/`
  - Create the archive directory if it doesn't exist

### 2. Archive Expired Proposals
Read all files in `agents/shared/proposals/` (not archive/).
For each proposal:
- Parse the `date` and `ttl_days` from frontmatter
- If `date + ttl_days < today` AND `status` is `proposed` (no decision was made):
  - Set `status: expired` in the frontmatter
  - Move the file to `agents/shared/proposals/archive/`

### 3. Clean Old Logs
```bash
find agents/shared/logs/ -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
```
NOTE: This is an explicit exception to the "no destructive commands" rule — cleanup
is specifically authorized to delete old log directories.

### 4. Summary
"Cleanup: archived N observations, expired M proposals, removed K log directories"

## Boundaries
- ONLY modify files in `agents/shared/observations/`, `agents/shared/proposals/`, `agents/shared/logs/`
- You may NOT touch any other files
```

## Product/Coordinator Routine Addition

If the builder agent also coordinates (common for small teams), add these sections
to their routine prompt:

```markdown
## Cross-Agent Orchestration Tasks

### N. Route Feedback Requests
Read all files in `agents/shared/proposals/`.
For any proposal where `status: feedback`:
- Check which agents are in `feedback_requested` but not in `feedback_received`
- For each missing agent, spawn the agent headlessly:
  ```bash
  cd {PROJECT_ROOT}/agents/{agent} && \
    claude --dangerously-skip-permissions -p "Read agents/shared/proposals/{file}.
    Your feedback is requested. Add your feedback under ### Agent Feedback.
    Add your agent name to feedback_received in the frontmatter. Keep to 2-4 sentences."
  ```

### N+1. Finalize Completed Proposals
For any proposal where `feedback_received` matches `feedback_requested`:
- Write the `### Recommendation` section synthesizing all feedback
- Set `status: proposed`
```

---

## dispatch.sh Template

Replace `{PROJECT_NAME}`, `{PROJECT_ROOT}`, and the agent/prompt pairs in event handlers.

```bash
#!/bin/bash
# {PROJECT_NAME} Agent Dispatch
# Triggered by {PROJECT_KEY}-dispatch.timer twice daily.
# Runs the appropriate agents for the current time window.
#
# Usage: dispatch.sh [--dry-run]

set -euo pipefail

PROJECT_ROOT="{PROJECT_ROOT}"
AGENTS_DIR="$PROJECT_ROOT/agents"
SHARED_DIR="$AGENTS_DIR/shared"
LOG_BASE="$SHARED_DIR/logs"
LOG_DIR="$LOG_BASE/$(date +%Y-%m-%d)"
DRY_RUN="${1:-}"
MAX_DAILY_RUNS=15
AGENT_TIMEOUT=300  # 5 minutes

export LC_ALL=C

# --- Logging ---

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# --- Safety ---

check_daily_limit() {
  mkdir -p "$LOG_DIR"
  local count
  count=$(find "$LOG_DIR" -name "*.run" 2>/dev/null | wc -l)
  if [[ "$count" -ge "$MAX_DAILY_RUNS" ]]; then
    log "Daily limit reached ($count/$MAX_DAILY_RUNS). Exiting."
    exit 0
  fi
  log "Daily run count: $count/$MAX_DAILY_RUNS"
}

# --- Event dedup ---

event_done() {
  [[ -f "$LOG_DIR/.event-$1" ]]
}

mark_event() {
  touch "$LOG_DIR/.event-$1"
}

# --- Agent Execution ---

run_agent() {
  local agent="$1"
  local prompt_file="$2"
  local ts
  ts=$(date +%H%M%S)

  check_daily_limit
  touch "$LOG_DIR/${agent}-${ts}.run"

  log "Starting agent: $agent with $prompt_file"

  if [[ "$DRY_RUN" == "--dry-run" ]]; then
    log "DRY RUN: would run $agent with $prompt_file"
    return 0
  fi

  local agent_dir="$AGENTS_DIR/$agent"
  if [[ ! -d "$agent_dir" ]]; then
    log "ERROR: Agent directory not found: $agent_dir"
    return 1
  fi

  local prompt_path="$SHARED_DIR/prompts/$prompt_file"
  if [[ ! -f "$prompt_path" ]]; then
    log "ERROR: Prompt file not found: $prompt_path"
    return 1
  fi

  if ! timeout "$AGENT_TIMEOUT" bash -c "
    cd '$agent_dir' && \
    claude --dangerously-skip-permissions -p \"\$(cat '$prompt_path')\"
  " >"$LOG_DIR/${agent}-${ts}.out" 2>"$LOG_DIR/${agent}-${ts}.err"; then
    local exit_code=$?
    if [[ "$exit_code" -eq 124 ]]; then
      log "TIMEOUT: $agent exceeded ${AGENT_TIMEOUT}s"
    else
      log "FAILED: $agent exited with code $exit_code"
    fi
    return 1
  fi

  log "Completed: $agent ($(wc -c < "$LOG_DIR/${agent}-${ts}.out") bytes output)"
  return 0
}

run_agents_parallel() {
  local pids=()
  local agents_and_prompts=("$@")  # pairs: agent1 prompt1 agent2 prompt2 ...

  local i=0
  while [[ $i -lt ${#agents_and_prompts[@]} ]]; do
    local agent="${agents_and_prompts[$i]}"
    local prompt="${agents_and_prompts[$((i+1))]}"
    run_agent "$agent" "$prompt" &
    pids+=($!)
    ((i+=2))
  done

  local failed=0
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      ((failed++))
    fi
  done

  return "$failed"
}

# --- Proposal Router ---

maybe_route_proposals() {
  local files
  files=$(find "$SHARED_DIR/proposals/" -maxdepth 1 -name '*.md' 2>/dev/null | head -1)
  if [[ -z "$files" ]]; then
    log "No proposals to check"
    return 0
  fi
  local pending
  pending=$(grep -rl '^status: feedback' "$SHARED_DIR/proposals/" 2>/dev/null | head -1)
  if [[ -z "$pending" ]]; then
    log "No pending proposal feedback requests"
    return 0
  fi
  log "Found pending proposal feedback — running coordinator"
  run_agent "{COORDINATOR_AGENT}" "{COORDINATOR_AGENT}-routine.md" || true
}

# --- Event Handlers ---
# Customize these based on which agents run at which times.

handle_morning() {
  log "Event: morning"
  if event_done "morning"; then
    log "morning already ran today, skipping"
    return 0
  fi
  mark_event "morning"

  # {MORNING_AGENTS_COMMENT}
  {MORNING_AGENT_CALLS}
}

handle_evening() {
  log "Event: evening"
  if event_done "evening"; then
    log "evening already ran today, skipping"
    return 0
  fi
  mark_event "evening"

  # {EVENING_AGENTS_COMMENT}
  {EVENING_AGENT_CALLS}
}

# --- Main ---

main() {
  log "=== {PROJECT_NAME} Agent Dispatcher Starting ==="
  check_daily_limit

  local hour
  hour=$(TZ=America/New_York date +%-H)
  log "Current time (ET): $(TZ=America/New_York date +%H:%M)"

  # Morning window: 6:45 - 7:15 ET
  if [[ "$hour" -ge 6 && "$hour" -lt 8 ]]; then
    handle_morning
  # Evening window: 20:45 - 21:15 ET
  elif [[ "$hour" -ge 20 && "$hour" -lt 22 ]]; then
    handle_evening
  else
    log "No event matches current time window, exiting"
  fi

  maybe_route_proposals

  log "=== {PROJECT_NAME} Agent Dispatcher Complete ==="
}

main "$@"
```

---

## Systemd Timer Template

Filename: `agents/shared/{PROJECT_KEY}-dispatch.timer`

```ini
[Unit]
Description={PROJECT_NAME} Agent Dispatch Timer

[Timer]
OnCalendar=*-*-* 07:00 America/New_York
OnCalendar=*-*-* 21:00 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
```

## Systemd Service Template

Filename: `agents/shared/{PROJECT_KEY}-dispatch.service`

```ini
[Unit]
Description={PROJECT_NAME} Agent Dispatch
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={PROJECT_ROOT}
ExecStart={PROJECT_ROOT}/agents/shared/dispatch.sh
Environment=PATH=/var/home/chris/.local/bin:/usr/local/bin:/usr/bin:/bin

StandardOutput=journal
StandardError=journal

TimeoutStartSec=1800
```

---

## Tmux Launch Script Template

Filename: `agents/shared/tmux-agents.sh`

The script creates a tmux session with one pane per agent plus a Terminal pane.
Calculate the grid layout based on total pane count (agents + 1 terminal).

### Color Palette

Cycle through these colors for agent panes (one per agent):

| Index | Colour Code | Name    | Symbol |
|-------|-------------|---------|--------|
| 0     | colour34    | Green   | ◆      |
| 1     | colour135   | Magenta | ✎      |
| 2     | colour199   | Pink    | ◈      |
| 3     | colour35    | Teal    | ◎      |
| 4     | colour214   | Orange  | ▣      |
| 5     | colour37    | Cyan    | ▲      |
| 6     | colour63    | Blue    | ◉      |
| 7     | colour244   | Gray    | ▸      |

The Terminal pane always uses colour244 (gray) with ▸ symbol.

### Grid Layout Logic

```
total_panes = len(agents) + 1  (agents + terminal)
if total_panes <= 2: cols = 2, rows = 1
elif total_panes <= 4: cols = 2, rows = 2
elif total_panes <= 6: cols = 3, rows = 2
elif total_panes <= 9: cols = 3, rows = 3
else: cols = 4, rows = ceil(total_panes / 4)
```

### Script Structure

```bash
#!/bin/bash
# Launch a tmux session with Claude Code agents in a grid layout.
# Layout: {ROWS} rows x {COLS} columns = {TOTAL} panes
#   {LAYOUT_COMMENT}

SESSION="{PROJECT_KEY}-agents"
PROJECT_ROOT="{PROJECT_ROOT}"

# Kill existing session if present
tmux kill-session -t "$SESSION" 2>/dev/null

# Create session with the first pane
tmux new-session -d -s "$SESSION" -c "$PROJECT_ROOT" -x 240 -y 60

# Enable pane border labels at top
tmux set-option -t "$SESSION" pane-border-status top

# --- Split panes ---
# First split into {ROWS} rows, then split each row into {COLS} columns.
{SPLIT_COMMANDS}

# --- Per-pane border colors ---
{COLOR_COMMANDS}

# --- Build pane-border-format with labels ---
{LABEL_FORMAT_BLOCK}

# --- Launch agents ---
{AGENT_LAUNCH_COMMANDS}

# Terminal pane (plain shell, no claude)
tmux send-keys -t "$SESSION:0.{TERMINAL_INDEX}" "cd $PROJECT_ROOT" Enter

# Select the first pane
tmux select-pane -t "$SESSION:0.0"

# Attach
tmux attach-session -t "$SESSION"
```

When generating:
- Calculate splits mathematically (50%, 66%, 75% etc. based on remaining columns)
- Each agent pane runs: `cd $PROJECT_ROOT/agents/{agent} && claude --dangerously-skip-permissions`
- The last pane is always Terminal (plain shell)
- Use UPPERCASE agent names with symbols for labels (e.g., "◆ PRODUCT MANAGER")
