#!/usr/bin/env bash
# Agency Global Dispatcher
# Runs on a systemd timer (default every 15 minutes).
# Reads schedule rules from config.yaml via Python/PyYAML,
# evaluates at/every rules, and dispatches qualifying agents.

set -euo pipefail

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ---------------------------------------------------------------------------
# Read dispatch.conf
# ---------------------------------------------------------------------------
DISPATCH_CONF="${HOME}/.config/agency/dispatch.conf"

if [[ ! -f "$DISPATCH_CONF" ]]; then
    log "ERROR: dispatch.conf not found at $DISPATCH_CONF"
    exit 1
fi

# Source the conf file (expects config_path=... and venv_python=...)
# shellcheck source=/dev/null
source "$DISPATCH_CONF"

if [[ -z "${config_path:-}" ]]; then
    log "ERROR: config_path not set in $DISPATCH_CONF"
    exit 1
fi
if [[ -z "${venv_python:-}" ]]; then
    log "ERROR: venv_python not set in $DISPATCH_CONF"
    exit 1
fi
# claude_path is optional — fall back to bare "claude" in PATH
CLAUDE_CMD="${claude_path:-claude}"
if [[ ! -f "$config_path" ]]; then
    log "ERROR: config.yaml not found at $config_path"
    exit 1
fi
if [[ ! -x "$venv_python" ]]; then
    log "ERROR: venv python not found or not executable at $venv_python"
    exit 1
fi

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
TODAY=$(date '+%Y-%m-%d')
NOW_EPOCH=$(date '+%s')

# Read dispatch interval from config (default 15)
INTERVAL=$("$venv_python" -c "
import yaml, sys
with open('$config_path') as f:
    cfg = yaml.safe_load(f)
interval = cfg.get('agency', {}).get('dispatch', {}).get('interval', 15)
print(int(interval))
")

log "Dispatch started (interval=${INTERVAL}m)"

# ---------------------------------------------------------------------------
# Python helper: emit one JSON line per enabled group
# ---------------------------------------------------------------------------
# Each line: {"group":"key","path":"/...","timeout":300,"daily_limit":20,
#             "agents":{"name":[{"prompt":"...","at":"09:00"},...],...}}
GROUPS_JSON=$("$venv_python" -c "
import yaml, json, sys

with open('$config_path') as f:
    cfg = yaml.safe_load(f)

groups = cfg.get('groups', {})
for key, g in groups.items():
    d = g.get('dispatch', {})
    if not d.get('enabled', False):
        continue
    out = {
        'group': key,
        'path': g.get('path', ''),
        'timeout': d.get('timeout', 300),
        'daily_limit': d.get('daily_limit', 20),
        'agents': d.get('agents', {}),
    }
    print(json.dumps(out))
")

if [[ -z "$GROUPS_JSON" ]]; then
    log "No enabled dispatch groups found. Exiting."
    exit 0
fi

# ---------------------------------------------------------------------------
# Helper: parse a JSON field from a line using Python
# ---------------------------------------------------------------------------
json_field() {
    # Usage: json_field "$json_line" "field"
    "$venv_python" -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])" "$1" "$2"
}

json_field_int() {
    "$venv_python" -c "import json,sys; print(int(json.loads(sys.argv[1])[sys.argv[2]]))" "$1" "$2"
}

# ---------------------------------------------------------------------------
# Helper: extract agent schedule list (returns JSON array lines)
# ---------------------------------------------------------------------------
agent_schedules() {
    # Usage: agent_schedules "$json_line" "agent_name"
    # Prints one JSON line per schedule entry
    "$venv_python" -c "
import json, sys
data = json.loads(sys.argv[1])
agent = sys.argv[2]
entries = data.get('agents', {}).get(agent, [])
for e in entries:
    print(json.dumps(e))
" "$1" "$2"
}

# ---------------------------------------------------------------------------
# Helper: list agent names from group JSON
# ---------------------------------------------------------------------------
agent_names() {
    "$venv_python" -c "
import json, sys
data = json.loads(sys.argv[1])
for name in data.get('agents', {}).keys():
    print(name)
" "$1"
}

