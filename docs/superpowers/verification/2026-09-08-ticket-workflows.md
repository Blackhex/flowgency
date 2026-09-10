# Verification Record — Ticket Workflows

Branch: `feat/ticket-workflows`
Worktree: `C:/Projekty/Flowgency/.worktrees/ticket-workflows`
Recorded: 2026-09-10
Status: `PENDING FINAL GATES`

## Current transport status

Task 3's transport amendment is verified at the feature tip that includes the
CLI log-level regression fix (`44aa081`). Copilot ticket tools use a
worker-owned authenticated loopback MCP HTTP endpoint. Restricted Copilot
ticket runs keep local-network access off by default and require explicit
per-agent `integration_config.allow_local_network: true`; when the field is
absent or `false`, Flowgency stops the run before launch with
`ticket-local-network-required`.

This status supersedes the earlier one-job experiment that still reported
`Ticket broker is unavailable`. That result remains historical only; it does not
describe the current transport or the approved consented path.

## Verified evidence

- Real Copilot CLI: `1.0.84-3` on Windows, authenticated.
- Interpreter: CPython `3.13.13` in `./.superpowers/venv-cpython`.
- Full current live ticket suite: `6 passed, 4 deselected, 1 warning in 591.98s`.
- Focused correlated denial proof: `1 passed in 222.03s` for the restricted
  denied-write live case, with the same call showing `success:false` and
  sandbox policy denial metadata.
- Full current deterministic CLI slice after the log-level fix at `44aa081`:
  `68 passed`.

Restricted live runs preserved the intended boundary: workspace read-only,
the nested `sandbox.auth.git=false` and `sandbox.auth.gh=false` credential
denial, no `allowOutbound`, no `sandboxMcpServers=false`, and only the explicit
per-job local-network opt-in when approved.

Credential schema correction (measured on `1.0.84-3`): Flowgency now emits git/gh
injection under the CLI's supported `sandbox.auth.git`/`sandbox.auth.gh` keys.
The earlier top-level `gitAuth`/`ghAuth` keys were logged by this CLI as
`Ignoring unknown top-level key(s) ... have no effect`, so the intended denial
had not actually taken effect at that version. A restricted denied-write live
run's job `settings.json` now carries the nested keys, and its process log shows
no unknown-key warning and a real `[rust:sandbox_spawn]`, confirming acceptance.
Because the CLI injects git/gh only while the sandbox is enabled, the denial is
enforceable exactly for confined (restricted or authored-rule) policies — the
read-only ticket-agent case — and cannot bind an unconfined run whose sandbox is
off.

Built-in file edits are cooperatively policed in-process. The live denial proof
establishes policy refusal of the ticket canary write, not OS-enforced file-edit
containment. Shell commands remain the only path covered by OS sandbox
containment, and the shell backend is unavailable here.

## Historical checkpoints kept for comparison

- Clean worktree baseline: `2222 passed, 6 skipped, 1 warning`.
- Earlier deterministic feature checkpoint: `2574 passed, 7 skipped, 5 deselected, 1 warning`.
- Earlier full UI checkpoint: `474 passed, 2 skipped`.
- Earlier non-consented loopback experiments that reported broker unavailability
  are retained as timeline evidence only and are superseded by the current HTTP
  transport plus explicit consent results above.

## Representative assets and documents

- Verified board screenshot source for README replacement:
  `tests/ui/workflow_board.spec.ts-snapshots/workflow-board-overview-desktop-light-win32.png`
- Live acceptance report:
  `.superpowers/sdd/2026-09-10-ticket-http-transport/task-3-report.md`

## Still pending

- Final full UI rerun at the current transport/consent tip.
- Final full deterministic suite rerun at the current tip.
- Whole-branch review.

No feature-complete claim is made until those gates finish.
