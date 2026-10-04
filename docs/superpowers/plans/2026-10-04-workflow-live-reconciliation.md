# Non-Disruptive Workflow Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep workflow data live without collapsing agent selection, replacing active controls, or silently overwriting concurrent changes.

**Architecture:** Poll the existing board JSON snapshot on both board and expanded-ticket pages, and reconcile keyed DOM nodes rather than fetching page HTML. Keep request ordering, draft state, and selection versions in the existing controller; isolate DOM reconciliation in a small view module. Reuse server-rendered read-only markup through additive JSON presentation data and retain the detail snapshot as the existing mutation redirect target.

**Tech Stack:** FastAPI, Pydantic views, Jinja2, the existing `ticket_markdown`/`nh3` sanitizer, native browser APIs, pytest, and the locked Playwright/Chromium matrix.

**Approved Specification:** `docs/superpowers/specs/2026-10-04-workflow-live-reconciliation-design.md`, committed separately as `4909d3a`. No visual sketch was approved; there are no normative sketch asset paths to archive. Preserve the existing rendered layout and committed screenshot baselines.

## Global Constraints

- Keep the two-second polling cadence, conditional ETag requests, hidden-page pausing, and immediate refresh when the document becomes visible.
- Polling must not fetch page HTML, replace the workflow page or inspector, change history or the selected ticket, recreate live controls, or deliberately move focus or scroll positions.
- Never overwrite dirty values.
- Do not automatically rebase or retry the assignment.
- Reject incompatible snapshots before applying partial changes to the view or adopting their mutation versions.
- Retain the ETag only for a snapshot successfully reconciled with the current view.
- Preserve the existing layout, native controls, filters, tabs, edit mode, new-ticket dialog, and JavaScript-disabled forms.
- Do not change canonical configuration, ticket storage, job ownership, workflow transitions, agent permissions, integrations, dependencies, or runtime-local data.
- Run complete Python and browser suites sequentially, never concurrently against the same UI runtime directory.
- Use the existing worktree and its explicit `.venv\Scripts\python.exe`; do not use global-user Python for wheel-isolation checks.
- Use synchronous terminal mode for one-shot commands. If a command detaches, retain its execution ID and receipt path and resume it before doing other work.
- Do not adjust screenshot tolerances, update unrelated baselines, skip a live probe, switch models, widen permissions, or change transport to obtain a green UI gate.
- Commit this plan separately from both its specification and implementation. Use Conventional Commits and review each implementation task before dependent work.
- User-approved exception on 2026-10-04: only the exact three recorded live-Copilot baseline tests listed below may fail, disclosed and executed unchanged; no other failure/error or browser failure is accepted.
- Separate the unintegrated eager-exposure metadata experiment from this UI feature; do not ship integration changes with the board refresh fix.

---

## Baseline Gate And Commands

The worktree is `C:\Projekty\Flowgency\.worktrees\workflow-ui`, on `feature/workflow-live-reconciliation`. Python test extras and locked npm dependencies are already installed there.

The original global-user full run produced 3353 passed, 19 skipped, and three failures. The isolated targeted rerun fixed the two packaging failures but still failed `test_restricted_agent_reads_ticket_over_http_without_editing[copilot]`: the CLI exited zero, made no ticket broker calls, and reported that it could not load a required tool-search tool. This does not establish the actual catalog supplied to the model or a network/authentication failure. The focused receipt is `.superpowers/evidence/workflow-live-reconciliation/baseline-focused.xml`.

**The user explicitly authorized proceeding with the UI fix under a known-failure exception on 2026-10-04, then approved the exact three failures observed in the unchanged full baseline.** The accepted identities, all in `tests/test_ticket_runtime_live.py`, are:

- `test_restricted_agent_reads_ticket_over_http_without_editing[copilot]`
- `test_restricted_agent_refreshes_stale_transition_and_signs_off_second_ticket[copilot]`
- `test_restricted_agent_timeout_after_committed_transition_keeps_ticket_state[copilot]`

No other failure/error or browser failure is accepted. Keep every test and security assertion intact, do not skip or xfail them, and never describe the full suite as green while it remains red. Runtime corrections stay outside this UI plan. Remove the unintegrated eager-exposure source delta from the UI branch before baseline; preserve its commit/evidence without discarding history. Do not repeat standalone paid probes or change models/grants/transport to obtain a green report.

- [ ] Verify the already-resolved packaging environment through unchanged focused checks, without another standalone runtime retry:

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'
& .\.venv\Scripts\python.exe -m pytest tests/test_setup_assets.py::test_documented_workflow_recipe_runs_from_the_wheel_not_the_checkout tests/test_workflow_setup.py::test_installed_distribution_validates_shipped_workflow_examples -q --junitxml=.superpowers/evidence/workflow-live-reconciliation/baseline-packaging.xml
```

Expected: both pass without source, marker, or assertion edits. Prior passing focused evidence may satisfy this environment check; do not repeat it merely for reassurance. The runtime test still executes in the complete suite below.

- [ ] Obtain the complete Python baseline and classify its actual result:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/ -q --junitxml=.superpowers/evidence/workflow-live-reconciliation/baseline-python.xml
```

Expected: no failure/error outside the exact accepted three-test runtime set; retain justified existing platform/runtime skips. If any known failure occurs, retain exit1 and its receipt and report it as a failed full suite with a user-approved exception. Starting the command is not evidence of success. Any additional failure requires a new decision rather than expanding this exception.

- [ ] After Python finishes, obtain the unchanged full browser baseline:

```powershell
$env:PLAYWRIGHT_JUNIT_OUTPUT_FILE = "$PWD\.superpowers\evidence\workflow-live-reconciliation\baseline-ui.xml"
npm run test:ui -- --reporter=list,junit
```

Expected: exit zero in all four default-headless projects. Port 8765 must be free for the test harness; do not reuse or stop an unrelated server. Preserve failure traces and screenshots. Do not overlap this command with pytest.

## File Ownership

