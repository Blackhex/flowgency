# Verification Record — Ticket Workflows (Task 16)

**Branch:** `feat/ticket-workflows`  
**Worktree:** `C:/Projekty/Flowgency/.worktrees/ticket-workflows`  
**HEAD:** `a19af6d`  
**Recorded:** 2026-09-09  
**Status:** `BLOCKED` — required restricted live acceptance is still failing in the measured environment.

## Scope

This note records Task 16 verification checkpoints. It does not claim feature scope was added at verification time, does not waive remaining gates, and does not replace branch review or integration.

## Checkpoints

| Checkpoint | Commit | Result | Evidence |
| --- | --- | --- | --- |
| Clean baseline in the feature worktree | `89c0cb2` | 2222 passed, 6 skipped, 1 warning | `.superpowers/sdd/2026-09-08-ticket-workflows/baseline-clean.txt` |
| Deterministic verification checkpoint | `98345fc` | 2574 passed, 7 skipped, 5 deselected, 1 warning | `.superpowers/sdd/2026-09-08-ticket-workflows/task-16-deterministic-green.txt` |
| Full UI matrix | `9ede8d3` | `npm run test:ui -- --reporter=dot` -> 474 passed, 2 skipped, 10.2m, exit 0 | `.superpowers/sdd/2026-09-08-ticket-workflows/task-16b-ui-final.txt` |
| Native-launch containment repair slice | `374a356` | 62 passed, 2 skipped, 1 warning | superseding section in `.superpowers/sdd/2026-09-08-ticket-workflows/task-16-report.md` |
| Required restricted live probe | `374a356` + uncommitted live test | 1 failed, 4 deselected, 1 warning, 21.47s | superseding section in `.superpowers/sdd/2026-09-08-ticket-workflows/task-16-report.md` |

`89c0cb2` was not `master`; it was the clean baseline rerun inside this feature worktree.

## Commands And Evidence

### Baseline and deterministic

```text
.venv/Scripts/python.exe -m pytest tests/ -q
=> 2222 passed, 6 skipped, 1 warning in 259.19s
```

```text
.venv/Scripts/python.exe -m pytest tests/ -m 'not real_runtime' -q \
  --junitxml=.superpowers/sdd/2026-09-08-ticket-workflows/task-16-deterministic-green.xml
=> 2574 passed, 7 skipped, 5 deselected, 1 warning in 295.97s
```

The deterministic checkpoint is a historical verification checkpoint at `98345fc`, not a full current-tip rerun.

### UI verification

```text
npm run test:ui -- --reporter=dot
=> 474 passed, 2 skipped (10.2m), exit 0
```

No `--update-snapshots` was used in that normal UI green run. The two skips are the desktop members of a mobile-only navigation test.

Snapshot baseline commit `cca63d1` committed 96 remaining PNGs; no dirty PNG set remained after that snapshot commit. Commit `9ede8d3` later regenerated only the eight settings/create/error PNGs.

Approved actual-versus-source UI checks covered eight image pairs plus the corrected create/error images. The reviewed surfaces were UI-scoped and approved by direct comparison against the source assets in [docs/superpowers/specs/assets/2026-09-07-ticket-workflows](../../specs/assets/2026-09-07-ticket-workflows).

Actual snapshot directories reviewed:

| Surface | Actual snapshots | Approved source assets |
| --- | --- | --- |
| Workflow board / ticket | [tests/ui/workflow_board.spec.ts-snapshots](../../../tests/ui/workflow_board.spec.ts-snapshots) | [docs/superpowers/specs/assets/2026-09-07-ticket-workflows](../../specs/assets/2026-09-07-ticket-workflows) |
| Workflow overview / states / transitions | [tests/ui/workflow_library.spec.ts-snapshots](../../../tests/ui/workflow_library.spec.ts-snapshots) | [docs/superpowers/specs/assets/2026-09-07-ticket-workflows](../../specs/assets/2026-09-07-ticket-workflows) |
| Workflow settings / create / storage error | [tests/ui/workflow_settings.spec.ts-snapshots](../../../tests/ui/workflow_settings.spec.ts-snapshots) | [docs/superpowers/specs/assets/2026-09-07-ticket-workflows](../../specs/assets/2026-09-07-ticket-workflows) |

Reviewed filenames include `workflow-ticket-desktop-*`, `workflow-ticket-mobile-*`, `workflow-overview-*`, `workflow-states-*`, `workflow-transitions-*`, `workflow-create-*`, and `workflow-storage-error-*`. Earlier references to non-existent `workflow-ticket-detail-*` and `workflow-library-*` files were inaccurate.

### Live-runtime facts

Authentication evidence came from:

```text
.venv/Scripts/python.exe -m pytest tests/test_runtime_projectors_live.py \
  -m real_runtime -k 'basic and copilot'
=> 1 passed in 19.36s
```

That probe verifies basic CLI authentication only. It does not prove ticket acceptance and does not prove MCP success under restriction.

The recorded Python launcher fact is narrower than the earlier doc claimed: `py --list-paths` showed only a Microsoft Store Python 3.13 installation under WindowsApps. No standard non-Store CPython was present.

Earlier unrestricted live passes at `dc4eb0d` used a temporarily relaxed launcher and are superseded. They are not accepted as read-only or artifact-retention proof, and no hardcoded `42 passed` claim is accepted as live evidence.

### Restored containment and restricted live blocker

Commit `374a356` restored the native ticket MCP launcher because the real Task 9b boundary evidence showed the WindowsApps alias path can escape the Job Object under the relaxed launcher. StdIO EOF was not confirmed as a stop guarantee.

The measured environment also showed MCP unavailable under restriction. That is an observed environment result here, not a universal claim that all configurations fail.

Required restricted probe:

```text
.venv/Scripts/python.exe -m pytest tests/test_ticket_runtime_live.py \
  -m real_runtime -k restricted_workspace -v
=> 1 failed, 4 deselected, 1 warning in 21.47s
```

Observed result recorded in the Task 16 report:

- model output reported the `flowgency-tickets` tools were unavailable before any artifact publication or write attempt
- persisted job state still ended `complete` with exit `0`
- the ticket remained in `review`
- `write_attempts` stayed empty because the tools never became available
- `changed_files` stayed empty

Earlier native and PowerShell errors support an environment hypothesis, but they should not be conflated into a single root-cause claim. The measured statement is narrower: this installed environment blocked the required restricted ticket-tool run before the workflow could proceed.

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

The required failing live probe lives in the uncommitted `tests/test_ticket_runtime_live.py` state referenced by the superseding Task 16 report section. Known untracked files preserved throughout: `config.yaml.example.lock` and `tests/test_records_worker.py`. No SDD artifacts were committed.

## Blocked Requirement And Next Prerequisite

The missing prerequisite is still environmental: an isolated standard non-Store CPython and a Copilot environment compatible with the reviewed native launcher may be needed to retry the restricted live acceptance path. That candidate is untested and not guaranteed.

No partial-live-evidence waiver is accepted here. Existing deterministic, UI, live, and review gates remain mandatory. The only approval this record asks for before more runtime work is the environment change needed to measure the restricted live path again.
