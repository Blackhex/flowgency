# Routines Configuration UI Verification

Date: 2026-09-07
Worktree: `C:/Projekty/Flowgency/.worktrees/routines-ui`
Branch: `feat/routines-ui`
Reviewed implementation revision: `c4100fa`
Scope: Task 5 steps 1-4 only. Step 5 integration was not executed. Controller final review remains pending.

## Task 5 changes made here

- Added the narrow operator workflow note to `kb/dispatch.md` for the Routines tab: ordered editing, Save/Discard behavior, grouped Blueprint and Instance prompt choices, the saved-status timing caveat, and the ID-change warning that schedule markers and routine-scoped memory are not moved.
- Added this verification record only. No schema, runtime, scheduler, or configuration-model code was edited in this Task 5 pass.

## Command evidence

1. Staged-doc boundary check after the `kb/dispatch.md` edit:

```text
.venv/Scripts/python.exe -m pytest tests/test_repository_boundaries.py tests/test_team_terminology.py -q
10 passed in 0.28s
```

2. Full Python suite from the active worktree:

```text
.venv/Scripts/python.exe -m pytest tests/ -q
2218 passed, 6 skipped, 1 warning in 278.04s (0:04:38)
```

Retained warning:

```text
DeprecationWarning from starlette.testclient importing anyio.abc.BlockingPortal
```

3. Full UI suite from the active worktree:

```text
npm run test:ui
run status: interrupted
```

Observed nonfatal fixture-server log line during the UI run:

```text
Failed to project terminal job job-failed to its decision: 'decision_path'
```

Playwright recorded nine failing screenshot artifacts in `test-results/.last-run.json`.
The failing screenshot cases were:

```text
tests/ui/agent_permissions.spec.ts :: permissions editor layout remains stable (desktop-light)
tests/ui/agent_permissions.spec.ts :: permissions editor layout remains stable (desktop-dark)
tests/ui/agent_permissions.spec.ts :: preview failure retains a custom tool draft and retry preview recovers the summary (desktop-light)
tests/ui/agent_permissions.spec.ts :: preview failure retains a custom tool draft and retry preview recovers the summary (desktop-dark)
tests/ui/agent_permissions.spec.ts :: empty permissions state remains stable (desktop-light)
tests/ui/agent_permissions.spec.ts :: empty permissions state remains stable (desktop-dark)
tests/ui/agent_permissions.spec.ts :: long path permissions state remains stable (desktop-light)
tests/ui/agent_permissions.spec.ts :: long path permissions state remains stable (desktop-dark)
tests/ui/agent_routines.spec.ts :: empty routines state renders without overflow (desktop-light)
```

Artifacts inspected from disk:

```text
test-results/agent_permissions-permissions-editor-layout-remains-stable-desktop-light/agent-permissions-diff.png
test-results/agent_permissions-permissions-editor-layout-remains-stable-desktop-dark/agent-permissions-diff.png
test-results/agent_permissions-preview--033c9-review-recovers-the-summary-desktop-light/agent-permissions-preview-error-diff.png
test-results/agent_permissions-preview--033c9-review-recovers-the-summary-desktop-dark/agent-permissions-preview-error-diff.png
test-results/agent_permissions-empty-permissions-state-remains-stable-desktop-light/agent-permissions-empty-diff.png
test-results/agent_permissions-empty-permissions-state-remains-stable-desktop-dark/agent-permissions-empty-diff.png
test-results/agent_permissions-long-path-permissions-state-remains-stable-desktop-light/agent-permissions-long-path-diff.png
test-results/agent_permissions-long-path-permissions-state-remains-stable-desktop-dark/agent-permissions-long-path-diff.png
test-results/agent_routines-empty-routines-state-renders-without-overflow-desktop-light/agent-routines-empty-diff.png
```

4. Diff hygiene after the acceptance run:

```text
git diff --check
no output
```

## Visual comparison

Normative assets inspected:

```text
docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red.html
docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red.png
docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red-mobile.png
docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red-rename.png
```

Saved routines screenshots inspected from disk:

```text
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-desktop-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-desktop-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-mobile-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-mobile-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-long-text-desktop-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-long-text-desktop-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-long-text-mobile-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-long-text-mobile-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-preview-error-desktop-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-preview-error-desktop-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-preview-error-mobile-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-preview-error-mobile-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-empty-desktop-dark-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-empty-mobile-light-win32.png
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-empty-mobile-dark-win32.png
```

Observed matches against the approved routines assets and Task 5 acceptance targets:

- Populated desktop snapshots keep the editor before the summary on the left/right split.
- Populated mobile snapshots stack the editor before the summary.
- Saved Last fired and Next due fields are integrated inside each routine summary row rather than in a separate panel.
- Enabled and Disabled states use the same detail layout. The approved red disabled styling is present in the normative populated and rename assets, and the application summary shows disabled rows with the same field order and saved-data placement.
- The rename-warning state in the approved rename asset matches the implemented behavior checked by the browser test: changing an existing ID warns that schedule markers and routine memory remain under the original ID and are not moved.
- The page does not reintroduce a standalone schedule-status table or a generic inner `Routine` heading in the editor cards.
- Long ID and argument snapshots remain readable without clipping.

Explicit limitation from the inspected artifacts:

- The full suite did not produce a persisted passing rename-state screenshot artifact. Rename-state conformance was checked through the normative rename asset plus the passing browser assertions in `tests/ui/agent_routines.spec.ts`, not through a saved final image file.

## Acceptance gaps found

1. The full UI gate is not green, so Task 5 cannot be marked fully accepted.

2. The one routines-area screenshot failure is limited to the empty desktop-light baseline.

Observed cause from the inspected expected, actual, and diff files:

- The page structure, empty-state layout, and controls match.
- The visible diff is the rendered config revision string near the bottom of the page.
- Expected baseline shows revision `3c22c700...`; actual render shows revision `1df4fd6a...`.
- No broader routines layout regression was visible in the inspected empty-state artifact.

3. The other eight UI failures are in `tests/ui/agent_permissions.spec.ts`, outside the routines task code that this pass was allowed to modify.

Observed cause from the inspected permissions diff artifacts:

- The dominant differences are path- and revision-heavy text regions in desktop screenshots.
- No permissions code or baselines were changed in this Task 5 pass.
- Per the task instruction, these unrelated app or baseline issues were not fixed here and are reported for controller review.

## Preservation and boundary evidence

The full Python suite covered the explicit boundary requirements the task asked to call out:

- Unsupported saved timing remains preserved unless edited: `tests/test_routine_forms.py` covers unsupported interval `3600`, unsupported daily time `9am`, and unsupported recovery `later`, plus correction back to supported values.
- Rename and reorder preserve saved provenance: `tests/ui/agent_routines.spec.ts` covers renamed IDs, reorder, and the saved-status origin note.
- Missing prompts stay selected across rerenders and inherited memory falls back correctly: `tests/ui/agent_routines.spec.ts` covers the missing-instance-prompt state and actual inherited-memory label.
- Save does not mutate saved schedule markers or routine memory files: `tests/test_routine_editor.py` includes `test_save_does_not_mutate_saved_history_or_memory_files`.
- Preview and save use the disposable UI fixture configuration, not live config: the Playwright run used `tests/ui/server.py` and `tests/ui/fixtures/config.yaml` via the configured fixture web server.

## Review status

- Task 5 Step 1: complete.
- Task 5 Step 2: Python gate passed; UI gate failed with nine screenshot artifacts.
- Task 5 Step 3: populated desktop/mobile and supporting routines screenshots were inspected against the four approved assets; the full rename-state artifact is still missing as a persisted final screenshot.
- Task 5 Step 4: this evidence record is complete, but whole-branch approval is intentionally withheld because the full UI gate is not green and controller final review remains pending.

## Handoff

- Do not integrate from this Task 5 pass.
- Do not update baselines blindly. The routines empty-state failure appears to be revision-text drift; the permissions failures are outside this task and need separate review before any snapshot refresh.