| File | Responsibility |
| --- | --- |
| `flowgency/web/ticket_snapshots.py` (new) | Pure web serialization of domain views plus stable presentation data; no configuration writes or domain mutations. |
| `flowgency/templates/_ticket_presentation.html` (new) | Shared read-only description, output, requirement, history, and issue macros, extracted from the current inspector. |
| `flowgency/web/routes/workflows.py` | Apply the board serializer to snapshots and initial JSON; preserve ETag/304 and HTML routing. |
| `flowgency/web/routes/tickets.py` | Apply serializers to detail snapshots and every initial board payload; preserve POST/303 contracts and HTML errors. |
| `flowgency/templates/_ticket_inspector.html` | Existing inspector shell and live forms; shared read-only macros and stable reconciliation attributes. |
| `flowgency/templates/workflow_board.html` | Existing board layout, keyed columns/cards, inert creation templates, read-only hosts, and script load order. |
| `flowgency/templates/ticket_detail.html` | Expanded page read-only/status hosts and matching script load order. |
| `flowgency/static/workflow-board-view.js` (new) | Keyed DOM reconciliation and deferred read-only/card structure updates; no fetches, mutation versions, or draft ownership. |
| `flowgency/static/workflow-board.js` | Existing controller: polling/navigation separation, versions, drafts, action queue, interactions, and refresh status. |
| `flowgency/static/workflow-board.css` | Only required refresh-status/hidden-host layout rules; no restyling. |
| `tests/test_workflow_routes.py`, `tests/test_ticket_routes.py` | Web projection, sanitation, ETags, route compatibility, and HTML fallbacks. |
| `tests/ui/workflow_board.spec.ts` | Existing fixtures and browser behavior regressions; no extra test files. |

The two presentation files eliminate duplicated SSR/JSON rendering. The view module keeps DOM rendering out of a controller that already owns navigation, drafts, and serialized mutations. Do not split other modules or change the Pydantic domain view models.

## Task 1: Stable Web Presentation Without Changing Domain Data

**Files:** Create the web serializer and read-only macro file; modify both route modules and the three existing page/inspector templates; extend the two existing route test files.

**Interfaces:**
- `ticket_snapshot_payload(request, detail: TicketDetailView, agent_options: Sequence[str]) -> dict[str, Any]` preserves existing detail keys and adds `presentation`.
- `board_snapshot_payload(request, board: BoardView, agent_options: Sequence[str]) -> dict[str, Any]` preserves existing board keys, projects selected detail through the same helper, and adds board `presentation`.
- Detail `presentation`: `format: 1`, `agent_options: string[]`, `description_html`, `outputs_html`, `requirements_html`, `history_html`, and `issues_html`, all strings.
- Board `presentation`: `format: 1`, `agent_options: string[]`, and `issues_html`.
- Read-only macros: `description(detail)`, `outputs(detail)`, `requirements(detail)`, `history(detail)`, and `issues(issues)`.

- [ ] **Step 1: Add the failing projection/sanitation regression in the existing ticket route tests.**

```python
@pytest.mark.parametrize("surface", ["board", "detail"])
def test_snapshot_presentation_is_sanitized_and_keeps_raw_data(workflow_web_env, surface):
    env = workflow_web_env
    ticket = env.create(title="Snapshot presentation")
    raw = "**safe**\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert(1))"
    current = env.read(ticket.ref)
    env.service.update(
        env.user,
        current.version,
        current.patch(description=raw),
        env.operation("snapshot-markdown"),
    )
    url = (
        f"{env.base_path}/snapshot?ticket={ticket.ref.ticket_id}"
        if surface == "board"
        else f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"
    )
    response = env.client.get(url)
    assert response.status_code == 200
    payload = response.json()
    detail = payload["selected_ticket"] if surface == "board" else payload
    assert detail["ticket"]["description"] == raw
    assert "body_html" not in detail["ticket"]
    assert detail["presentation"]["format"] == 1
    rendered = detail["presentation"]["description_html"]
    assert "<strong>safe</strong>" in rendered
    assert "<script" not in rendered.lower()
    assert "javascript:" not in rendered.lower()
    configured = env.client.app.state.services.config_store.load()
    agents = configured.config.teams[detail["binding"]["team_id"]].agents
    assert detail["presentation"]["agent_options"] == list(agents)
```

Also add checks that initial `workflow-initial` JSON has the same projection, successful assignment/update/run redirects still reach the canonical snapshot, absent selected tickets remain representable, and the existing deterministic ETag tests hash the entire augmented payload. Use the existing `workflow_web_env`; do not invent a fixture.

- [ ] **Step 2: Run the focused red check.**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_ticket_routes.py -q -k snapshot_presentation
```

Expected: the new test fails on missing `presentation`, not an import or fixture error.

- [ ] **Step 3: Extract the current read-only markup and add serializers.**

Keep artifact links, false/zero values, recorded assessments, historical transition labels, and evidence issues exactly as currently rendered. Move the existing output rows, requirement children, history articles, issue lines, and `render_value` macro into the shared macro file; do not recreate that markup in JavaScript. Add `data-view-key` to repeated rows using field IDs, transition IDs, and event IDs. Within a transition, prefix keys by row kind and field/criterion identity; include event ID for recorded assessments.

Import the shared macros with context in the inspector after its existing `detail`/`current_ticket` assignments. Inside `outputs(detail)`, derive `output_fields` from that argument; inside `requirements(detail)`, derive the current transitions from that argument. These prologues belong inside their respective macros before the unchanged rendering bodies:

```jinja2
{% set output_fields = detail.fields | selectattr('is_output') | list %}
```

```jinja2
{% set transitions = detail.current_definition.transitions | selectattr('from_state', 'equalto', detail.ticket.state_id) | list if detail.ticket else [] %}
```

Do not depend on inspector-local `output_fields` or `transitions` during standalone JSON projection. Keep the shared value macro's URL context supplied by the inspector import and `_presentation_module` below.

The description and issue macros have concrete bodies:

```jinja2
{% macro description(detail) -%}
  {% if detail.ticket %}{{ detail.ticket.description | ticket_markdown }}{% endif %}
{%- endmacro %}

