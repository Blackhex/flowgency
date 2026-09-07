# Agent Permissions UI Verification

Date: 2026-09-07
Worktree: `C:/Projekty/Flowgency/.worktrees/agent-permissions-ui`
Branch: `feat/agent-permissions-ui`
HEAD: `50a31b82b978d1576ea76870483c3bb80f9b1797`
Scope: Task 7 acceptance steps 1-4 only. Step 5 integration intentionally not executed.

## Command evidence

1. Focused boundary and catalog regression slice

```text
.venv\Scripts\python.exe -m pytest tests/test_repository_boundaries.py tests/test_tool_catalog.py -q
15 passed in 0.25s
```

2. Focused permissions UI evidence refresh

```text
npx playwright test tests/ui/agent_permissions.spec.ts --update-snapshots
40 passed in 52.2s
```

Alternate-state snapshots written by that focused UI run:

```text
agent-permissions-empty-{desktop,mobile}-{light,dark}-win32.png
agent-permissions-preview-error-{desktop,mobile}-{light,dark}-win32.png
agent-permissions-long-path-{desktop,mobile}-{light,dark}-win32.png
```

3. Full Python suite

```text
.venv\Scripts\python.exe -m pytest tests/ -q
2159 passed, 6 skipped, 1 warning in 271.00s (0:04:31)
```

Warning retained as requested:

```text
DeprecationWarning from starlette.testclient importing anyio.abc.BlockingPortal
```

4. Full UI suite

```text
npm run test:ui
154 passed, 4 failed, 2 skipped in 3.1m
```

Blocking failures:

```text
tests/ui/dashboard.spec.ts:34 [desktop-light] dashboard reports selected group pipeline and durable job semantics
tests/ui/dashboard.spec.ts:50 [desktop-light] jobs expose waiting, failed artifact, diagnostics hash, and empty state
tests/ui/dashboard.spec.ts:34 [desktop-dark] dashboard reports selected group pipeline and durable job semantics
tests/ui/dashboard.spec.ts:50 [desktop-dark] jobs expose waiting, failed artifact, diagnostics hash, and empty state
```

Observed screenshot regression details:

```text
dashboard.png: 715 pixels different in each desktop theme
waiting-job.png: 715 pixels different in each desktop theme
```

Investigation outcome:

```text
waiting-job desktop diff localizes to the 32x32 logo box at x=16..47, y=44..75
dashboard desktop diff includes that logo box plus small top-card regions
the visible newsletter agent/sidebar counts still match the fixture expectations
the added research.permissions-editor fixture does not justify refreshing these dashboard baselines yet
```

5. Diff hygiene

```text
git diff --check
no whitespace or merge-marker errors; Git emitted line-ending warnings only
```

## UI comparison against approved v7 assets

Approved assets inspected:

```text
docs/superpowers/specs/assets/2026-09-06-agent-permissions-ui/permissions-short-labels-v7.png
docs/superpowers/specs/assets/2026-09-06-agent-permissions-ui/permissions-short-labels-v7-mobile.png
```

Final saved snapshots inspected:

```text
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-desktop-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-desktop-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-mobile-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-mobile-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-empty-desktop-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-empty-desktop-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-empty-mobile-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-empty-mobile-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-preview-error-desktop-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-preview-error-desktop-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-preview-error-mobile-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-preview-error-mobile-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-long-path-desktop-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-long-path-desktop-dark-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-long-path-mobile-light-win32.png
tests/ui/agent_permissions.spec.ts-snapshots/agent-permissions-long-path-mobile-dark-win32.png
```

Dimensions:

```text
v7 desktop mockup: 1425x1057
v7 mobile mockup: 375x1562
final desktop snapshots: 1440x1000 (light and dark)
final mobile populated snapshots: 390x949 (light and dark)
final mobile alternate-state snapshots: 390x844 viewport captures (light and dark)
```

Observed matches with the approved layout contract:

- Desktop keeps the editor on the left and the effective summary on the right.
- Mobile stacks the editor before the summary.
- The page uses `Permissions`, `Mode`, `Rules`, `Path`, `Tools`, `Discard changes`, and `Save permissions` labels.
- There are no Target selectors.
- There is no separate inherited-rules panel.
- The workspace-write status remains visible near the section header.
- Source provenance remains visible in the summary through Team or Agent links.
- `Path` and `Tools` appear with matching normal-weight label treatment.

