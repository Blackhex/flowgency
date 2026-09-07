# Routines Configuration UI Verification

Date: 2026-09-07
Worktree: `C:/Projekty/Flowgency/.worktrees/routines-ui`
Branch: `feat/routines-ui`
Task 5 implementation source revision: `c4100fa`
Final green UI test-and-snapshot repair revision: `d2a5d57`
Final fix-wave source revision: `e3094b6`
Earlier documentation-only revisions in this evidence trail: `69bac9c`, `a8ef960`
Scope: Task 5 UI gate investigation and repair plus the final routines save/fallback correction wave. Step 5 integration was not executed. Controller final review remains pending.

## Final fix-wave changes made here

- Guarded the routines save path when `blueprint_library` or `prompt_store` is unavailable so the page returns a recoverable `503`, retains the submitted draft, and does not attempt any config write.
- Corrected fallback summary rendering so unsupported saved schedule/recovery warnings remain only while the controlling draft fields still match the unsupported baseline. Once the operator edits those controls to supported values, failed saves now show the current corrected draft summary instead of the stale raw warning text.
- Removed the visible instructional paragraph from the routines heading to match the approved visual contract.
- Added route regressions covering save-time unavailable services, corrected unsupported daily/interval schedules with unrelated validation failures, unchanged unsupported fallback summaries, retained inputs, and no-write behavior.
- Refreshed only the corresponding routines screenshots whose diffs were justified by the removed heading paragraph.

## Task 5 changes made here

- Investigated the nine screenshot failures reported from the Task 5 UI gate and separated fixture-driven text drift from genuine layout regressions.
- Added screenshot-test assertions that verify the live permissions and routines pages still render the correct runtime-derived paths and config revision format before masking those variable text regions.
- Narrowed screenshot masking to only the unstable absolute-path and config-revision text, then refreshed only the affected baselines.
- No application templates, runtime behavior, scheduler logic, or configuration-model code was edited in this repair pass.

## Command evidence

0. Final routines-focused regression and acceptance checks for the fix wave at `e3094b6`:

```text
.venv/Scripts/python.exe -m pytest tests/test_agent_routines.py -q
31 passed, 1 warning in 4.69s

.venv/Scripts/python.exe -m pytest tests/test_routine_editor.py tests/test_routine_presentation.py tests/test_agent_routines.py -q
46 passed, 1 warning in 7.17s
```

Retained warning in the focused runs:

```text
DeprecationWarning from starlette.testclient importing anyio.abc.BlockingPortal
```

1. Prior Python evidence retained from the Task 5 implementation pass because this repair changed only Playwright specs and snapshots:

```text
.venv/Scripts/python.exe -m pytest tests/test_repository_boundaries.py tests/test_team_terminology.py -q
10 passed in 0.28s

.venv/Scripts/python.exe -m pytest tests/ -q
2218 passed, 6 skipped, 1 warning in 278.04s (0:04:38)
```

Retained warning:

```text
DeprecationWarning from starlette.testclient importing anyio.abc.BlockingPortal
```

2. Final full Python suite for the fix wave at `e3094b6`:

```text
.venv/Scripts/python.exe -m pytest tests/ -q
2222 passed, 6 skipped, 1 warning in 279.13s (0:04:39)
```

3. Earlier focused screenshot validation after adding the targeted masks and runtime-value assertions:

```text
npx playwright test tests/ui/agent_permissions.spec.ts tests/ui/agent_routines.spec.ts --project=desktop-light --project=desktop-dark --project=mobile-light --project=mobile-dark --grep "permissions editor layout remains stable|empty permissions state remains stable|preview failure retains a custom tool draft and retry preview recovers the summary|long path permissions state remains stable|empty routines state renders without overflow" --update-snapshots
20 passed in 30.0s
```

4. Final routines screenshot refreshes justified by diff review after removing the prohibited instructional paragraph:

```text
npx playwright test tests/ui/agent_routines.spec.ts --grep "routines layout remains stable for enabled and disabled rows|empty routines state renders without overflow|failed preview preserves the edited ID and retry recovers|long text state wraps without clipping" --update-snapshots
16 passed in 30.2s
```

5. Final full UI suite from the active worktree after the fix wave and corresponding snapshot refreshes:

```text
npm run test:ui
214 passed, 2 skipped in 4.4m
```

Observed nonfatal fixture-server log line during the focused and full UI runs:

```text
Failed to project terminal job job-failed to its decision: 'decision_path'
```

6. Diff hygiene after the acceptance run:

```text
git diff --check
no output
```

## Investigation evidence

The initial failing state from the Task 5 implementation source `c4100fa`, first written up in `69bac9c`, reproduced nine screenshot failures:

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