{% macro issues(issues) -%}
  {% for issue in issues %}
  <p data-view-key="issue:{{ issue.code }}:{{ loop.index0 }}"><strong>{{ issue.code }}</strong>: {{ issue.message }}</p>
  {% endfor %}
{%- endmacro %}
```

Use the existing inspector containers as hosts: `data-ticket-description-read`, `data-ticket-output-values` on the output `dl`, and the existing requirements/history panels. Keep an empty hidden output section when outputs are absent so a later snapshot can show it without replacing forms. Add distinct `data-board-issues` and `data-ticket-issues` hosts; never use the action-error banner for these fragments.

Implement the web serialization helper without importing `flowgency.app`:

```python
from collections.abc import Sequence
from typing import Any

from fastapi import Request

from flowgency.tickets.views import BoardView, TicketDetailView, WorkflowBindingView


def _presentation_module(request: Request, binding: WorkflowBindingView, detail=None):
    template = request.app.state.templates.env.get_template("_ticket_presentation.html")
    return template.make_module({
        "detail": detail,
        "current_ticket": detail.ticket if detail is not None else None,
        "team": binding.team_id,
        "active_workflow_id": binding.workflow_id,
    })


def ticket_snapshot_payload(
    request: Request,
    detail: TicketDetailView,
    agent_options: Sequence[str],
) -> dict[str, Any]:
    payload = detail.model_dump(mode="json")
    module = _presentation_module(request, detail.binding, detail)
    payload["presentation"] = {
        "format": 1,
        "agent_options": list(agent_options),
        "description_html": str(module.description(detail)),
        "outputs_html": str(module.outputs(detail)),
        "requirements_html": str(module.requirements(detail)),
        "history_html": str(module.history(detail)),
        "issues_html": str(module.issues(detail.issues)),
    }
    return payload


def board_snapshot_payload(
    request: Request,
    board: BoardView,
    agent_options: Sequence[str],
) -> dict[str, Any]:
    payload = board.model_dump(mode="json")
    module = _presentation_module(request, board.binding, board.selected_ticket)
    payload["presentation"] = {
        "format": 1,
        "agent_options": list(agent_options),
        "issues_html": str(module.issues(board.issues)),
    }
    if board.selected_ticket is not None:
        payload["selected_ticket"] = ticket_snapshot_payload(
            request, board.selected_ticket, agent_options,
        )
    return payload
```

Read configured agent names from the existing config snapshot used by each HTML context. For JSON handlers, load the current config through `services.config_store` and pass the same captured tuple to the serializer; do not use native integration files or write configuration. Replace every initial `board.model_dump(mode="json")` payload in the workflow and ticket page/error renderers with the board serializer. Call serializers before the existing `_json_with_etag`, so presentation/catalog changes participate in the ETag. Leave POST routes and their 303 redirects unchanged.

- [ ] **Step 4: Run the green checks and unchanged HTML fallback checks.**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_ticket_routes.py tests/test_workflow_routes.py -q
npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'javascript-disabled|configured team agent|approved toolbar' --reporter=list
```

Run these sequentially. Expected: the new route tests pass, ETags are deterministic, and shared markup has not changed the existing controls or fallback semantics.

- [ ] **Step 5: Review and commit the independently testable projection.**

Stage only this task's source/templates and two route tests. Review sanitation, missing-definition handling, raw domain data preservation, and the absence of forms/random operation IDs in JSON fragments. Commit as `feat(workflows): project stable ticket presentation`.

## Task 2: Consume Board Snapshots And Reconcile Stable Nodes

**Files:** Create `flowgency/static/workflow-board-view.js`; modify the controller and both page templates; extend `tests/ui/workflow_board.spec.ts`.

**Interfaces:**
- `new WorkflowBoardView(page: HTMLElement, boardUrl: string, initialBoard: BoardWebSnapshot)` captures the loaded binding/control schema and DOM hosts.
- `WorkflowBoardView.refKey(ref: TicketRefJSON) -> string` exports the scoped identity tuple for both the view and controller; `TicketRefJSON` has the existing binding/team/workflow/ticket fields.
- `view.inspectBoard(board, selectedTicketId) -> { ok: boolean, reason: string, unavailable: boolean }` checks the entire candidate before mutation.
- `view.renderBoard(board, selectedTicketId) -> void` updates heading/totals, keyed columns/cards, and board issues; an expanded page has no column host and updates only its header.
- `view.renderTicket(detail) -> void` updates read-only state/title/status/presentation and matching visible-card metadata, not live input values; the next board poll remains authoritative for membership/counts.
- `view.deferOrApply(key: string, node: Node, apply: () => void)`, `view.flushDeferred()`, and `view.clearDeferred()` own deferred read-only/structural work.
- `controller.applyBoardSnapshot(board) -> boolean` inspects first, rebases the selected draft, adopts compatible model data, and renders in place.
- `controller.applyDetailSnapshot(detail, { source, submittedValues, committedControl } = {}) -> boolean` is the shared selected-detail adoption path for polls and later action integration; sources are `poll` or `action`, and `committedControl` defaults to null.
- `BoardWebSnapshot` and `TicketDetailWebSnapshot` mean the existing JSON views with Task 1's additive `presentation`; they are JavaScript data contracts, not new Pydantic models.

- [ ] **Step 1: Add deterministic polling helpers and the root regression.**

Keep helpers in the existing browser spec:

