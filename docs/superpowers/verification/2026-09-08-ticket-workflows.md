# Verification Record — Ticket Workflows (Task 16)

**Branch:** `feat/ticket-workflows`  
**Worktree:** `C:/Projekty/Flowgency/.worktrees/ticket-workflows`  
**HEAD:** `6042b3a` (live test dirty, ignored driver outside Git)  
**Recorded:** 2026-09-10  
**Status:** `BLOCKED` — local-network one-job experiment (HEAD `6042b3a`) applied `sandbox.userPolicy.network.allowLocalNetwork: true` for one isolated job; live acceptance still red. Both `ticket_get` calls returned `Ticket broker is unavailable`; no mutation, artifact, or workspace write reached. TCP connect resolution not proven by this run.

## Scope

This note records Task 16 verification checkpoints and the bounded 2026-09-10 non-Store interpreter experiment. It does not waive any remaining gate and does not replace whole-branch review or integration.

## Latest Outcome

- The isolated non-Store install was completed and measured. `uv` was already present, `uv --no-config python install 3.13.14 --no-bin --no-registry` was unavailable (`catalog not found`), and `uv --no-config python install 3.13` selected CPython `3.13.13` under `.superpowers/python/cpython-3.13.13-windows-x86_64-none`.
- New virtual environment: `.superpowers/venv-cpython`. Original `.venv` Store CPython `3.13.14` remained untouched. No PATH, registry, default-Python, or global-auth settings changed.
- `uv pip freeze` comparison excluding the editable install matched 50 package versions across the two environments. The application remained editable from this feature worktree. The patch difference `3.13.14` vs `3.13.13` is real, so this was not a single-bit-controlled environment swap.
- Test-only commit `a49ecd4` reinjects `__PYVENV_LAUNCHER__` into each generated native `Popen` in the live-process tests. That fixes CPython child portability in tests only. It does not change production launcher policy, sandbox policy, or native process containment.
- The old Store-only symptom, where `flowgency-tickets` tools never appeared, is now historical for this experiment. In the restricted non-Store run the MCP server initialized, tools registered, and `ticket_get` executed.
- Acceptance is still blocked because both live `ticket_get` calls returned `Ticket broker is unavailable`, the ticket stayed in `review`, and no artifact publication or workspace write was reached.
- Broker path diagnostic (HEAD `b889b7b`): parent server-trace showed the control request arriving and completing (HTTP 200, ~45 ms, auth/binding/dispatch returned); no child `request_received` appeared at the server. Child-client-trace (job outbox) showed three `ticket_get` attempts each reaching `connect_start` on 127.0.0.1:53956 and timing out after ~5 s (`TimeoutError`, no `connect_end`). Failure phase confirmed as TCP connect, not server auth or storage lock.
- Local-network one-job experiment (HEAD `6042b3a`, interpreter CPython 3.13.13, Copilot 1.0.84-3): a cheap wrapper proved an exact one-leaf delta — `sandbox.userPolicy.network.allowLocalNetwork: true` added; all other settings (sandbox.enabled true, allowBypass false, workspace read-only, gitAuth/ghAuth false, no allowOutbound) unchanged. Persisted settings for job `4ba622233cc34d0690080e5f71e35192` confirmed the grant on disk. Pytest selected 1 / failures 1 / errors 0 / skips 0 / time 44.059 s. Tool counts: `ticket_get` ×2, zero start/artifact/report/transition. Both `ticket_get` calls returned `Ticket broker is unavailable`; ticket stayed `review`; no workspace write, artifact, or protected-hash assertion was reached. `exit_code 0` means the CLI session ended cleanly, not acceptance success. No product source, global Copilot config, firewall, or loopback exemption was changed; this was one isolated job, not a permanent default.

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
| Broker path diagnostic | `b889b7b` + instrumented live probe | Parent control HTTP 200 ~45 ms; child `TimeoutError` ~5 s × 3, no `connect_end`; TCP connect confirmed; post-cleanup: 1 pass, 25 deselected, 1 warning | `task-16c-broker-path-runtime/server-trace.jsonl`, job outbox `child-client-trace.jsonl` |
| Local-network one-job experiment | `6042b3a` + ignored driver | 1 failed, 4 deselected, 1 warning, 44.07 s; `ticket_get` ×2, broker unavailable, no mutation/write/protected-hash reached | `task-16c-local-network.xml`, `task-16c-local-network-runtime/policy-summary.json`, job `4ba622233cc34d0690080e5f71e35192/.copilot/settings.json` |

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

### Broker path diagnostic

Server-trace (`task-16c-broker-path-runtime/server-trace.jsonl`): parent control `request_received` t=1789029277.817, response 200 at t=277.862 (~45 ms); auth, binding, and dispatch all returned. No child `request_received` in the server trace.

Child-client-trace (job outbox `child-client-trace.jsonl`): three `ticket_get` real attempts; `connect_start` 127.0.0.1:53956 → `TimeoutError` at 5016 ms, 5010 ms, 5001 ms; no `connect_end` or response. No WinError numeric (`TimeoutError` is a subclass of `OSError`).

Post-cleanup broker-check: 1 pass, 25 deselected, 1 warning. No production diff.

### Root-cause boundary

The broker path diagnostic (without network grant, earlier instrumented baseline) confirmed the failure is at the TCP connect phase. Child attempts to connect to 127.0.0.1 timed out before completion; the parent control reached the server healthy. No WinError numeric is present; `TimeoutError` is a subclass of `OSError`. The blocker is not server auth or storage lock deadlock.

