# Verification Record — Ticket Workflows Exact Artifact Repair

Branch: `fix/ticket-artifact-exact-bytes`
Worktree: `C:/Projekty/Flowgency/.worktrees/ticket-artifact-exact-bytes`
Base: `9bcd218`
Recorded: 2026-09-10
Documentation updated: 2026-09-11 (restored transport/version/asset references dropped
from this checkpoint; no new test run performed for this update)
Status: `REPAIR WORKTREE VERIFIED / MAIN INTEGRATION REQUIRED`

## Repair checkpoint scope

This record covers the user-authorized documentation checkpoint for the exact
artifact repair in the dedicated worktree at `9bcd218`. It does not claim that
`master` has been reverified, fast-forwarded, published, or cleaned up. Those
integration actions remain separately required after this checkpoint.

The repair scope is limited to one behavior correction in the live ticket
verification surface: artifact publication must carry the raw genuine pytest
report bytes, with `content_b64` derived unchanged from the actual fixture file,
and the job must successfully read the exact file before
`ticket_artifact_publish`. Strict stored-byte equality, protected hashes, the
actual transition flow, and the restricted-policy denied-write proof remain in
force.

## Retained transport, environment, and asset references

- Current transport (unchanged by this repair): Copilot ticket tools use a
  worker-owned authenticated loopback MCP HTTP endpoint. Restricted Copilot
  ticket runs keep local-network access off by default and require explicit
  per-agent `integration_config.allow_local_network: true`; this repair made
  no policy change to that consent gate.
- Measured runtime: Copilot CLI `1.0.84-3`, non-Store CPython `3.13.13`. The
  repair worktree ran under its own `.venv`; the main-workspace verification
  environment is the separately owned
  `.superpowers/ticket-workflows-integration/venv` (not installed or changed
  by this repair).
- Design and plan references:
  [ticket HTTP transport design](../specs/2026-09-10-ticket-http-transport-design.md),
  [ticket HTTP transport plan](../plans/2026-09-10-ticket-http-transport.md),
  [ticket workflows design](../specs/2026-09-08-ticket-workflows-design.md).
- Approved design assets:
  [workflow overview desktop](../specs/assets/2026-09-07-ticket-workflows/workflow-overview-desktop.png),
  [workflow overview mobile](../specs/assets/2026-09-07-ticket-workflows/workflow-overview-mobile.png).
- Current verified UI screenshots:
  [board overview desktop](../../../tests/ui/workflow_board.spec.ts-snapshots/workflow-board-overview-desktop-light-win32.png),
  [board overview mobile](../../../tests/ui/workflow_board.spec.ts-snapshots/workflow-board-mobile-mobile-light-win32.png).

## Verified worktree evidence

- Complete repair-worktree Python suite at `9bcd218`: `2651 passed, 7 skipped,
  1 warning` in `1097.51s` via `./.venv/Scripts/python.exe -m pytest tests/ -q
  --tb=short --junitxml=C:/Projekty/Flowgency/.superpowers/ticket-workflows-integration/exact-artifact-full.xml`.
- Full runtime composition in that suite remained intact: all `6` actual Copilot
  ticket-workflow scenarios plus `5` existing runtime/projector probes were
  included, with no marker deselection.
- Skip accounting in this worktree is stable and committed: there is no unknown
  retired test file here, and the `7` skips come from committed skip conditions.
- Focused repair checks passed before the full suite: `4` deterministic cases and
  `2` affected live cases passed for exact-byte `content_b64` handling and the
  successful-read-before-publish proof.
- Independent diff review over `ebc8c11..9bcd218` rated spec compliance `PASS`
  and quality `PASS`; the only residual note is the low-risk fixed-width
  timestamp-format assumption in the read-before-publish helper.
- The earlier full UI gate remains applicable: `490 passed, 2 skipped` in `10.6`
  minutes. This repair made no production or UI changes.

Restricted live runs still preserve the intended ticket boundary: the repair did
not change permissions, grants, sandbox policy, or the denied-write proof.

## Credential and evidence limits retained honestly

The existing credential-schema-versus-denial evidence limit remains unchanged.
This record does not expand the earlier claim beyond what was directly measured:
the nested `sandbox.auth.git` and `sandbox.auth.gh` settings were accepted by the
measured Copilot CLI runtime, and the confined denied-write run demonstrated the
expected ticket sandbox boundary. A separate direct remote git/gh denial probe was
not re-run here, and this checkpoint does not claim one.

Built-in file edits remain cooperatively policed in-process. The live denial proof
continues to establish policy refusal of the ticket canary write, not a new or
broader OS-enforced containment claim.

## Historical checkpoints kept for comparison

- Pre-repair baseline on the fresh worktree failed only the retained artifact
  trailing-newline assertion: `2646 passed, 7 skipped, 1 failed` in `1160.59s`.
- The complete repair-worktree suite at `9bcd218` is the current scoped Python
  checkpoint for this repair: `2651 passed, 7 skipped, 1 warning` in `1097.51s`.
- The prior full UI checkpoint remains `490 passed, 2 skipped` in `10.6` minutes
  and is still applicable because this repair did not touch production or UI
  surfaces.

## Related records

- Durable local execution timeline: `.superpowers/ticket-workflows-integration/report.md`
- Repair implementation report: `.superpowers/ticket-workflows-integration/exact-artifact-report.md`
- Independent review: `.superpowers/ticket-workflows-integration/exact-artifact-review.md`

## Next required gate

Main integration work remains required after this documentation checkpoint:
fast-forward integration onto `master`, the post-integration full-suite rerun,
publish steps, and worktree cleanup must be recorded separately when they occur.

This record marks the exact-artifact repair as verified in its dedicated worktree
and preserves the distinction between scoped worktree evidence and later mainline
integration evidence.