```typescript
type PollController = {
  pollTimer: number;
  refreshBoard: () => Promise<void>;
};

async function stopPollTimer(page: Page): Promise<void> {
  await waitForWorkflowController(page);
  await page.evaluate(() => {
    const controller = (window as typeof window & {
      workflowBoardController: PollController;
    }).workflowBoardController;
    clearTimeout(controller.pollTimer);
  });
}

async function forcePoll(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const controller = (window as typeof window & {
      workflowBoardController: PollController;
    }).workflowBoardController;
    clearTimeout(controller.pollTimer);
    await controller.refreshBoard();
    clearTimeout(controller.pollTimer);
  });
}

test('polling keeps the assignee node and never fetches page html', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const htmlRequests: string[] = [];
  page.on('request', (requestEvent) => {
    const accepted = requestEvent.headers()['accept'] || '';
    if (requestEvent.method() === 'GET' && accepted.includes('text/html')) {
      htmlRequests.push(requestEvent.url());
    }
  });
  const original = await page.locator('#ticket-assignee').elementHandle();
  expect(original).not.toBeNull();
  await page.locator('#ticket-assignee').focus();
  const other = await detailSnapshot(request, 'fixture-backlog-1');
  const changed = await request.post('/newsletter/workflows/delivery/tickets/fixture-backlog-1/update', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: other.ticket.version,
      operation_id: operationId('poll-other-card'),
      patch: { title: 'Remote card label' },
    }) },
  });
  expect(changed.ok()).toBeTruthy();
  await forcePoll(page);
  expect(await original!.evaluate((node) => node.isConnected && node === document.getElementById('ticket-assignee'))).toBe(true);
  await expect(page.locator('#ticket-assignee')).toBeFocused();
  await expect(page.getByRole('link', { name: /Remote card label/ })).toBeVisible();
  expect(htmlRequests).toEqual([]);
  const saved = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.locator('#ticket-assignee').selectOption('builder');
  expect((await saved).ok()).toBeTruthy();
  expect((await detailSnapshot(request)).ticket.assignee).toBe('builder');
  await assertNoConsoleErrors(page);
});
```

Add the expanded-page case using its existing URL. Verify header totals change after an actual ticket creation through the existing create route. Capture page, inspector, dialog, input, and unaffected card handles, not just locators.

- [ ] **Step 2: Run the root regression red.**

```powershell
npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps the assignee node' --project=desktop-light --reporter=list
```

Expected: the original select handle is disconnected or a page HTML request is observed. Fix test setup errors before implementation.

- [ ] **Step 3: Add stable view keys, inert creation templates, and the view module.**

Add `data-column-key` to current columns and `data-ticket-id` to current cards. Use `data-view-key` for DOM reconciliation keys. Put inert `template` elements for a card and column inside the existing board root; reuse the present card/header markup, icons, and CSS classes without introducing visible wrappers. Add specific label targets for column name/count and card title/number/assignment/badge.

Load `/static/workflow-board-view.js` before `/static/workflow-board.js` in both script blocks. Export the IIFE class as `window.WorkflowBoardView`, matching the repository's existing browser pattern. Create a new view after intentional `applyPageHtml`; clear the old view's deferrals rather than attaching another set of document listeners.

Use scoped identity and schema checks:

```javascript
function refKey(ref) {
  return JSON.stringify([ref.binding_id, ref.team_id, ref.workflow_id, ref.ticket_id]);
}

function editableSignature(detail) {
  return JSON.stringify((detail?.fields || [])
    .filter((field) => !field.is_output && field.type !== 'artifact')
    .map((field) => [field.id, field.type])
    .sort(([left], [right]) => left.localeCompare(right)));
}

function isHeld(node) {
  if (node instanceof Element && node.contains(document.activeElement)) {
    return true;
  }
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) {
    return false;
  }
  for (let index = 0; index < selection.rangeCount; index += 1) {
    if (selection.getRangeAt(index).intersectsNode(node)) {
      return true;
    }
  }
  return false;
}

function setText(node, value) {
  const text = String(value ?? '');
  if (node && node.textContent !== text) {
    node.textContent = text;
  }
}
```

After the view class definition, publish the concrete shared helper before the IIFE closes:

```javascript
WorkflowBoardView.refKey = refKey;
window.WorkflowBoardView = WorkflowBoardView;
```

`inspectBoard` must reject wrong binding identity, duplicate column/card/field keys, invalid arrays/counts, wrong selected-ticket identity, unsupported presentation format, and a changed editable signature before rendering or adopting versions. Treat an absent selected ticket or `selected_ticket.ticket === null` as unavailable, not a request to close the inspector. A changed agent catalog must never replace options under focus; Task 3 handles deferral or requests manual refresh if the selected assignment cannot be represented safely.

Keep a card map spanning all columns, not one map per column. Clone inert templates only for new identities. Patch plain text with `setText`, classes with conditional toggles, and card URLs with `new URL(boardUrl, window.location.origin)` plus the snapshot's query/assignee and ticket ID. Apply server order with `insertBefore` only when needed. Reuse the same structural deferral key for a node's move and removal, so reappearance cancels a previously deferred removal.

The deferral primitive has a complete lifecycle:

```javascript
deferOrApply(key, node, apply) {
  if (isHeld(node)) {
    this.deferred.set(key, { node, apply });
    return;
  }
  this.deferred.delete(key);
  apply();
}

flushDeferred() {
  if (!this.page.isConnected) {
    this.deferred.clear();
    return;
  }
  for (const [key, pending] of this.deferred) {
    if (!isHeld(pending.node)) {
      this.deferred.delete(key);
      pending.apply();
    }
  }
}

clearDeferred() {
  this.deferred.clear();
}
```

For read-only HTML, parse only Task 1's server-rendered strings into an inert template. Reconcile child nodes recursively by `data-view-key`, retaining same-key/same-tag elements and unchanged text nodes. Patch attributes only when different; preserve user-owned `open` state. Defer a protected subtree's changes, moves, removals, or hiding until focus/selection leaves it. Use a single latest callback per structural or content key and clear it when a subsequent compatible snapshot can apply immediately. Do not use `innerHTML` on the page, inspector, panels, forms, dialog, or an ancestor of live inputs.

Refresh or cancel a key's pending callback on every reconciliation, including when the newest desired state equals the currently displayed state. Put the conditional DOM write inside the callback, rather than skipping deferral bookkeeping. This prevents a previously deferred change/removal from firing after the server has reverted it or a card has reappeared.

- [ ] **Step 4: Switch passive refresh to JSON consumption with post-parse ordering checks.**

`currentSnapshotUrl` must always use `this.initial.urls.snapshot`, preserving query/assignee and selected ticket. Expanded pages use the same board response for up-to-date header totals; their view simply has no column host. Retain the existing detail snapshot URL for mutation redirects and direct API consumers. Keep existing navigation methods unchanged.

