# Verification Record — Ticket Workflows (Task 16)

**Branch:** `feat/ticket-workflows`  
**HEAD:** `374a356` (fix(runtime): restore native ticket MCP launcher)  
**Date recorded:** 2026-09-09  
**Status: BLOCKED — live acceptance incomplete**

---

## Scope

Task 16 adds ticket workflow orchestration: board UI, ticket lifecycle (get/start/artifact/transition/sign-off), MCP-stdio broker integration, and installed-runtime acceptance via Copilot CLI.

---

## Checkpoints

| Stage | Commit | Result | Notes |
|---|---|---|---|
| Clean baseline | `89c0cb2` | 2222 pass, 6 skip, 1 warning | Measured on `master`; baseline-clean.xml archived |
| 16A deterministic | `98345fc` | 2574 pass, 7 skip, 1 warning | 5 live deselected; not rerun after subsequent live fixes; checkpoint date/revision not a current claim |
| 16B normal UI | `9ede8d3` | 474 pass, 2 skip, 10.2 min, exit 0 | No `--update-snapshots`; 2 skips are desktop-only projects for mobile-only nav |
| 16C scope (impacted modules) | `374a356` | 62 pass, 2 skip, 1 warning, 8.80 s | See command below |
| 16C live (all real-runtime) | `374a356` (uncommitted probe) | 1 failed, 4 deselected, 21.47 s | See failure detail below |

Baseline confirmed ancestor:
```
git merge-base --is-ancestor 89c0cb2 HEAD  => exit 0
```

Deterministic green (2574 pass) archived at:
```
.superpowers/sdd/2026-09-08-ticket-workflows/task-16-deterministic-green.xml
```

---

## 16A — Deterministic acceptance

All ticket orchestration tests pass without a live runtime. Key additions and changes at `98345fc`:

- `tests/test_ticket_end_to_end.py` (2 tests): cross-layer `execute_job` + broker drives
  `start_work` / `publish_artifact` / `transition`; presatisfied file unchanged;
  assignee persists; `active_run` cleared; artifact content retained;
  user-cannot-transition boundary asserted.
- `tests/test_ticket_jobs.py`, `test_ticket_job_recovery.py`, `test_ticket_runtime_capabilities.py`,
  `test_ticket_mcp.py`, `test_copilot_ticket_tools.py` all green.
- Retired 410-route tests (`test_decision_verify.py`, `test_execute_decision.py`,
  `test_proposal_questions.py`) replaced by `test_pipeline_retirement.py`.
- Known warning: Starlette deprecated `BlockingPortal` (external dependency; not suppressed).

---

## 16B — Browser acceptance

**UI run command (task-16b-ui-final.txt):**
```
npm run test:ui
=> 474 passed, 2 skipped (10.2 min, exit 0)  at 9ede8d3, no --update-snapshots
```

Approved image pairs (8 total, `cca63d1` + `9ede8d3`):

| Snapshot file | Commit |
|---|---|
| `workflow-board-*` (desktop-light/dark, mobile-light/dark) | `cca63d1` |
| `workflow-ticket-detail-*` | `cca63d1` |
| `workflow-library-*` (editor overview/states/transitions) | `cca63d1` |
| `workflow-settings-*` (settings page) | `cca63d1` |
| `workflow-create-*` (create form, all 4 projects) | `9ede8d3` |
| `workflow-storage-error-*` (all 4 projects) | `9ede8d3` |

Snapshot baseline: `cca63d1` (96 PNG files).
Remaining dirty PNGs (`workflow-settings-*`) diverge on font metrics only; controller
approved as pre-existing; not included in the 8 accepted pairs.

Fixes landed in 16B production code:

| Commit | Fix |
|---|---|
| `d1c9c8c` | `tickets.py _team_context` supplies `team_agents` (missing agent options on ticket pages) |
| `f7b0b3a` | Workflow board action queue serialisation; refresh stale-guard |
| `b412497` | Playwright config `serviceWorkers: 'block'`; editor save-settle await |
| `be5d24b` | Fixture server `--log-level warning` argument |
| `9ede8d3` | Settings form: concise Pydantic error messages; canonical heading |

320px viewport coverage added (`workflow_board.spec.ts`, `workflow_library.spec.ts`,
`workflow_settings.spec.ts`); no new snapshots.

---

## 16C — Installed-runtime acceptance

### Measured environment

```
C:/Users/Blackhex/AppData/Local/Microsoft/WindowsApps/copilot.exe --version
=> GitHub Copilot CLI 1.0.84-3
py --list-paths
=> C:\Users\Blackhex\AppData\Local\Microsoft\WindowsApps\python3.13.exe  (Store app only)
```

