# Agent Permissions UI Verification

Date: 2026-09-07
Worktree: `C:/Projekty/Flowgency/.worktrees/agent-permissions-ui`
Branch: `feat/agent-permissions-ui`
HEAD: `535fcf90d6b22159aca1be1c5afe43a2421e124b`
Scope: Task 7 acceptance steps 1-4 only. Step 5 integration intentionally not executed.

## Command evidence

1. Focused Python acceptance slice

```text
.venv\Scripts\python.exe -m pytest tests/test_permission_relocation.py tests/test_tool_catalog.py tests/test_permission_forms.py tests/test_permission_presentation.py tests/test_permission_editor.py tests/test_agent_permissions.py -q
70 passed, 1 warning in 3.38s
```

Warning retained as requested:

```text
DeprecationWarning from starlette.testclient importing anyio.abc.BlockingPortal
```

2. Full Python suite

```text
.venv\Scripts\python.exe -m pytest tests/ -q
2158 passed, 1 failed, 6 skipped, 1 warning in 266.92s
```

Blocking failure:

```text
tests/test_repository_boundaries.py::test_tracked_tree_omits_prohibited_terms[v2]
```

Observed tracked match:

```text
flowgency/static/lucide.min.js
```

3. Full UI suite

```text
npm run test:ui
106 passed, 4 failed, 1 interrupted, 2 skipped, 39 did not run in 2.3m
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

Dependent interruption:

```text
tests/ui/dashboard.spec.ts:86 [mobile-light] fleet cards expose run timing and the routine link
Interrupted after earlier failures closed the page/context.
```

4. Diff hygiene

```text
git diff --check
exit 0
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
```

Dimensions:

```text
v7 desktop mockup: 1425x1057
v7 mobile mockup: 375x1562
final desktop snapshots: 1440x1000 (light and dark)
final mobile snapshots: 390x949 (light and dark)
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

Limits of the saved visual evidence:

- The stable saved snapshots cover the populated state only.
- Empty, preview-error, and long-path states are exercised by browser tests, but this branch does not store dedicated screenshot artifacts for each of those states.
- The mobile saved snapshot is full-page for the real fixture page, but it is shorter than the tall mockup asset because the real page content is shorter. It does not by itself prove one-to-one parity with every below-the-fold region of the mockup composition.
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

Status: BLOCKED

Blocking finding 1:

- Repository boundary regression: `tests/test_repository_boundaries.py` fails because tracked vendor asset `flowgency/static/lucide.min.js` contains the prohibited substring `v2`.

Blocking finding 2:

- Dashboard visual regression: full Playwright acceptance fails on desktop dashboard and waiting-job snapshots in both light and dark themes, with identical 715-pixel diffs. This is outside the permissions page and needs reviewed fix dispatch rather than local snapshot churn during Task 7 verification.

Non-blocking carried note:

- Task 2 minor remains deferred in the SDD ledger: the discovery-failure tool-catalog test checks warning presence but not the exact `unavailable` version plus generic warning text.

## Acceptance result for steps 1-4

- Step 1: Partially complete. Focused Python slice passed. Full Python and full UI gates remain blocked by the failures listed above.
- Step 2: Partially complete. Approved v7 assets and final permissions snapshots were inspected, and the primary layout contract matches. Saved evidence remains incomplete for all requested alternate states, and branch-wide UI acceptance is blocked by unrelated dashboard failures.
- Step 3: Complete. Active producers and consumers in the worktree use the relocated sibling permissions model, with old nested references retained only for rejection diagnostics and their tests.
- Step 4: Complete for documentation and review reporting. Verification evidence, blockers, warnings, skips, and catalog limitations are recorded here without making unsupported claims.

Controller handoff:

- Do not integrate from Task 7.
- Dispatch reviewed fixes for the repository-boundary vendor-term failure and the dashboard screenshot regressions.