Use this control-flow shape in `refreshBoard`:

```javascript
async refreshBoard() {
  if (document.hidden) {
    return;
  }
  const pageSeq = this.requestCounters.page;
  const actionSeq = this.requestCounters.action;
  const snapshotUrl = this.currentSnapshotUrl().toString();
  const request = this.beginAbortableRequest('refresh');
  try {
    const headers = { Accept: 'application/json' };
    if (this.etags.board) {
      headers['If-None-Match'] = this.etags.board;
    }
    const response = await fetch(snapshotUrl, { headers, signal: request.controller.signal });
    const payload = response.status === 304 || !response.ok ? null : await response.json();
    if (!this.isLatestRequest('refresh', request.seq)
      || request.controller.signal.aborted || document.hidden
      || this.requestCounters.page !== pageSeq
      || this.requestCounters.action !== actionSeq || this.pendingAction
      || this.currentSnapshotUrl().toString() !== snapshotUrl) {
      return;
    }
    if (response.status === 304) {
      return;
    }
    if (!response.ok) {
      throw new TicketActionError(this.issuePayload('refresh-failed', 'The workflow board could not be refreshed.'));
    }
    if (this.applyBoardSnapshot(payload)) {
      this.etags.board = response.headers.get('etag');
    } else {
      this.etags.board = null;
    }
  } catch (error) {
    if (error?.name !== 'AbortError') {
      this.reportActionError(error);
    }
  } finally {
    if (this.isLatestRequest('refresh', request.seq)) {
      this.scheduleRefresh();
    }
  }
}
```

Task 4 replaces refresh error reporting with a separate status channel and supplies missing/incompatible notices. This step must already reject stale responses after body parsing and retain ETags only after application. `applyBoardSnapshot` must preserve the old selected detail/draft when unavailable, while updating safe board content. For compatible selected data, call `applyDetailSnapshot` with `source: 'poll'`; keep current dirty values and do not rewrite unchanged/focused controls. Task 3 completes baseline and interaction semantics.

- [ ] **Step 5: Run the focused green checks and structural matrix.**

```powershell
npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'polling keeps|visible polling|older selection|hidden pages' --reporter=list
```

Update hidden-page and delayed-poll intercepts to the board snapshot URL on both screens, preserving their assertions rather than weakening them. Add a structural test that clones an actual board response, route-controls one changed snapshot, moves a card to another column, removes another card, and recalculates counts. Assert unchanged DOM identities and exact membership/order. Use real create/update/assignee/run routes for supported mutations; route-controlled snapshots cover provider-side removal/moves without adding a forbidden user transition/delete API. Preserve the existing backend view coverage in `tests/test_workflow_routes.py` and `tests/test_ticket_routes.py`, including definition order, filtered counts, unavailable data, and detail serialization.

- [ ] **Step 6: Review and commit the snapshot consumer.**

Review protected-node deferral, compatibility validation before mutation, read-only fragment sanitation, no forced focus/scroll changes, and script packaging through the existing `static/*` package data. Commit as `fix(workflows): reconcile polling without page swaps`.

## Task 3: Preserve Interaction Versions And In-Flight Drafts

**Files:** Modify the controller/view module and the existing workflow browser spec only.

**Interfaces:**
- `controller.beginAssigneeInteraction() -> void` captures `{ refKey, version }` once per deliberate attempt.
- `controller.finishAssigneeInteraction() -> void` ends that attempt after settlement or uncommitted blur.
- `controller.saveAssignee(value: string, intent: { refKey: string, version: object }) -> Promise<void>` captures intent before queue execution.
- `controller._saveAssignee(value, intent) -> Promise<void>` posts the captured version and reconciles success/conflict without a page fetch.
- `controller.rebaseDraft(detail, submittedValues: Record<string, unknown> = {}) -> void` preserves current values and safe baselines.
- `controller.releaseInteractions() -> void` reapplies the latest compatible model to clean/unfocused controls and flushes view deferrals.

- [ ] **Step 1: Add the conflicting-assignment regression.**

```typescript
test('focused assignment uses its original version and exposes a remote conflict', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Keep my local draft');
  const original = await detailSnapshot(request);
  await page.locator('#ticket-assignee').focus();
  const remote = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/assignee', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: original.ticket.version,
      operation_id: operationId('remote-assignment'),
      assignee: 'researcher',
    }) },
  });
  expect(remote.ok()).toBeTruthy();
  await forcePoll(page);
  await expect(page.locator('#ticket-assignee')).toHaveValue(original.ticket.assignee || '');
  const posted = page.waitForRequest((requestEvent) => requestEvent.method() === 'POST' && requestEvent.url().endsWith('/fixture-review/assignee'));
  const conflicted = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/fixture-review/assignee'));
  await page.locator('#ticket-assignee').selectOption('builder');
  const form = new URLSearchParams((await posted).postData() || '');
  expect(JSON.parse(form.get('payload') || '{}').version).toEqual(original.ticket.version);
  expect((await conflicted).status()).toBe(409);
  expect((await detailSnapshot(request)).ticket.assignee).toBe('researcher');
  await expect(page.locator('#ticket-assignee')).toHaveValue('researcher');
  await expect(page.locator('#workflow-action-errors')).toBeVisible();
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Keep my local draft');
});
```

Then perform another deliberate selection without blurring the select and verify it succeeds with the current version. Add cases for unchanged selected-ticket versions, remote input changes during focus before typing, deferred clean-field release, new dialog text, and edits made while an assignment/input response is held.

- [ ] **Step 2: Run the assignment conflict check red.**

```powershell
npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'focused assignment uses' --project=desktop-light --reporter=list
```

Expected: the current implementation posts a newer polled version or overwrites the focused selection; the required 409 is not observed.

- [ ] **Step 3: Capture selection intent before queueing and protect controls.**

Bind document `focusin`, `focusout`, `pointerdown`, and `keydown` to the existing controller's one-time event registration. Focus/pointer/keyboard activity starts an assignee attempt only if one is not active. A settled attempt clears the capture; the next deliberate activity can capture a fresh version even while focus stays on the select. `change` ends popup interaction but preserves its captured intent until its queued action settles. A programmatic change without focus falls back to capture at `change`, before enqueueing.