Intentional sample-data differences from the mockup, not treated as regressions:

- Production snapshots use real Flowgency navigation, branding, agent identity, and worktree fixture paths instead of the mockup shell.
- Real saved policies display concrete workspace paths and provenance links rather than illustrative placeholder values.

Saved visual evidence now covers these states:

- Populated state
- Empty rules state
- Preview-error state with retry and reload actions visible
- Long-path state on both desktop and mobile

Remaining limit:

- The mobile populated snapshot is full-page for the real fixture page, but it is shorter than the tall mockup asset because the real page content is shorter. It does not by itself prove one-to-one parity with every below-the-fold region of the mockup composition.
- Because the full UI suite is red on unrelated dashboard screenshots, final branch-wide visual acceptance is blocked.

## Config authority audit

Audit command:

```text
git -C C:/Projekty/Flowgency/.worktrees/agent-permissions-ui grep -nE "runtime\.permissions|permissions\.mode|permissions\.rules|permissions:" -- flowgency tests kb README.md AGENTS.md config.yaml.example .github/skills examples
```

Audit outcome:

- Active examples and setup content use sibling `permissions:` blocks in `AGENTS.md`, `config.yaml.example`, both example READMEs, `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`, and `tests/ui/fixtures/config.yaml`.
- Active model and policy consumers read `team.permissions` and `agent.permissions` in `flowgency/configuration/effective.py`, `flowgency/configuration/paths.py`, and `flowgency/integrations/__init__.py`.
- Remaining `runtime.permissions` strings in tracked worktree sources are limited to the explicit relocation rejection message in `flowgency/configuration/models.py` and the matching assertion in `tests/test_permission_relocation.py`.
- No evidence found that the feature branch still writes canonical owner configuration through the old nested runtime location.
- No production or live config files outside the feature worktree were edited during this verification.

## Tool catalog truthfulness

- Real Copilot catalog evidence in this branch remains intentionally incomplete.
- The feature does not claim a universal exhaustive tool list.
- Known catalog names are limited to the documented Copilot permission identifiers `read`, `search`, and `write`.
- Applicability is still unknown for those names, so the UI cannot truthfully claim complete path versus no-path coverage for the real integration.
- Summary text describing `all tools, including future tools` refers only to an unbounded configured policy row when `tools` are omitted. It is not evidence that the runtime catalog is exhaustive.

## Whole-branch review findings

Status: NOT COMPLETE

Resolved in this Task 7 acceptance pass:

- Repository boundary scan now excludes exact content scanning for `flowgency/static/lucide.min.js` only. Tracked path scanning remains unchanged.
- Task 2 discovery-failure coverage now asserts the exact public fallback contract: `version == "unavailable"` and warning `Tool availability could not be determined.`

Open blocking finding:

- Dashboard visual regression: full Playwright acceptance fails on desktop dashboard and waiting-job snapshots in both light and dark themes, with identical 715-pixel diffs. This is outside the permissions page and needs reviewed fix dispatch rather than local snapshot churn during Task 7 verification.

## Acceptance result for steps 1-4

- Step 1: Partially complete. Focused regression slices passed, and the full Python suite passed. The full UI gate remains blocked only by the four desktop dashboard snapshot failures listed above.
- Step 2: Partially complete. Approved v7 assets and real saved desktop/mobile images were inspected, including populated, empty, preview-error, and long-path states. The permissions page evidence is complete, but branch-wide UI acceptance remains blocked by unrelated dashboard snapshots.
- Step 3: Complete. Active producers and consumers in the worktree use the relocated sibling permissions model, with old nested references retained only for rejection diagnostics and their tests.
- Step 4: Complete for documentation and review reporting. Verification evidence, blockers, warnings, skips, and catalog limitations are recorded here without making unsupported claims. Final whole-branch review completion is intentionally not claimed in this document.

Controller handoff:

- Do not integrate from Task 7.
- Dispatch a reviewed fix for the remaining dashboard screenshot regressions.