No non-Store CPython interpreter installed. No environment modifications, elevation,
or credential changes were made.

### Scope run at HEAD (impacted modules, `374a356`)

```
.venv/Scripts/python.exe -m pytest \
  tests/test_runtime_process_lifecycle.py \
  tests/test_copilot_ticket_tools.py \
  tests/test_ticket_mcp.py \
  tests/test_copilot_launch_arguments.py \
  tests/test_copilot_credentials.py \
  tests/test_ticket_runtime_capabilities.py \
  tests/test_ticket_end_to_end.py \
  -k 'execute_job_records_denied_write_attempts or not real_runtime' -q
=> 62 passed, 2 skipped, 1 warning in 8.80s
```

Scoped review approved; no regressions vs `98345fc`.

### Earlier unrestricted live probe (superseded — not acceptance)

Two `real_runtime` tests passed at a temporarily relaxed launcher (`a8b9b28`/`dc4eb0d`).
Those runs had no read-only/artifact proof and are explicitly superseded by the
`374a356` boundary correction. They cannot be used as acceptance evidence.

### Auth basic sanity (not acceptance)

```
test_agent_verifies_presatisfied_project_without_rewriting_it[copilot]  PASSED  62s
```

One auth probe passed (real copilot round-trip, unrestricted, pre-satisfied fixture).
This confirms CLI reachability and basic MCP broker plumbing but does not satisfy the
required read/search-only denial case or artifact-publication requirement.

### Required restricted live probe (FAILED — blocker)

Uncommitted probe file: `tests/test_ticket_runtime_live.py` (not to be committed in
current state).

```
.venv/Scripts/python.exe -m pytest tests/test_ticket_runtime_live.py \
  -m real_runtime -k restricted_workspace -v
=> 1 failed, 4 deselected, 1 warning in 21.47s
```

Observed job output from the failed probe:

> Blocked: the `flowgency-tickets` MCP tools are unavailable, including required
> `ticket_get`, so the ordered workflow cannot proceed. No workspace write was
> attempted because it occurs only after ticket artifact publication.

Persisted job state: `runtime copilot`, status `complete`, exit `0`, ticket stayed in
`review`, `write_attempts: []`, `changed_files: []`.

**Root cause:** Copilot 1.0.84-3 enables its sandbox when a restricted permission
policy is active. The enabled sandbox's PowerShell drive-initialisation fails
("The file cannot be accessed by the system") before the MCP stdio server can start.
Ticket tools never register; the agent cannot proceed.

### What `374a356` does

Restores `__PYVENV_LAUNCHER__` environment variable and native Windows image
(`GetModuleFileNameEx`) in the ticket MCP server launch command, reversing the
`9e3c563` workaround which had used `sys.executable` (the `.venv` Scripts path).
The reversion was required because the scope-62 review flagged the Task 9b Windows
containment trade-off. This makes the Store-Python MCP launch reliably fail under
restriction again, which is the honest measured state.

Protected inputs at `374a356`: project files, `config.yaml`, workflow definitions,
Playwright config — all unchanged from baseline.

### Unmet live requirements

The following live scenarios remain unverified in this environment:

- Read/search-only workspace denial with retained ticket artifact proof
- Multi-ticket active-work observation
- Stale ticket refresh retry
- Optional sign-off flow
- Failed-after-commit cleanup

---

## Incomplete gates

| Gate | State |
|---|---|
| Full deterministic suite at HEAD | Not rerun since `98345fc`; post-live-fix deterministic impact covered by scope-62 only |
| Whole-branch review | Not done |
| Full suite integration (merge to `master`) | Not done |
| Merge / push | Not done |

---

## Protected files

Untracked files left untouched as required:
- `config.yaml.example.lock`
- `tests/test_records_worker.py`

No raw tokens, job stdout secrets, or sensitive environment data recorded here.

---

## Candidate unblock path

Candidate: install an isolated standard CPython (non-Store) and measure whether the
Copilot 1.0.84-3 sandbox permits MCP stdio with a non-WindowsApps interpreter path.

**This is unverified and not guaranteed to resolve the blocker.** Do not install
the environment without explicit approval. No environment modifications authorised.

---

## Approval required to proceed

1. **Non-Store CPython installation** — to test whether an alternate interpreter
   bypasses the sandbox drive-init failure.
2. **Copilot version change** (if 1.0.84-3 sandbox behaviour is confirmed as
   the root cause regardless of interpreter).
3. **Explicit acceptance of partial live evidence** as sufficient for merge, if the
   environment constraint is deemed a deployment-environment issue rather than a
   product defect.
