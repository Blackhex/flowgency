# Decision Execution: Executing Agent, Log Link, and Changed Files

**Date:** 2026-07-10
**Status:** Approved (design)

## Problem

When a proposal is decided, Agency dispatches the proposal's `origin_agent` to
act on the decision. The decision detail page shows a generic execution status
(e.g. *"Agent completed execution (inferred from exit code)."*) but does **not**
show:

- **which agent** handled the decision, or
- **what the agent actually changed.**

The executing agent identity is known at dispatch time but never persisted to
the decision file, and the real work product (file edits) is only visible by
digging through raw log files. Users cannot see the outcome at a glance.

## Goal

On the decision detail page, surface, inside the existing **Execution** block:

1. **The executing agent** — rendered with the existing `agent_badge` filter.
2. **A link to the execution log** — the agent's `.out` log.
3. **A git-status-style list of changed files** — per file: an **A/M/D** status
   badge, the path (monospace), and `+N` (green) / `−M` (red) line counts.

The changed-files list is **provided through the integration interface** and
**implemented for the Copilot CLI integration only** for now. Integrations that
do not populate it (all others today) simply render no list. When there are no
changes, no list is shown either.

## Non-Goals (YAGNI)

- Changed-file support for non-Copilot integrations (claude-code, codex, gemini,
  aider, goose, script, sdk). They keep the empty default.
- Per-file inline diff rendering on the decision page. (Copilot's JSONL *does*
  carry unified diffs; capturing them is deferred — only path + status + line
  counts are surfaced now.)
- Git-based diffing of the sandbox root. The change data comes natively from the
  Copilot CLI, not from git.

## Verified Copilot CLI Behavior

Confirmed by live probes on 2026-07-09/10 against `copilot -p ... --output-format json`:

- `--output-format json` emits **JSONL** — one JSON object per line.
- **Native file tools** (`create`, `edit`, `str_replace`, `view`, `delete`)
  produce structured change data. Edits performed by **shelling out**
  (`Add-Content`, `sed`, etc.) are **not** counted — an accepted, honest
  limitation.
- `tool.execution_start` → `data: { toolCallId, toolName, arguments: { path, ... } }`
- `tool.execution_complete` → `data: { toolCallId, success, result: { detailedContent (unified diff) }, toolTelemetry: { properties: { command }, metrics: { linesAdded, linesRemoved } } }`
  - **Note:** `execution_complete` does **not** echo `toolName`; it must be
    paired with its `execution_start` via `toolCallId`.
- Final `result` event → `data.usage.codeChanges = { linesAdded, linesRemoved, filesModified: [absolute paths] }` — a session-level summary (native-tool edits only).
- `assistant.message` events carry `data.content` — human-readable text, used to
  reconstruct readable stdout from the JSONL stream.

## Design

### 1. Integration interface (`agency/integrations/__init__.py`)

Introduce a structured per-file change record and extend `RunResult`:

```python
from dataclasses import dataclass, field

@dataclass
class FileChange:
    path: str            # relative to sandbox root when possible; absolute fallback
    status: str          # "added" | "modified" | "deleted"
    lines_added: int
    lines_removed: int

@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    changed_files: list[FileChange] = field(default_factory=list)
```

`changed_files` defaults to an empty list, so every existing integration keeps
working unchanged and simply reports no changes. This is the generic contract:
any integration *may* populate it; only Copilot does today.

### 2. Copilot implementation (`agency/integrations/agency/copilot.py`)

