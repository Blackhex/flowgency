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
{Copy from the agent's selected identity file (`CLAUDE.md` or `AGENTS.md`), scoped to
what makes sense during dispatch.}
- Read any file in the project
- Write and update observation/proposal files in `agents/shared/`

## Boundaries
{Copy from the agent's selected identity file boundaries, plus dispatch-specific
restrictions like:}
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

Claude/Linux:

```bash
find agents/shared/logs/ -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
```

Copilot/Windows:

```powershell
Get-ChildItem agents/shared/logs -Directory |
  Where-Object LastWriteTime -LT (Get-Date).AddDays(-14) |
  Remove-Item -Recurse -Force
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

  Claude/Linux:

  ```bash
  cd {PROJECT_ROOT}/agents/{agent} && \
    claude --dangerously-skip-permissions -p "Read agents/shared/proposals/{file}.
    Your feedback is requested. Add your feedback under ### Agent Feedback.
    Add your agent name to feedback_received in the frontmatter. Keep to 2-4 sentences."
  ```

  Copilot/Windows (run from `agents/{agent}` with the real `copilot.exe` resolved):

  ```powershell
  $copilotExe = @(Get-Command copilot -All -ErrorAction SilentlyContinue) |
    Where-Object { $_.Source -and [System.IO.Path]::GetExtension($_.Source) -ieq '.exe' } |
    Select-Object -First 1
  if (-not $copilotExe) {
    foreach ($directory in ($env:PATH -split [System.IO.Path]::PathSeparator)) {
      if (-not $directory) { continue }
      $candidate = Join-Path $directory 'copilot.exe'
      if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $copilotExe = [PSCustomObject]@{ Source = (Resolve-Path -LiteralPath $candidate).Path }
        break
      }
    }
  }
  & $copilotExe.Source -p "Read agents/shared/proposals/{file}. Your feedback is requested. Add your feedback under ### Agent Feedback. Add your agent name to feedback_received in the frontmatter. Keep to 2-4 sentences." --autopilot --experimental
  ```

### N+1. Finalize Completed Proposals
For any proposal where `feedback_received` matches `feedback_requested`:
- Write the `### Recommendation` section synthesizing all feedback
- Set `status: proposed`
```

---

## Tmux Launch Script Template

Claude/Linux only.

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

---

## Windows Terminal Launch Script Template

Copilot/Windows only. Filename: `agents/shared/start-agents.ps1`.

```powershell
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = '{PROJECT_ROOT}'
$Agents = @({AGENT_NAME_LITERALS})
$shell = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $shell) { $shell = (Get-Command powershell.exe -ErrorAction Stop).Source }
$terminal = Get-Command wt.exe -ErrorAction SilentlyContinue

function Get-CopilotExecutable {
  $commands = @(Get-Command copilot -All -ErrorAction SilentlyContinue)
  $executable = $commands | Where-Object {
    $_.Source -and [System.IO.Path]::GetExtension($_.Source) -ieq '.exe'
  } | Select-Object -First 1
  if ($executable) { return $executable.Source }

  foreach ($directory in ($env:PATH -split [System.IO.Path]::PathSeparator)) {
    if (-not $directory) { continue }
    $candidate = Join-Path $directory 'copilot.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  throw 'GitHub Copilot CLI executable was not found on PATH.'
}

$copilotExe = Get-CopilotExecutable
$escapedCopilotExe = $copilotExe.Replace("'", "''")
$copilotCommand = "& '$escapedCopilotExe' --autopilot --experimental"
$encodedCopilotCommand = [Convert]::ToBase64String(
  [Text.Encoding]::Unicode.GetBytes($copilotCommand)
)

if ($terminal) {
  $arguments = @()
  foreach ($agent in $Agents) {
    if ($arguments.Count -gt 0) { $arguments += ';' }
    $agentDir = Join-Path (Join-Path $ProjectRoot 'agents') $agent
    $arguments += @(
      'new-tab', '--title', $agent, '--startingDirectory', $agentDir,
      $shell, '-NoExit', '-EncodedCommand', $encodedCopilotCommand
    )
  }
  if ($arguments.Count -gt 0) { $arguments += ';' }
  $arguments += @(
    'new-tab', '--title', 'Terminal', '--startingDirectory', $ProjectRoot,
    $shell, '-NoExit'
  )
  Start-Process -FilePath $terminal.Source -ArgumentList $arguments
  return
}

foreach ($agent in $Agents) {
  $agentDir = Join-Path (Join-Path $ProjectRoot 'agents') $agent
  Start-Process -FilePath $shell -WorkingDirectory $agentDir `
    -ArgumentList @('-NoExit', '-EncodedCommand', $encodedCopilotCommand)
}
Start-Process -FilePath $shell -WorkingDirectory $ProjectRoot -ArgumentList '-NoExit'
```

Replace `{AGENT_NAME_LITERALS}` with comma-separated, single-quoted agent names. Keep
agent names restricted to the setup's validated directory names. Resolve the real
`copilot.exe` in the parent process and pass its absolute path via `-EncodedCommand`;
Windows Terminal may reuse a process whose `PATH` predates the Copilot installation.
Do not interpolate untrusted text into the encoded command.