```javascript
beginAssigneeInteraction() {
  const current = this.currentTicket();
  if (!this.assigneeInteraction && current?.version) {
    this.assigneeInteraction = {
      refKey: window.WorkflowBoardView.refKey(current.ref),
      version: structuredClone(current.version),
    };
  }
}

finishAssigneeInteraction() {
  this.assigneeInteraction = null;
}

saveAssignee(value, intent) {
  const captured = structuredClone(intent);
  return this.enqueueAction(() => this._saveAssignee(value, captured));
}
```

Initialize `assigneeInteraction` to null in the controller. In `handleChange`, call `beginAssigneeInteraction` before queueing, and pass that captured object to `saveAssignee`. Check captured scope against the current ticket before sending and before adopting a reply. Do not recapture the version inside `_saveAssignee` or retry a stale assignment automatically. Its mutation payload uses the captured version:

```javascript
const detail = await this.postTicketAction(this.urlFor('assignee', current.ref.ticket_id), {
  version: intent.version,
  operation_id: crypto.randomUUID(),
  assignee: value || null,
});
```

On 409, end that attempt, fetch the current detail JSON through `urlFor('detailSnapshot', ticketId)`, and call `applyDetailSnapshot` as action settlement. Show the existing actionable issue, preserve unrelated drafts, and reset the select to the current assignment without replacing it. If reconciliation fails, keep the draft and show Task 4's refresh-required status. Preserve the original action error if the recovery fetch also fails. A successful current selection clears its action error normally.

Make `renderAssignment(source = 'poll', committedControl = null)` protect focused controls unless the update settles that same control's explicit local command. `source === 'action'` alone is not permission to overwrite an unrelated focused select or input. Assignment settlement passes the assignee select as `committedControl`; input-save/run settlement does not. A pending local assignment may disable its own select after the selection event, but keep the attempted value until settlement. Write properties only when different. Apply the same passive-write guard to editable controls and filter options. Leave creation-dialog inputs alone. A safe catalog update can patch option nodes only after interaction release; an unrepresentable assignment/catalog requests manual refresh.

Use the latest cached model on release, not a stale value captured by an old callback. On `focusout` queue a microtask so the new active element is known; on document `selectionchange`, flush protected read-only fragments once selection leaves them. Clear interaction captures and deferrals during intentional navigation.

- [ ] **Step 4: Rebase drafts against server values without losing newer typing.**

Implement `rebaseDraft` around the existing `syncDraftFromInputs`, `inputDraft`, `dirtyFieldsStillMatchServer`, and `ticketDrafts`. Build a server map from title, description, and detail fields. Before replacing `this.ticket`, compare every dirty key to its old baseline. Update clean/unfocused values and baselines. For a focused clean field whose server value differs, retain its displayed value/baseline and do not advance the draft version past that discrepancy. When it later becomes dirty, the remote difference remains a real conflict.

For input-save replies, capture the actual submitted key/value map before posting. Advance a submitted key's baseline only when the returned server value equals the submitted value. Clear dirty only if the current local value still equals that submitted value; otherwise retain typing performed during the request. After those acknowledged-baseline updates, advance the draft version only if all remaining dirty baselines match the reply.

Replace start-of-request draft restoration in assignment handling with reconciliation of the current draft. Apply the same selected-detail path to run/input replies. The queued request must still read its own required version only at the appropriate point: assignment from captured intent, input save from draft base version, run from current authoritative ticket. Keep operation IDs nonempty and unique.

The dirty-key decision is concrete:

```javascript
const acknowledged = Object.prototype.hasOwnProperty.call(submittedValues, key)
  && Object.is(serverValue, submittedValues[key]);
if (acknowledged) {
  this.inputDraft.baseValues[key] = serverValue;
  if (Object.is(this.inputDraft.values[key], submittedValues[key])) {
    this.inputDraft.dirty.delete(key);
  }
}
if (this.inputDraft.dirty.has(key)
  && !Object.is(serverValue, this.inputDraft.baseValues[key])) {
  canAdvanceVersion = false;
}
```

Call `restoreDraft` only for values that actually need rendering and are not passively protected. Do not reassign an unchanged focused value merely to restore it. Action settlement must not mutate a draft belonging to a newer selection.

The edit-save click currently calls `loadPage` after `saveInputs`. Once the canonical JSON reply renders the read description, remove that redundant HTML request and close edit mode directly:

```javascript
await this.saveInputs();
this.setEditing(false, { focus: false });
```

Keep its existing error catch so a failed save leaves editing open. Add `edit save updates read description without page html` and a held input-save reply arriving while the assignee control has focus; neither may replace the page or overwrite the unrelated active select.

- [ ] **Step 5: Run the focused concurrency and draft suite.**

```powershell
npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'focused assignment|assignee selection|dirty inputs|concurrent dirty|remote input|visible polling|in-flight draft|deferred clean|edit save' --reporter=list
```

Expected: real 409 outcomes for remote conflicts, fresh deliberate retries succeed, no automatic retry, and current/local/server values stay distinct where required. Include both board and expanded pages for focused control preservation.

- [ ] **Step 6: Review and commit interaction protection.**

Review intent capture before enqueueing, current draft preservation at reply time, conservative version advancement, option/disabled deferral, and action-scope guards. Commit as `fix(workflows): preserve focused assignment intent`.

## Task 4: Separate Refresh Status And Complete Failure/Race Handling

**Files:** Modify controller/view, both page templates, minimal workflow CSS, and the existing browser spec.

**Interfaces:**
- `controller.setRefreshStatus(kind, message) -> void`, where `kind` is `failed`, `required`, or `unavailable`.
- `controller.clearRefreshStatus() -> void` clears only the refresh status.
- `view.inspectBoard` supplies the incompatibility/unavailability result before any model or DOM application.
- `releaseInteractions` never performs an HTML fetch or automatically reloads an incompatible view.

