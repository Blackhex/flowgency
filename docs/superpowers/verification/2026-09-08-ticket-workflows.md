# Verification Record — Ticket Workflows (Task 16)

**Branch:** `feat/ticket-workflows`  
**Worktree:** `C:/Projekty/Flowgency/.worktrees/ticket-workflows`  
**HEAD:** `a49ecd4`  
**Recorded:** 2026-09-10  
**Status:** `BLOCKED` — the user-approved isolated non-Store CPython experiment unblocked restricted MCP startup, but the required restricted live acceptance still fails later at broker request time.

## Scope

This note records Task 16 verification checkpoints and the bounded 2026-09-10 non-Store interpreter experiment. It does not waive any remaining gate and does not replace whole-branch review or integration.

## Latest Outcome

- The isolated non-Store install was completed and measured. `uv` was already present, `uv --no-config python install 3.13.14 --no-bin --no-registry` was unavailable (`catalog not found`), and `uv --no-config python install 3.13` selected CPython `3.13.13` under `.superpowers/python/cpython-3.13.13-windows-x86_64-none`.
- New virtual environment: `.superpowers/venv-cpython`. Original `.venv` Store CPython `3.13.14` remained untouched. No PATH, registry, default-Python, or global-auth settings changed.
- `uv pip freeze` comparison excluding the editable install matched 50 package versions across the two environments. The application remained editable from this feature worktree. The patch difference `3.13.14` vs `3.13.13` is real, so this was not a single-bit-controlled environment swap.
- Test-only commit `a49ecd4` reinjects `__PYVENV_LAUNCHER__` into each generated native `Popen` in the live-process tests. That fixes CPython child portability in tests only. It does not change production launcher policy, sandbox policy, or native process containment.
- The old Store-only symptom, where `flowgency-tickets` tools never appeared, is now historical for this experiment. In the restricted non-Store run the MCP server initialized, tools registered, and `ticket_get` executed.
- Acceptance is still blocked because both live `ticket_get` calls returned `Ticket broker is unavailable`, the ticket stayed in `review`, and no artifact publication or workspace write was reached.

## Checkpoints

| Checkpoint | Commit | Result | Evidence |
| --- | --- | --- | --- |
| Clean baseline in the feature worktree | `89c0cb2` | 2222 passed, 6 skipped, 1 warning | `.superpowers/sdd/2026-09-08-ticket-workflows/baseline-clean.txt` |
| Deterministic verification checkpoint | `98345fc` | 2574 passed, 7 skipped, 5 deselected, 1 warning | `.superpowers/sdd/2026-09-08-ticket-workflows/task-16-deterministic-green.txt` |
| Full UI matrix | `9ede8d3` | `npm run test:ui -- --reporter=dot` -> 474 passed, 2 skipped, exit 0 | `.superpowers/sdd/2026-09-08-ticket-workflows/task-16b-ui-final.txt` |
| Native-launch containment repair slice | `374a356` | 62 passed, 2 skipped, 1 warning | superseding Task 16 report section |
| Required restricted live probe, Store `.venv` | `374a356` + uncommitted live test | 1 failed, 4 deselected, 1 warning, 21.47s | superseding Task 16 report section |
| Non-Store portability — initial run | pre-`a49ecd4` | 26 passed, 1 failed, 2 skipped, 1 warning | `task-16c-cpython-launch.txt/xml` |
| Non-Store portability correction | `a49ecd4` | 27 passed, 2 skipped, 1 warning | `task-16-report.md` |
| Required restricted live probe, non-Store `.superpowers/venv-cpython` | `a49ecd4` + uncommitted live test | 1 failed, 4 deselected, 1 warning, 59.80s first probe; 53.39s bounded retry | `task-16c-cpython-live.txt/xml`, `task-16c-cpython-diag-runtime.xml` |

`89c0cb2` was the clean baseline rerun inside this feature worktree, not `master`.

## Commands And Evidence

### Historical baseline and UI

```text
.venv/Scripts/python.exe -m pytest tests/ -q
=> 2222 passed, 6 skipped, 1 warning in 259.19s

.venv/Scripts/python.exe -m pytest tests/ -m 'not real_runtime' -q \
  --junitxml=.superpowers/sdd/2026-09-08-ticket-workflows/task-16-deterministic-green.xml
=> 2574 passed, 7 skipped, 5 deselected, 1 warning in 295.97s

npm run test:ui -- --reporter=dot
=> 474 passed, 2 skipped, exit 0
```

Commit `9ede8d3` records the eight settings/create/error PNGs; `cca63d1` records the remaining 96 reviewed PNGs. No `--update-snapshots` was used in the normal UI green run.