Do not quote `10061` as the live cause here. That code was captured only from the deliberate shutdown unit control, not from the restricted live artifacts. A bounded temporary broker warning logged only safe exception metadata, did not surface in the live MCP stderr, and was removed immediately with no production diff. Do not claim default loopback denial is proven by `copilot help sandbox`, that Windows Firewall is the culprit, or that the child is not AppContainer based on parent manifest inspection alone.

The local-network one-job experiment applied `allowLocalNetwork: true` and the job still observed broker unavailable. Whether TCP connect now completes under this setting is not yet instrumented — the earlier TCP-timeout evidence was captured in a baseline run without the network grant, and the latest run did not include child connect-phase tracing.

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

The local-network one-job experiment is done and failed. The next minimal candidate, if approved, is a single instrumented replay combining the `allowLocalNetwork: true` wrapper with the earlier child connect-phase trace, so the remaining boundary can be narrowed to `TCP connect still blocked under this setting` versus `later broker/source failure after connect`. Alternatively, inspect the actual CLI→MCP translation to determine why the flag may not be restoring broker connectivity. Do not claim the network grant alone is sufficient, that TCP connect is now resolved, or that any broader permission or firewall change is needed. No merge, push, or waiver is claimed.

## Task 3 — HTTP transport + consent live acceptance (2026-09-10, supersedes the BLOCKED restricted result)

**Status:** the required restricted live ticket acceptance now **PASSES** through the
approved per-agent `integration_config.allow_local_network: true` consent. This
supersedes the "restricted live is unattainable" conclusion above: that conclusion was
measured *without* the consent path, which the user subsequently approved. The consent is
the only delta — no sandbox knob was widened by the test, no test-side sandbox patch or
permission override was used, and no real user config was edited.

Interpreter `./.superpowers/venv-cpython/Scripts/python.exe` (CPython 3.13.13, mcp SDK);
Copilot CLI `1.0.84-3` (authenticated), model `gpt-5.6-sol`. Commands ran through
controller-owned `Diagnostics/*` tasks (`run_in_terminal` was stuck in an alternate
buffer); `.vscode/tasks.json` was not staged.

### Live acceptance (`-m real_runtime`)

```text
pytest tests/test_ticket_runtime_live.py -m real_runtime -k requires_explicit_local_network -v
=> 1 passed, 2.23s   (preflight: no-consent restricted ticket submission raises
                      ValidationFailed code ticket-local-network-required at
                      resolve_job_request, before any job record or CLI launch)

pytest tests/test_ticket_runtime_live.py -m real_runtime -k reads_ticket_over_http_without_editing -v
=> 1 passed, 25.57s  (restricted read/search-only run WITH consent calls ticket_get over
                      MCP HTTP; ticket untouched; protected hashes unchanged)

pytest tests/test_ticket_runtime_live.py -m real_runtime -k restricted_workspace_keeps_ticket_flow_and_records_denied_write -vv
=> 1 passed, 86.17s  (restricted full artifact flow; the model's apply_patch write to
                      blocked-note.txt was denied by the sandbox — success:false,
                      "Outside the sandbox's writable paths.", sandbox_denied:true;
                      write_attempts == ['blocked-note.txt']; retained artifact bytes ==
                      on-disk result; protected hashes unchanged; active_run cleared)

pytest tests/test_ticket_runtime_live.py -m real_runtime -k one_run_creates_updates_and_transitions_multiple_tickets -v
=> 1 passed, 107.19s (one run started/updated/reported/transitioned ticket A to done AND
                      created+operated ticket B; the dispatch observer captured
                      ticket_create and ticket_update strictly before the terminal
                      ticket_transition)

pytest tests/test_ticket_runtime_live.py -m real_runtime -v
=> 5 passed, 3 deselected, 1 warning, 392.64s (0:06:32)
   (adds the unrestricted presatisfied full-flow control)
```

### Deterministic cross-layer lifecycle (real broker/service/provider/job lifecycle)

```text
pytest tests/test_ticket_end_to_end.py -q -k 'sign_off or stale or committed_transition_survives'
=> 3 passed, 4.14s
   - sign-off is optional (the ticket reaches done with no signed-off event; sign-off,
     when used, releases the assignment)
   - a stale transition is rejected as stale-ticket after a genuine intervening update,
     then succeeds on refresh
   - a committed transition survives a timed-out run (exit 124 + confirmed
     ProcessStopEvidence): state stays done, assignment and retained artifact intact,
     active_run cleared

pytest tests/test_copilot_output.py -q
=> 22 passed (adds apply_patch denied-write and add/update/delete change regressions)

pytest tests/test_copilot_output.py tests/test_copilot_ticket_tools.py \
  tests/test_ticket_runtime_live.py tests/test_ticket_end_to_end.py \
  tests/test_job_execution.py -m 'not real_runtime' -q
=> 77 passed, 5 deselected
```

### Production change

`flowgency/integrations/flowgency/copilot.py` — the output parser now recognises the
`apply_patch` tool used by `gpt-5.6-sol`, whose target files live in the patch envelope
rather than an `arguments.path` field. A sandbox-denied patch is now recorded as a real
write attempt, and a successful patch contributes add/modify/delete changes. Without this,
a genuine denied write over `apply_patch` left no telemetry. The 64KiB result contract and
the request field-type catalog were unchanged.

### Still pending for the whole feature

Full deterministic + UI matrix rerun under the transport/consent change, whole-branch
review, and integration gates. No feature-complete claim is made here.