- [ ] **Step 1: Add failure and late-body regressions.**

Add these exact named tests to the existing spec so the focused command below selects them:

- `refresh failure preserves controls and recovers without clearing an action error`
- `incompatible snapshot requires explicit refresh without partial application`
- `missing selected ticket keeps its inspector and draft`
- `poll body parsed after an action cannot roll back its accepted reply`
- `hidden document rejects a snapshot whose body parsing finishes late`

For a transient failure, route-abort one board snapshot, retain select/page/input handles and a dirty field, then restore the route and force a successful poll. Create a genuine stale-input action error before recovery and verify it survives. For incompatible data, clone a real snapshot, change one editable field kind, and fulfill it; verify no model/version/card changes and no HTML request or automatic reload, including after blur.

For unavailable selection, fulfill a real board response with a null selected ticket (or a ticket-not-found detail), preserving board structure; verify the inspector handle and draft remain. Verify subsequent recovery from a valid response.

Hold body parsing rather than only delaying the HTTP response:

```typescript
await page.evaluate(() => {
  const probeWindow = window as typeof window & {
    releasePollBody?: () => void;
    pollBodyHeld?: boolean;
  };
  const originalFetch = window.fetch.bind(window);
  const held = new Promise<void>((resolve) => {
    probeWindow.releasePollBody = resolve;
  });
  let heldOnce = false;
  window.fetch = async (input, init) => {
    const response = await originalFetch(input, init);
    if (!heldOnce && String(input).includes('/workflows/delivery/snapshot') && response.status === 200) {
      heldOnce = true;
      const originalJson = response.json.bind(response);
      response.json = async () => {
        probeWindow.pollBodyHeld = true;
        await held;
        return originalJson();
      };
    }
    return response;
  };
});
```

Start `refreshBoard` without awaiting it, wait for `pollBodyHeld`, complete an actual assignment or hide the document using the existing visibility helper, then release parsing. Assert stale content/ETags are not adopted. Use condition-based waits, not sleep-based timing. Existing hidden-page and older-navigation tests remain required.

- [ ] **Step 2: Run the new failure checks red.**

```powershell
npx --no-install playwright test tests/ui/workflow_board.spec.ts --grep 'refresh failure|incompatible snapshot|missing selected|poll body parsed|hidden document rejects' --project=desktop-light --reporter=list
```

Expected: missing separate refresh status, absent manual notice, or a false failure path. Task 2's sequencing tests may already pass; retain them as regression coverage rather than changing code to manufacture failure.

- [ ] **Step 3: Add a separate non-blocking status and manual command.**

Place a hidden `#workflow-refresh-status` with `role="status"` near the existing toolbar on both pages. Use a label span and an icon button only for an explicitly requested refresh. Use the existing Lucide `refresh-cw` icon, accessible name/title `Refresh page`, and existing restrained styling. Initial hidden markup must not change screenshots or introduce overflow at 320px.

Use `Refresh required` for incompatible structure, `Unable to refresh` for transient failure, and `Ticket unavailable` for missing selected data. A manual click may call `window.location.reload()`; passive polling, blur, and recovery never may. Use `textContent` for messages. Keep this status independent of `lastActionError` and `#workflow-action-errors`.

Replace Task 2's refresh catch with `setRefreshStatus('failed', 'Unable to refresh')`, without `reportActionError`. Add incompatible/unavailable reporting to the inspection result. A compatible applied snapshot clears its refresh status only, never an action error. A valid, scope-checked 304 confirms the last fully applied snapshot and may clear refresh status; an ignored response never does. Incompatible or partially applied unavailable views must invalidate `etags.board`, forcing the next poll to obtain a full body rather than confirming content that is no longer represented by the complete loaded UI. A recovered valid selected detail clears its availability notice. Do not store an ETag for a partially applied unavailable/incompatible selected view.

Guard all branches with current request generation and visibility checks. Ensure `finally` schedules one next poll only for the latest request and only when visible. After a pending mutation settles, schedule or perform the next passive refresh, without aborting or retrying an action. Do not mutate live controls to announce failure.

- [ ] **Step 4: Run the complete touched browser slice and route checks.**

```powershell
npx --no-install playwright test tests/ui/workflow_board.spec.ts --reporter=list
& .\.venv\Scripts\python.exe -m pytest tests/test_workflow_routes.py tests/test_ticket_routes.py -q
```

Run sequentially. Expected: all default-headless projects and route-based view checks pass, with preserved navigation, JavaScript-disabled forms, artifacts, required assessment rendering, and existing screenshot tolerances. Run `node --check` on both touched JS files and inspect editor diagnostics.

- [ ] **Step 5: Review and commit the failure-handling deliverable.**

Review malformed JSON, compatibility rejection before mutation, missing tickets, hidden-body races, ETag rejection, action error isolation, and the absence of automatic HTML reloads. Commit as `fix(workflows): keep refresh failures non-disruptive`.

## Task 5: Whole-Branch Verification And Pre-Authorized Integration

**Files:** No unrelated application files. Plan checkbox/evidence updates may be a separate documentation commit. Preserve all canonical/runtime data and unrelated user edits.

- [ ] **Step 1: Complete acceptance coverage and inspect the actual native selection interaction.**

Verify same-node polling on both pages, real create/update/assign/run status changes, route-controlled moves/removals, counts/empty states, field and Markdown updates, history/artifact links, focused/dirty/deferred controls, dialog state, caret/tabs/edit mode, frozen assignment conflict and retry, action/navigation/body races, 304, hidden visibility recovery, failures, and manual incompatibility handling. Each specification acceptance criterion must have a passing check or an explicit unresolved blocker.

For the native-popup smoke check, use a controlled fixture server after automated suites finish, not the user's Atreides ticket. Open the agent menu, keep it open across a changed snapshot, and complete the choice with keyboard/pointer input. Automation with `selectOption` is supplemental, not proof of popup preservation. Do not run committed screenshot gates in headed mode or change goldens to accommodate a diagnostic viewport. Stop the owned fixture server before subsequent pytest, integration, or worktree cleanup.