Artifacts inspected from disk during the investigation:

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

## Root cause

The failing screenshots were not caused by a layout or behavior regression in the rendered pages.

Verified evidence:

- The routines empty-state diff isolated to the footer text `Config revision: ...`.
- The permissions diffs isolated to three text regions only: the footer config revision, the editor path input, and the rendered effective-access paths in the summary column.
- The page structure, controls, spacing, empty-state copy, and save/preview behavior matched between expected and actual images.
- `tests/ui/agent_permissions.spec.ts` originally introduced the permissions baselines in commit `50a31b8` without masking any runtime-derived path or revision text.
- The fixture config changed later in commit `bbc934b` when the routines feature added the `research-digest` routine under the `research.permissions-editor` fixture agent in `tests/ui/fixtures/config.yaml`.
- The UI test server writes the runtime config by loading that fixture YAML, replacing `__RUNTIME__`, and serializing it with `yaml.safe_dump(sort_keys=False)` before the app computes `config_revision` as `sha256(payload)`.
- Recomputing that exact server-side payload hash showed that the current fixture serializes to revision `1df4fd6a80b776c282a4654ae3c4f7b92dcdaf3cfd222b59fa49fd531b095f64`, which matches the rendered footer observed in the failing artifacts.

Conclusion:

- The routines fixture addition changed the canonical config payload and therefore the rendered config revision string.
- The same runtime checkout path also changed the absolute path lengths rendered in the permissions editor and summary.
- Those values are genuinely variable test-fixture output, not user-visible regressions to the permissions or routines UI.

## Repair applied

- Added explicit Playwright assertions that the permissions editor still shows the agent rule path suffix under `tests/ui/.runtime/current/teams/newsletter/editorial` and that the effective-access summary still includes the team workspace path under `tests/ui/.runtime/current/workspaces/newsletter`.
- Added explicit Playwright assertions that the routines empty state still renders a valid 64-hex config revision string.
- Kept screenshots behavior-scoped by masking only the variable absolute-path and config-revision text nodes.
- Pinned the masked footer text width inside the tests so the mask rectangle itself does not drift by a few pixels between runs.
- Refreshed exactly 16 justified baselines: 12 permissions screenshots and 4 routines-empty screenshots. No other snapshots were refreshed.

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
tests/ui/agent_routines.spec.ts-snapshots/agent-routines-empty-desktop-light-win32.png
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

- The full suite still does not persist a dedicated final rename-state screenshot artifact. Rename-state conformance remains covered by the approved rename asset plus the passing browser assertions in `tests/ui/agent_routines.spec.ts`.

- The fix wave removed visible heading copy to satisfy the approved visual contract, so the corresponding routines populated, empty, preview-error, and long-text snapshots were refreshed across the affected desktop and mobile projects after direct diff review.

## Final UI gate result

The repaired UI gate is green for the final fix wave at `e3094b6`:

```text
npm run test:ui
214 passed, 2 skipped in 4.4m
```

The two skipped tests are unchanged pre-existing skips. No routines or permissions UI failures remain after the targeted masking repair.

## Preservation and boundary evidence

The full Python suite evidence from the earlier Task 5 pass still covers the behavior boundaries that this UI repair did not change:

- Unsupported saved timing remains preserved unless edited: `tests/test_routine_forms.py` covers unsupported interval `3600`, unsupported daily time `9am`, and unsupported recovery `later`, plus correction back to supported values.
- Rename and reorder preserve saved provenance: `tests/ui/agent_routines.spec.ts` covers renamed IDs, reorder, and the saved-status origin note.
- Missing prompts stay selected across rerenders and inherited memory falls back correctly: `tests/ui/agent_routines.spec.ts` covers the missing-instance-prompt state and actual inherited-memory label.
- Save does not mutate saved schedule markers or routine memory files: `tests/test_routine_editor.py` includes `test_save_does_not_mutate_saved_history_or_memory_files`.
- Preview and save use the disposable UI fixture configuration, not live config: the Playwright run used `tests/ui/server.py` and `tests/ui/fixtures/config.yaml` via the configured fixture web server.


## Review status

- Task 5 UI gate investigation: complete.
- Final routines save/fallback fix wave: complete at `e3094b6`.
- Python gate: passed, including the final full-suite rerun at `e3094b6`.
- UI gate: passed after targeted masking, the limited Task 5 snapshot repair, and the corresponding routines snapshot refresh required by the visual-contract fix.
- Whole-branch approval is still intentionally withheld. This document is verification evidence only; controller final review remains pending.

## Handoff

- Do not integrate from this Task 5 pass.
- Do not refresh additional baselines beyond the 16 files justified here.
- No live `config.yaml`, main workspace files, or scratch reports were staged or required for this repair.