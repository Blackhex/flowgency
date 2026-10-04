# Task 2 Report: JSON Snapshot Consumption And Stable DOM Reconciliation

## Result

Status: DONE

Commit: `df8552657f57b003fcb4b67d4e1236961aad759a`

Message: `fix(workflows): reconcile polling without page swaps`

Branch: `feature/workflow-live-reconciliation`

Worktree: `C:/Projekty/Flowgency/.worktrees/workflow-ui`

## Files Changed

- `flowgency/static/workflow-board-view.js` added.
- `flowgency/static/workflow-board.js` updated.
- `flowgency/templates/workflow_board.html` updated.
- `flowgency/templates/ticket_detail.html` updated.
- `tests/ui/workflow_board.spec.ts` updated.

No backend/runtime/dependency/configuration files were changed.

## Interfaces Implemented

- `new WorkflowBoardView(page, boardUrl, initialBoard)` captures the page hosts, initial binding schema, editable field signature, templates, and current keyed card map.
- `WorkflowBoardView.refKey(ref)` exports the scoped ticket identity tuple.
- `view.inspectBoard(board, selectedTicketId)` validates binding identity, presentation format, column/card/field uniqueness, counts, selected ticket identity, and editable signature before mutation.
- `view.renderBoard(board, selectedTicketId)` reconciles header totals, board issues, columns, cards, selected state, and selected detail fragments.
- `view.renderTicket(detail)` updates read-only selected ticket state/title/run status/presentation fragments and matching visible-card metadata without replacing live controls.
- `view.deferOrApply`, `view.flushDeferred`, and `view.clearDeferred` own held-node deferral and stale callback cancellation.
- `controller.applyBoardSnapshot(board)` validates first, updates safe board content, adopts compatible selected detail, and records success/failure for ETag retention.
- `controller.applyDetailSnapshot(detail, { source, submittedValues, committedControl })` is the shared selected-detail adoption path for poll and action responses.

## RED

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps the assignee node' --project=desktop-light --reporter=list
```

Exit: 1

Output:

```text
Running 1 test using 1 worker
  x  1 ... polling keeps the assignee node and never fetches page html (1.1s)

Error: expect(received).toBe(expected) // Object.is equality
Expected: true
Received: false
tests\ui\workflow_board.spec.ts:534

1 failed
```

The failure was the intended one: the original `#ticket-assignee` ElementHandle was disconnected after passive polling.

## Iteration Checks

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps the assignee node' --project=desktop-light --reporter=list
```

Exit: 1 after initial implementation.

Output:

```text
Expected Remote card label link to be visible, element not found.
```

Cause: column reconciliation used `column.kind`, which is not unique for state columns. Fixed by using `column.key`.

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps the assignee node' --project=desktop-light --reporter=list
```

Exit: 1 after column key fix.

Output:

```text
Expected assignee POST response ok() to be truthy; received false.
```

Cause: the existing assignee route intentionally returns a `303` browser-observed POST redirect. The regression now asserts status `303` and verifies persisted assignee through the detail snapshot.

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling reconciles moved and removed cards' --project=desktop-light --reporter=list
```

Exit: 1 on first structural implementation.

Output:

```text
Expected moved card ElementHandle to remain connected; received false.
```

Cause: the source column removed a globally desired card before the destination column could move it. Fixed with a board-wide desired card key set.

## GREEN / Covering Checks

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps the assignee node' --project=desktop-light --reporter=list
```

Exit: 0

Output:

```text
Running 1 test using 1 worker
  ok 1 ... polling keeps the assignee node and never fetches page html (1.1s)
1 passed (3.9s)
```

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps the assignee node|expanded polling updates header totals' --project=desktop-light --reporter=list
```

Exit: 0

Output:

```text
Running 2 tests using 1 worker
  ok polling keeps the assignee node and never fetches page html
  ok expanded polling updates header totals without replacing held ticket chrome
2 passed (4.8s)
```

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling reconciles moved and removed cards' --project=desktop-light --reporter=list
```

Exit: 0

Output:

```text
Running 1 test using 1 worker
  ok polling reconciles moved and removed cards with stable keyed nodes (778ms)
1 passed (3.6s)
```

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps|expanded polling|visible polling|older selection|hidden pages|polling reconciles moved and removed cards' --reporter=list
```

Exit: 0

Output:

```text
Running 24 tests using 1 worker
24 passed (48.4s)
```

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; .\.venv\Scripts\python.exe -m pytest tests/test_workflow_routes.py tests/test_ticket_routes.py -q
```

Exit: 0

Output:

```text
105 passed, 1 warning in 101.66s (0:01:41)
Warning: Starlette TestClient deprecation for anyio.abc.BlockingPortal.
```