- Switch `run()` to pass `--output-format json` and capture the JSONL stream.
- Add a private parser `_parse_jsonl_output(raw) -> tuple[str, list[FileChange]]`:
  1. Iterate JSONL lines, tolerating malformed lines.
  2. Track `toolCallId → toolName` and `arguments.path` from
     `tool.execution_start`.
  3. On `tool.execution_complete`, if the paired tool is a **write-type** tool
     (`create`, `edit`, `str_replace`, `delete` — skip read-only `view`), read
     the path from the start event and `linesAdded` / `linesRemoved` from
     `toolTelemetry.metrics`.
  4. Aggregate repeated edits to the same path (sum line counts). Status
     precedence: a file first `create`d stays **added** even if later edited;
     `edit`/`str_replace` → **modified**; `delete` → **deleted**.
  5. Map command → status: `create`→`added`, `edit`/`str_replace`→`modified`,
     `delete`→`deleted`.
  6. Reconstruct human-readable `stdout` by joining `assistant.message` content.
  7. **Fallbacks:** if per-tool parsing yields no files, fall back to
     `result.usage.codeChanges.filesModified` (status `modified`, line counts 0
     when per-file data is unavailable). On any parse error, return raw stdout
     and an empty list — a run must never break.
- Make each `FileChange.path` relative to the sandbox root's first root when
  possible; otherwise keep it absolute.
- Populate `RunResult.changed_files` from the parsed list.

The existing least-privilege flag matrix, sandbox handling, and the
`CREATE_NO_WINDOW` / `stdin=DEVNULL` Windows launch fix are all preserved.

### 3. Persistence (`execute_decision` in `agency/app.py`)

`execute_decision` already resolves the executing agent (`origin_agent`) and
writes the `.out` log (`out_path`, absolute, under
`{group}/shared/logs/<date>/`). Extend it so that when the run finishes it
records, via `update_decision_execution`:

- `executed_by` — the agent name.
- `execution_log` — the absolute path of `out_path` (the logs viewer route
  accepts an absolute `path` query param and validates it against
  `shared/logs`).
- `changed_files` — the `RunResult.changed_files` serialized as a list of dicts
  (`{path, status, lines_added, lines_removed}`), written **only when non-empty**.

### 4. Route (`decision_detail` in `agency/app.py`)

Read `executed_by`, `execution_log`, and `changed_files` from the decision
frontmatter and pass them to the template. `changed_files` is a list of dicts as
persisted.

### 5. Template (`agency/templates/decision_detail.html`)

Inside the existing **Execution** block:

- Render the executing agent with the `agent_badge` filter (when `executed_by`
  is present).
- Render a **View log** link to `/{group}/logs/view?path={execution_log}` (when
  `execution_log` is present).
- Render a **Files changed** list when `changed_files` is non-empty: each row
  shows a colored **A/M/D** badge (added=green, modified=amber, deleted=red),
  the path in monospace, and `+{lines_added}` (green) / `−{lines_removed}` (red).
- When `changed_files` is empty or absent, render no list.

## Data Flow

```
proposal decided
      │
      ▼
execute_decision(origin_agent)            agency/app.py
      │  dispatch via integration.run()
      ▼
CopilotIntegration.run(--output-format json)   copilot.py
      │  _parse_jsonl_output()
      ▼
RunResult(stdout, changed_files=[FileChange...])
      │
      ▼
update_decision_execution: executed_by, execution_log, changed_files
      │  (decision frontmatter)
      ▼
decision_detail() → decision_detail.html
      │
      ▼
Execution block: agent badge + log link + A/M/D changed-files list
```

## Testing

- **`tests/test_integration_sidecar.py` (`TestCopilot`):**
  - JSONL fixture with a native `create` + `edit` → assert `changed_files`
    parsed with correct paths, statuses, and `+/−` line counts.
  - Malformed JSONL → empty `changed_files` and raw-text stdout fallback.
  - Shell-only edit fixture → empty `changed_files` (documents the limitation).
- **`RunResult` default:** omitting `changed_files` yields `[]`.
- **`execute_decision`:** after a run, the decision frontmatter contains
  `executed_by`, `execution_log`, and `changed_files`.

Run: `python -m pytest tests/ -q`.

## Security

- The log link reuses the existing logs viewer route, which validates the `path`
  query param against `shared/logs` (path-traversal protection unchanged).
- Changed-file paths originate from the agent's own tool calls; they are
  displayed as text only (no filesystem access is granted by the list).