# ---------------------------------------------------------------------------
# Check "at" rule: is current time within (interval + 2) minutes of target?
# ---------------------------------------------------------------------------
check_at_rule() {
    local target_time="$1"  # HH:MM
    local window_minutes=$(( INTERVAL + 2 ))

    # Convert target to today's epoch
    local target_epoch
    target_epoch=$(date -d "${TODAY} ${target_time}" '+%s' 2>/dev/null) || {
        log "  WARNING: invalid at time '${target_time}', skipping"
        return 1
    }

    local diff=$(( NOW_EPOCH - target_epoch ))
    # We want: 0 <= diff < window_minutes*60  (i.e., we are past the target but within window)
    if (( diff >= 0 && diff < window_minutes * 60 )); then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Check "every" rule: has enough time elapsed since last marker mtime?
# ---------------------------------------------------------------------------
check_every_rule() {
    local marker_file="$1"
    local interval_str="$2"  # e.g., "6h", "30m"

    # Parse interval to seconds
    local seconds
    seconds=$("$venv_python" -c "
import sys, re
s = sys.argv[1]
m = re.fullmatch(r'(\d+)(m|h)', s)
if not m:
    print(-1)
else:
    val = int(m.group(1))
    unit = m.group(2)
    print(val * 60 if unit == 'm' else val * 3600)
" "$interval_str")

    if (( seconds < 0 )); then
        log "  WARNING: invalid every interval '${interval_str}', skipping"
        return 1
    fi

    # No marker = never run = run now
    if [[ ! -f "$marker_file" ]]; then
        return 0
    fi

    local marker_mtime
    marker_mtime=$(stat -c '%Y' "$marker_file")
    local elapsed=$(( NOW_EPOCH - marker_mtime ))

    if (( elapsed >= seconds )); then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Prompt stem: filename without .md extension
# ---------------------------------------------------------------------------
prompt_stem() {
    local p="$1"
    echo "${p%.md}"
}

# ---------------------------------------------------------------------------
# Run an agent
# ---------------------------------------------------------------------------
run_agent() {
    local group_path="$1"
    local agent="$2"
    local prompt_file="$3"
    local timeout_secs="$4"
    local log_dir="$5"

    local prompt_path="${group_path}/shared/prompts/${prompt_file}"
    local agent_dir="${group_path}/${agent}"

    if [[ ! -d "$agent_dir" ]]; then
        log "  WARNING: agent directory not found: ${agent_dir}, skipping"
        return 0
    fi
    if [[ ! -f "$prompt_path" ]]; then
        log "  WARNING: prompt file not found: ${prompt_path}, skipping"
        return 0
    fi

    local timestamp
    timestamp=$(date '+%H%M%S')
    local stem
    stem=$(prompt_stem "$prompt_file")
    local out_file="${log_dir}/${agent}-${stem}-${timestamp}.out"
    local err_file="${log_dir}/${agent}-${stem}-${timestamp}.err"

    log "  RUNNING: ${agent} with ${prompt_file} (timeout ${timeout_secs}s)"

    (
        cd "$agent_dir"
        timeout "${timeout_secs}" "$CLAUDE_CMD" --dangerously-skip-permissions \
            -p "$(cat "$prompt_path")" \
            > "$out_file" \
            2> "$err_file"
    ) || {
        local rc=$?
        if (( rc == 124 )); then
            log "  TIMEOUT: ${agent} exceeded ${timeout_secs}s"
        else
            log "  ERROR: ${agent} exited with code ${rc}"
        fi
    }

    log "  DONE: ${agent} (log: ${out_file})"
}

# ---------------------------------------------------------------------------
# Main loop: iterate enabled groups
# ---------------------------------------------------------------------------
while IFS= read -r group_json; do
    # Parse all group fields in one Python call
    IFS=$'\t' read -r group_key group_path group_timeout daily_limit < <("$venv_python" -c "
import json,sys
g=json.loads(sys.argv[1])
print(g['group'],g['path'],int(g['timeout']),int(g['daily_limit']),sep='\t')
" "$group_json")

    log "Processing group: ${group_key}"

    # Ensure log directory exists
    logs_root="${group_path}/shared/logs"
    log_dir="${logs_root}/${TODAY}"
    mkdir -p "$log_dir"

    # Daily limit check: count .out files in today's log dir
    out_count=$(find "$log_dir" -maxdepth 1 -name '*.out' -type f 2>/dev/null | wc -l)
    if (( out_count >= daily_limit )); then
        log "  SKIP: daily limit reached (${out_count}/${daily_limit})"
        continue
    fi

    # Iterate agents in this group's dispatch config
    while IFS= read -r agent_name; do
        [[ -z "$agent_name" ]] && continue

        # Iterate schedule entries for this agent
        while IFS= read -r sched_json; do
            [[ -z "$sched_json" ]] && continue

            # Parse all fields in one Python call
            IFS=$'\t' read -r prompt at_time every_val < <("$venv_python" -c "
import json,sys
d=json.loads(sys.argv[1])
print(d.get('prompt',''),d.get('at',''),d.get('every',''),sep='\t')
" "$sched_json")

            if [[ -z "$prompt" ]]; then
                log "  WARNING: schedule entry for ${agent_name} missing 'prompt', skipping"
                continue
            fi

            stem=$(prompt_stem "$prompt")

            # Re-check daily limit before each run
            out_count=$(find "$log_dir" -maxdepth 1 -name '*.out' -type f 2>/dev/null | wc -l)
            if (( out_count >= daily_limit )); then
                log "  SKIP: daily limit reached (${out_count}/${daily_limit})"
                break 2
            fi

            should_run=false

            if [[ -n "$at_time" ]]; then
                event_marker="${log_dir}/.event-${agent_name}-${stem}"
                if [[ -f "$event_marker" ]]; then
                    log "  SKIP: ${agent_name}/${stem} already ran today (at rule)"
                    continue
                fi
                if check_at_rule "$at_time"; then
                    should_run=true
                fi
            elif [[ -n "$every_val" ]]; then
                # every markers live in logs root (not daily dir) to persist across days
                every_marker="${logs_root}/.last-${agent_name}-${stem}"
                if check_every_rule "$every_marker" "$every_val"; then
                    should_run=true
                fi
            else
                log "  WARNING: schedule entry for ${agent_name}/${prompt} has no 'at' or 'every' rule, skipping"
                continue
            fi

            if [[ "$should_run" == true ]]; then
                run_agent "$group_path" "$agent_name" "$prompt" "$group_timeout" "$log_dir"

                # Update markers
                if [[ -n "$at_time" ]]; then
                    touch "${log_dir}/.event-${agent_name}-${stem}"
                elif [[ -n "$every_val" ]]; then
                    touch "${logs_root}/.last-${agent_name}-${stem}"
                fi
            fi

        done < <(agent_schedules "$group_json" "$agent_name")
    done < <(agent_names "$group_json")

done <<< "$GROUPS_JSON"

log "Dispatch complete."