Command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; git diff --check
```

Exit: 0

Output: no output.

Touched diagnostics:

```text
No errors found in:
flowgency/static/workflow-board.js
flowgency/static/workflow-board-view.js
flowgency/templates/workflow_board.html
flowgency/templates/ticket_detail.html
tests/ui/workflow_board.spec.ts
```

## Scope Review

- Passive refresh now fetches `initial.urls.snapshot` with JSON, parses before stale-response ordering checks, and applies only compatible snapshots.
- Board ETag is retained only after `applyBoardSnapshot` succeeds; incompatible snapshots clear the retained board ETag.
- Intentional navigation, filter submissions, card clicks, expand/close, create dialog, edit state, forms, action queue, and route contracts remain in the existing controller paths.
- Page HTML replacement remains only for intentional navigation and form/create flows that already used it.
- No redesign, dependency, backend model, route, runtime, configuration, permission, or full-suite policy changes were made.

## Concerns

- Full Python/browser gates were not run by this task per the explicit instruction not to run paid/full gates. Focused browser matrix and named backend route coverage passed.
- The assignee POST is still an intentional `303` route contract; the regression verifies persisted state after that redirect rather than treating the POST response as `ok()`.

## Fix Round 1: Scoped Reference Validation

Status: DONE

Commit: `1b103389a379a1bfab82cdad76189369312e2c3a`

Commit message: `fix(workflows): reject malformed polling refs`

Files changed:

- `flowgency/static/workflow-board-view.js`
- `tests/ui/workflow_board.spec.ts`
- `.superpowers/sdd/2026-10-04-workflow-live-reconciliation/task-2-report.md`

Fixes:

- `inspectBoard` now validates every card and selected-ticket reference before controller adoption or DOM rendering.
- Valid scoped references must be objects with nonempty string `binding_id`, `team_id`, `workflow_id`, and `ticket_id`.
- Card and selected-ticket reference binding/team/workflow values must match the loaded board binding.
- Invalid snapshots are rejected before model identity, ticket version, dirty draft, DOM identity/content, or response ETag adoption.

RED command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling rejects invalid scoped refs' --project=desktop-light --reporter=list
```

RED exit: 1

RED output:

```text
Running 1 test using 1 worker
  x  1 ... rejects invalid scoped refs before adopting model or DOM changes (836ms)

Error: expect(received).toBe(expected) // Object.is equality
Expected: true
Received: false
tests\ui\workflow_board.spec.ts:732
```

GREEN command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling rejects invalid scoped refs' --project=desktop-light --reporter=list
```

GREEN exit: 0

GREEN output:

```text
Running 1 test using 1 worker
  ok 1 ... rejects invalid scoped refs before adopting model or DOM changes (2.6s)
1 passed (5.5s)
```

Focused browser matrix command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps|expanded polling|visible polling|older selection|hidden pages|polling reconciles moved and removed cards|polling rejects invalid scoped refs' --reporter=list
```

Focused browser matrix exit: 0

Focused browser matrix output:

```text
Running 28 tests using 1 worker
28 passed (40.9s)
```

Syntax command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; node --check flowgency/static/workflow-board-view.js; node --check flowgency/static/workflow-board.js
```

Syntax exit: 0

Syntax output: no output.

Touched diagnostics:

```text
No errors found in:
flowgency/static/workflow-board-view.js
flowgency/static/workflow-board.js
tests/ui/workflow_board.spec.ts
```

Scope review:

- No backend, security, model, dependency, snapshot, tolerance, Task 3, or Task 4 changes were made.
- Successful keyed moves, focus/selection deferral, sanitized fragments, visibility handling, navigation ordering, action ordering, and the actual `303` persisted-state contract were left unchanged.
- `git diff --check` exited 0. Git reported the existing line-ending warning for `flowgency/static/workflow-board-view.js`.

Concerns:

- Full Python/browser gates and paid/live gates were not run, per the explicit fix-round instruction.
- The three known external Python live-Copilot failures remain unchanged and were not re-run in this focused fix round.

## Fix Round 2: Invalid Ref Test Evidence Repair

Status: DONE

Commit: this atomic test-fix commit; final hash recorded after commit creation.

Files changed:

- `tests/ui/workflow_board.spec.ts`
- `.superpowers/sdd/2026-10-04-workflow-live-reconciliation/task-2-report.md`

Fixes:

- Strengthened the invalid scoped-ref regression to assert the exact
  `inspectBoard` rejection reason for each corrupt card or selected-ticket
  reference.
- Added a valid-fixture control assertion before each invalid candidate.
- Removed the non-discriminating column and total count increments so all
  column-count, array, and schema invariants stay valid except the deliberately
  bad reference.
- Kept visible partial-application traps with a changed card title and changed
  board heading before asserting no DOM, model, version, draft, or ETag adoption.

RED command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling rejects invalid scoped refs' --project=desktop-light --reporter=list
```

RED exit: 1

RED output:

```text
Running 1 test using 1 worker
  x  1 ... rejects invalid scoped refs before adopting model or DOM changes (856ms)

Error: expect(received).toMatchObject(expected)

- Expected  - 1
+ Received  + 1

  Object {
    "ok": false,
-   "reason": "invalid-card-ref",
+   "reason": "invalid-column-count",
  }

tests\ui\workflow_board.spec.ts:730
1 failed
```

GREEN command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling rejects invalid scoped refs' --project=desktop-light --reporter=list
```

GREEN exit: 0

GREEN output:

```text
Running 1 test using 1 worker
  ok 1 ... rejects invalid scoped refs before adopting model or DOM changes (3.5s)
1 passed (6.5s)
```

Focused browser matrix command:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'; npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps|expanded polling|visible polling|older selection|hidden pages|polling reconciles moved and removed cards|polling rejects invalid scoped refs' --reporter=list
```

Focused browser matrix exit: 0

Focused browser matrix output:

```text
Running 28 tests using 1 worker
28 passed (1.1m)
```

Syntax and touched diagnostics:

```text
git diff --check: exit 0
get_errors tests/ui/workflow_board.spec.ts: No errors found
```

Covering slice:

- The repaired test now proves `invalid-card-ref` and `invalid-selected-ref`
  are the rejection branches before polling can adopt model identity, ticket
  version, dirty draft state, DOM nodes, changed card title, changed heading, or
  response ETag.
- No runtime, source, earlier task, backend, configuration, skipped-case,
  placeholder, tolerance, Task 3, or Task 4 changes were made.

Concerns:

- Full Python/browser gates and paid/live gates were not run, per the explicit
  fix-round instruction.
- The three known external Python live-Copilot failures remain unchanged and
  were not re-run in this focused fix round.