### Non-Store environment and focused validation

```text
uv --no-config python install 3.13
=> selected CPython 3.13.13 under .superpowers/python/cpython-3.13.13-windows-x86_64-none

.superpowers/venv-cpython/Scripts/python.exe -m pytest \
  tests/test_runtime_process_lifecycle.py tests/test_copilot_ticket_tools.py tests/test_ticket_mcp.py -q
=> 26 passed, 1 failed, 2 skipped, 1 warning

.superpowers/venv-cpython/Scripts/python.exe -m pytest \
  tests/test_runtime_process_lifecycle.py tests/test_copilot_ticket_tools.py tests/test_ticket_mcp.py -q
=> 27 passed, 2 skipped, 1 warning

.venv/Scripts/python.exe -m pytest tests/test_runtime_process_lifecycle.py \
  -k test_run_supervised_timeout_kills_native_descendants_after_root_exit -v
=> 1 passed, 18 deselected in 1.24s
```

The first non-Store failure was the CPython `__PYVENV_LAUNCHER__` test-environment bug fixed in `a49ecd4`. The focused Store rerun stayed green, which confirms the test-only portability fix did not break the original environment.

### Required restricted live probe

```text
.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_runtime_live.py \
  -m real_runtime -k restricted_workspace -v \
  --basetemp=.superpowers/sdd/2026-09-08-ticket-workflows/task-16c-cpython-live-runtime \
  --junitxml=.superpowers/sdd/2026-09-08-ticket-workflows/task-16c-cpython-live.xml
=> 1 failed, 4 deselected, 1 warning in 59.80s

.superpowers/venv-cpython/Scripts/python.exe -m pytest tests/test_ticket_runtime_live.py \
  -m real_runtime -k restricted_workspace -v \
  --basetemp=.superpowers/sdd/2026-09-08-ticket-workflows/task-16c-cpython-diag-runtime \
  --junitxml=.superpowers/sdd/2026-09-08-ticket-workflows/task-16c-cpython-diag-runtime.xml
=> 1 failed, 4 deselected, 1 warning in 53.39s
```

Measured restricted-runtime facts:

- Copilot version remained `1.0.84-3`.
- Security sample stayed constrained: `sandbox.enabled=true`, `allowBypass=false`, workspace read-only, `gitAuth=false`, `ghAuth=false`.
- The MCP service initialized as `flowgency-tickets`, tools registered, and `ticket_get` ran twice.
- Each `ticket_get` took about 5.3s, close to `BROKER_CLIENT_TIMEOUT_SECONDS = 5.0`.
- The returned tool payload was the unchanged redacted broker error: `Ticket broker is unavailable`.
- No ticket mutation, artifact publication, or workspace write attempt occurred. The ticket remained in `review`; `write_attempts` and `changed_files` stayed empty.

### Root-cause boundary

The experiment changed the blocker from startup failure to post-startup broker failure. The best current hypothesis is a timeout or connectivity problem on the sandboxed bridge's localhost broker transport. The exact live OS error remains unknown.

Do not quote `10061` as the live cause here. That code was captured only from the deliberate shutdown unit control, not from the restricted live artifacts. A bounded temporary broker warning logged only safe exception metadata, did not surface in the live MCP stderr, and was removed immediately with no production diff.

Earlier unrestricted live passes from `dc4eb0d` remain superseded and are not accepted as evidence for the required restricted workflow gate.

## Protected Inputs

The protected hashes asserted by the live probes covered the temporary config, compiled agent blueprint, workflow definition, and project file. This was not a Playwright-config claim.

## Remaining Gates

| Gate | State |
| --- | --- |
| Scope-focused deterministic rerun after native repair | Complete at `374a356`: 62 passed, 2 skipped, 1 warning |
| Full deterministic suite at current tip | Pending |
| Full live suite | Pending; required restricted probe still failing |
| Whole-branch review | Pending |
| Integration / merge / push | Not started |

UI counts `474 passed, 2 skipped` and deterministic `2574 passed` are historical checkpoints, not 2026-09-10 reruns. No merge, push, or waiver is claimed. The uncommitted live acceptance tightening in `tests/test_ticket_runtime_live.py`, plus preserved local `config.yaml.example.lock` and `tests/test_records_worker.py`, remain outside this docs-only commit.

## Next Investigation Boundary

The next justified investigation is the sandboxed bridge-to-loopback broker path and its timeout behavior. This record does not prove a Windows Firewall cause, does not prove all configurations impossible, and does not justify broader permission changes, localhost policy grants, bypass enablement, or a CLI upgrade as part of this checkpoint.