- [ ] **Step 2: Review the whole branch before integration.**

Inspect the diff from `master`, check the approved specification's requirements against the implementation, and follow the selected execution workflow's review procedure. Review the web JSON/SSR boundary, escaping, event listener lifecycle, protected-node identity, scope/version checks, queued actions, safe draft rebasing, deferral cancellation, hidden scheduling, and error isolation. Address only findings within the feature scope; require authorization for unrelated corrections.

- [ ] **Step 3: Run the full worktree gates sequentially and read the receipts.**

```powershell
Set-Location 'C:\Projekty\Flowgency\.worktrees\workflow-ui'
& .\.venv\Scripts\python.exe -m pytest tests/ -q --junitxml=.superpowers/evidence/workflow-live-reconciliation/feature-python.xml
```

Only after Python completes and its result is classified against the exact known-failure exception:

```powershell
$env:PLAYWRIGHT_JUNIT_OUTPUT_FILE = "$PWD\.superpowers\evidence\workflow-live-reconciliation\feature-ui.xml"
npm run test:ui -- --reporter=list,junit
```

Require zero Python errors/failures outside the exact accepted three-test runtime set. The complete browser matrix requires zero failures. Inspect actual receipts and retained screenshots/traces, disclose all remaining known failures without calling the full suite green, and confirm locked dependencies and snapshot/tolerance files have not changed unnecessarily. Record terminal handoffs if tools detach, never poll an active command, and resume its final output/receipt before continuing.

- [ ] **Step 4: Rebase only if required, then fast-forward master.**

Inspect `git status`, `git worktree list`, and both branch tips immediately before integration. If `master` advanced, rebase the feature onto `master`, resolve only understood conflicts, review the resulting diff, and rerun the full sequential worktree gates with the same narrowly recorded known-failure exception before proceeding. Do not squash or create a merge commit.

Preserve primary-worktree uncommitted tracked changes with a named stash before its fast-forward, record the stash ID, and restore it afterward. Preserve ignored canonical/runtime files in place; do not include them in a blanket cleanup or stage them. If untracked user files would collide with integration, preserve those exact files explicitly rather than deleting them.

The integration commands, after these conditions are met, are:

```powershell
git -C 'C:\Projekty\Flowgency' merge --ff-only feature/workflow-live-reconciliation
```

Restore any recorded stash without dropping it until its successful application is verified. Do not discard conflicting user changes. Confirm `master` carries the reviewed feature tip.

- [ ] **Step 5: Verify master and publish both branches.**

Use the primary worktree's installed `.venv\Scripts\python.exe` from its root. If it does not exist, establish that ignored environment before this gate rather than falling back to global-user Python. Run the full master Python suite with a retained receipt; run the default-headless browser suite sequentially for the integrated UI and compare to the worktree result. Before publishing, require no Python failures/errors outside the exact accepted three-test runtime set and zero browser failures; disclose all remaining known failures and actual suite exit rather than claiming green.

```powershell
Set-Location 'C:\Projekty\Flowgency'
& .\.venv\Scripts\python.exe -m pytest tests/ -q --junitxml=.superpowers/evidence/workflow-live-reconciliation/master-python.xml
```

Only after Python completes and its result satisfies the recorded narrow exception, install the exact Node lockfile if necessary and run the integrated browser gate:

```powershell
$env:PLAYWRIGHT_JUNIT_OUTPUT_FILE = "$PWD\.superpowers\evidence\workflow-live-reconciliation\master-ui.xml"
npm run test:ui -- --reporter=list,junit
```

```powershell
git -C 'C:\Projekty\Flowgency' push origin master feature/workflow-live-reconciliation
git -C 'C:\Projekty\Flowgency' ls-remote --heads origin master feature/workflow-live-reconciliation
```

Verify the published tips match the reviewed local tips. A prompt, a partial push, or an unfinished test is not completion. Do not ask whether to integrate: the repository already pre-authorizes this sequence once review and gates pass.

- [ ] **Step 6: Preserve evidence and remove only the owned worktree.**

Archive the owned evidence/receipts and required failure captures under the primary checkout's ignored `.superpowers/evidence/workflow-live-reconciliation` before removing their source. Verify copied receipts/hashes. Inventory the worktree for unknown user or runtime data; preserve it before any removal. Stop owned servers/watchers and ensure no pending terminal uses the worktree.

```powershell
git -C 'C:\Projekty\Flowgency' worktree remove .worktrees/workflow-ui
git -C 'C:\Projekty\Flowgency' worktree prune
```

Verify both the registration and directory are gone; Windows removal may deregister before a filesystem error. If deletion fails, inspect the exact remaining path and preserve unknown files before resolving it. Do not blindly force or generalize deletion. Keep the feature branch. Preserve the primary runtime configuration, logs, team directories, and unrelated user edits.

- [ ] **Step 7: Report the verified outcome.**

State what changed, exact Python/browser gate results, review/publication status, and the existing dashboard URL for trying assignment. Distinguish the original baseline issue from the UI fix and report any actual unresolved requirement. Never claim this feature fixed while only its plan/specification is complete.

## Plan Coverage

| Specification Requirement | Task |
| --- | --- |
| Same-node controls, no passive HTML fetch, board and expanded pages | 2, 3 |
| Keyed cards/columns, counts/order/empty states, all run statuses | 2, 5 |
| Sanitized descriptions, outputs, requirements, history, issues | 1, 2 |
| Focused controls, options/disabled deferral, caret/dialog/tab/edit state | 2, 3, 5 |
| Dirty baselines and typing during in-flight responses | 3 |
| Frozen assignment version, real conflict, deliberate fresh retry | 3 |
| Post-body sequencing, pending actions, rejected ETags | 2, 4 |
| Separate failures, explicit incompatible refresh, missing ticket recovery | 4 |
| Hidden pause/abort and immediate visibility refresh | 2, 4 |
| Navigation, filters, HTML fallback, accessibility and unchanged visuals | 1, 4, 5 |
| Recorded baseline with explicit known-failure exception, task reviews, full gates, integration/publication/cleanup | Baseline Gate, 1-5 |