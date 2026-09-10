# Verification Record — Ticket Workflows

Branch: `feat/ticket-workflows`
Worktree: `C:/Projekty/Flowgency/.worktrees/ticket-workflows`
Recorded: 2026-09-10
Status: `FEATURE VERIFIED / INTEGRATION PENDING`

## Current transport status

Task 3's HTTP transport amendment is verified at the feature tip `7734fd8`.
Copilot ticket tools use a worker-owned authenticated loopback MCP HTTP
endpoint. Restricted Copilot ticket runs keep local-network access off by
default and require explicit per-agent `integration_config.allow_local_network:
true`; when the field is absent or `false`, Flowgency stops the run before
launch with `ticket-local-network-required`.

This status supersedes the earlier one-job experiments that reported `Ticket
broker is unavailable`. Those failures remain historical checkpoints only; they
do not describe the current transport or the approved consented path.

## Verified evidence

- Real Copilot CLI: `1.0.84-3` on Windows, authenticated.
- Interpreter: CPython `3.13.13` in `./.superpowers/venv-cpython`.
- Post-review full Python gate at `7734fd8`: `2647 passed, 20 skipped, 1 warning
  in 1175.13s (19m35s)` via `.superpowers/venv-cpython/Scripts/python.exe -m
  pytest tests/ -q --tb=short --junitxml=.superpowers/sdd/2026-09-10-ticket-http-transport/feature-post-review-full.xml`.
- Current normal UI gate at `4e419f6`: `490 passed, 2 skipped` in 10.6 minutes
  across four projects. No subsequent substantive UI changes landed; the only
  later UI-file edit was a missing newline in a new keyboard test.
- Real-runtime composition in the full Python gate: `11` included live runtime
  cases, consisting of `6` actual Copilot ticket-workflow scenarios plus `5`
  existing runtime/projector probes. No runtime deselection was used.
- Current skipped-test accounting: `13` of the `20` skips come from the preserved
  untracked retired worker test file; `7` are committed skip conditions that did
  not change during this task. They are not new product skips.
- Known warning: one existing Starlette/AnyIO `BlockingPortal` warning.
- Focused live denied-write proof: `1 passed` for the restricted denied-write
  ticket case, with correlated `callId`, target path, `success:false`, exact-byte
  artifact retention, and `sandbox_denied` metadata.
- Whole-branch review `b0f0d8e..c1d171b` identified F1/F2/F5 as the only
  load-bearing findings. The single fix wave `c1d171b..7734fd8` addressed them,
  and the scoped re-review accepted F1/F2/F5 with the F1 measurement caveat
  retained as an evidence limit.

Restricted live runs preserved the intended ticket boundary: workspace read-only,
no `allowOutbound`, no `sandboxMcpServers=false`, and only the explicit per-job
local-network opt-in when approved.

Credential schema correction, measured on `1.0.84-3`: Flowgency now emits git/gh
settings under the CLI-supported `sandbox.auth.git` and `sandbox.auth.gh` keys.
The earlier top-level `gitAuth` and `ghAuth` keys were logged by this CLI as
unknown and ignored, so they were not the controlling settings on this runtime.
A restricted denied-write live run's job `settings.json` now carries the nested
keys, and its process log shows no unknown-key warning plus a real
`[rust:sandbox_spawn]`, confirming configuration acceptance. This final record
distinguishes that measured acceptance from a separate direct git/gh denial probe:
actual remote git/gh denial was not exercised independently. The accepted runtime
semantics are that the CLI injects those credentials only while the sandbox is
enabled, so confined policies are the only cases where Flowgency can request no
git/gh token injection; an unconfined run leaves the sandbox off.

Built-in file edits are cooperatively policed in-process. The live denial proof
establishes policy refusal of the ticket canary write, not OS-enforced built-in
file-edit containment. Shell commands remain the only path covered by OS sandbox
containment, and the shell backend is unavailable in this measured runtime.

## Historical checkpoints kept for comparison

- Clean worktree baseline: `2222 passed, 6 skipped, 1 warning`.
- Earlier deterministic feature checkpoint: `2574 passed, 7 skipped, 5 deselected, 1 warning`.
- Earlier full UI checkpoint: `474 passed, 2 skipped`.
- Earlier non-consented loopback experiments that reported broker unavailability
  are retained as timeline evidence only and are superseded by the current HTTP
  transport plus explicit consent results above.

## Representative assets and documents

- Approved design assets:
  `docs/superpowers/specs/assets/2026-09-07-ticket-workflows/workflow-overview-desktop.png`
  and `docs/superpowers/specs/assets/2026-09-07-ticket-workflows/workflow-overview-mobile.png`
- Current verified UI screenshots:
  `tests/ui/workflow_board.spec.ts-snapshots/workflow-board-overview-desktop-light-win32.png`
  and `tests/ui/workflow_board.spec.ts-snapshots/workflow-board-mobile-mobile-light-win32.png`
- Live acceptance report:
  `.superpowers/sdd/2026-09-10-ticket-http-transport/task-3-report.md`

## Reviewed limitations and next gate

- F3 remains accepted as a low-risk semantic limitation: typed `number`
  preconditions compare `1` and `1.0` strictly rather than numerically.
- F4 remains accepted as a low-risk runtime limitation: `TicketToolLaunch.headers`
  stays mutable but is copied defensively by consumers and is not persisted.
- Mandatory main verification is still pending: integration on `master`, the
  post-fast-forward full-suite rerun, push, and worktree cleanup have not started.

This record marks the feature as verified in the worktree and explicitly not yet
integrated or published.
