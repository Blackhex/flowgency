# App-Wide Live Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every open live-data page and shared navigation region current without reloading pages, disrupting interaction or bypassing the setup-completion gate.

**Architecture:** A shared browser coordinator owns conditional reads, visibility, cancellation and request generations. Page-owned snapshot adapters reuse their initial-render builders and Jinja macros; a small keyed reconciler changes only declared read-only regions and protects controller-owned forms, dialogs and streams. Existing workflow reconciliation stays authoritative for workflow controls and registers with the common coordinator instead of acquiring another timer.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, Jinja2, existing Markdown/nh3 sanitization, vanilla JavaScript, morphdom 2.7.8 bundled with the existing esbuild toolchain, pytest and Playwright.

## Global Constraints

- Approved spec: [2026-10-06-app-live-refresh-design.md](../specs/2026-10-06-app-live-refresh-design.md).
- Branch: `feature/app-live-refresh`; active root: `.worktrees/live-app/`.
- Cover every live-data page and shared live region, not just agent cards or the current team Inbox.
- Normal HTTP snapshot refresh runs every two seconds while a document is visible.
- Hidden documents pause it and cancel in-flight passive reads.
- Becoming visible triggers an immediate catch-up request.
- Update data in place without full-page reloads or destructive page replacement.
- Preserve local working state and existing streaming connections.
- Static content without live regions needs no additional periodic request.
- Do not silently advance a form's hidden revision independently of the values and baselines it protects.
- Retain an ETag only for a response accepted by the current view.
- Preserve the approved workflow refresh contract, including assignment intent and stale-draft conflicts.
- No new frontend framework, runtime event bus, authority model or storage format is included.
- No approved visual sketch exists; current layout, copy and committed visual gates remain normative.
- The setup completion handshake is independent and must be integrated before this branch's final gates.
- Review each testable task before dependent work. Stage explicitly and use Conventional Commits.
- Python and browser suites run sequentially against the shared UI runtime.
- Do not alter runtime-local config, logs, team-state directories or the user's running dashboard.

---

## Preparation And Baseline

```powershell
Set-Location C:\Projekty\Flowgency\.worktrees\live-app
git status --short --branch
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
npm install --package-lock=false
npm install --no-save --no-package-lock @playwright/test@1.61.1
$env:PLAYWRIGHT_SKIP_BROWSER_GC = '1'
npx playwright install chromium
.venv\Scripts\python.exe -m pytest tests/ -q
npm run test:ui
```

Reuse a valid existing worktree-local venv. Verify the measured browser pair
before use; Playwright 1.61.1 is the recorded green pair, not a manifest-update
proposal. Record complete baseline results under ignored
`.superpowers/evidence/app-live-refresh-20261006/`. Preserve failed reports.
Stop for a genuine baseline blocker rather than changing screenshots or
tolerances to establish a false baseline.

Do not overlap these suites with another worktree's fixture server on port
8765. Do not stop or reuse the user's port-8500 application. Use the active
worktree's Python explicitly; package imports and wheel gates must not resolve
against another checkout or global user-site dependencies.

## File Structure And Interfaces

| File | Responsibility |
| --- | --- |
| Create `flowgency/web/live.py` | Closed snapshot/policy types, macro rendering, compatibility and conditional response helper |
| Create `tools/live-refresh.js` | Shared coordinator and protected keyed read-only-region adapter; imports morphdom |
| Generate `flowgency/static/live-refresh.js`, its legal output and `flowgency/static/morphdom.LICENSE.txt` | Locally served bundle and retained dependency license, using the existing packaging pattern |
| [base.html](../../../flowgency/templates/base.html) and [setup.html](../../../flowgency/templates/setup.html) | Load/register the coordinator and stable shared status/region shells |
| Existing page route modules and templates | Page policies, reused read-model builders, live-region macros, stable item keys and explicit snapshot URLs |
| [workflow-board.js](../../../flowgency/static/workflow-board.js) | Register existing reconciliation without another passive transport |
| Existing activity/editor controllers | Keep local behavior; expose mutation settlement and initialize/clean up new read-only nodes |
| Create `tests/test_live_refresh.py` and `tests/ui/live_refresh.spec.ts` | Shared protocol/coordinator/reconciliation contract tests |
| Existing page test suites and [tests/ui/server.py](../../../tests/ui/server.py) | Family-specific remote changes and app-wide coverage proof |

The new shared Python module is not a page-registry mega-module. Each owning
route module builds its context and declares a `LivePagePolicy`. The new JS
entry point owns only transport and read-only DOM application, not ticket
actions, permission previews, routine editing or terminal output.

Task 1 defines these transport names and shapes; subsequent tasks use them:

```python
class LiveBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: str
    team: str | None = None
    entity: str | None = None
    tab: str | None = None
    query: dict[str, str] = Field(default_factory=dict)

class LiveRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    html: str

class LiveSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal[1] = 1
    binding: LiveBinding
    structure: str
    revisions: dict[str, str] = Field(default_factory=dict)
    regions: list[LiveRegion]

@dataclass(frozen=True)
class LivePagePolicy:
    template_name: str
    binding: LiveBinding
    structure: str
    region_macros: Mapping[str, str]
    snapshot_url: str
```

Produce these helpers in Task 1:

```python
def render_live_snapshot(templates, context: dict, policy: LivePagePolicy) -> LiveSnapshot: ...
def live_etag(snapshot: LiveSnapshot) -> str: ...
def respond_live_or_html(request, templates, context: dict, policy: LivePagePolicy, *, status_code: int = 200): ...
```

Successful page GET routes accept `?__live=1` and return JSON snapshots only on
that explicit flag. Otherwise they retain HTML. Preserve filters and entity
selectors when adding the flag. A POST-rendered error/confirmation page embeds
an explicit canonical read-only snapshot URL; never poll its POST action URL.
Document bindings do not include mutable config revision as entity identity.
The snapshot's `structure` is an explicit page/form compatibility identifier,
not an mtime, hash of current content, or another control-plane schema.
It includes the applicable editor field IDs/kinds and owning integration when
those can change the loaded form. Region roots keep existing container tags and
layout; add attributes to them rather than introducing new grid/flex nesting.

## Mandatory Route And Tab Inventory

Every row must have a family task and behavior coverage. The new shared suite
records coverage for each row; shell-only or immutable-content classifications
still require a test for the changing shell and protection of the content.

| Route/Tab | Owning Source And Presentation | Snapshot Target / Task |
| --- | --- | --- |
| `/{team}/` | [app.py](../../../flowgency/app.py), [home.html](../../../flowgency/templates/home.html) | Same GET plus `__live=1`; Task 4 |
| `/{team}/agents` | [agents.py](../../../flowgency/web/routes/agents.py), [agents.html](../../../flowgency/templates/agents.html) | Same GET; Task 5 |
| Agent `profile`, `blueprint`, `runtime`, `prompts`, `memory`, `activity`, `logs` | [agent_detail.py](../../../flowgency/web/routes/agent_detail.py), [agent_detail.html](../../../flowgency/templates/agent_detail.html) and its existing tab templates | Same tab GET; Task 5 |
| Agent `permissions` | [agent_permissions.py](../../../flowgency/web/routes/agent_permissions.py), [agent_detail_permissions.html](../../../flowgency/templates/agent_detail_permissions.html) | Same GET; Task 5 |
| Agent `routines` | [agent_routines.py](../../../flowgency/web/routes/agent_routines.py), [agent_detail_routines.html](../../../flowgency/templates/agent_detail_routines.html) | Same GET; Task 5 |
| Agent move confirmation and roster/detail POST errors | [agents.py](../../../flowgency/web/routes/agents.py), [agent_move.html](../../../flowgency/templates/agent_move.html) | Explicit shell-only agent/profile GET; preserve preview revision and move draft; Task 5 |
| `/{team}/jobs`, `/{team}/jobs/{job_id}` | [jobs.py](../../../flowgency/web/routes/jobs.py), [jobs.html](../../../flowgency/templates/jobs.html), [job_detail.html](../../../flowgency/templates/job_detail.html) | Same GET excluding artifact-download/resume operations; Task 6 |
| `/{team}/logs`, `/{team}/logs/view` | [app.py](../../../flowgency/app.py), [logs.html](../../../flowgency/templates/logs.html), [log_view.html](../../../flowgency/templates/log_view.html) | Same GET preserving path/agent/source selectors; Task 6 |
| `/{team}/workspaces`, `/{team}/workspaces/{idx}/file` | [app.py](../../../flowgency/app.py), [workspaces.html](../../../flowgency/templates/workspaces.html), [workspace_detail.html](../../../flowgency/templates/workspace_detail.html) | Same GET preserving selected allowed file; Task 7 |
| `/{team}/workflows/{workflow}` and expanded ticket | [workflows.py](../../../flowgency/web/routes/workflows.py), [tickets.py](../../../flowgency/web/routes/tickets.py) | Existing `/snapshot` transport and reconciler; Task 8 |
| Workflow `new` and `settings` | [workflow_settings.py](../../../flowgency/web/routes/workflow_settings.py), [workflow_settings.html](../../../flowgency/templates/workflow_settings.html) | Same safe GET with metadata/shell regions; preserve creation/editor draft; Task 9 |
| `/admin/workflow-library`, blueprint `new` and detail | [workflow_library.py](../../../flowgency/web/routes/workflow_library.py), [workflow_library.html](../../../flowgency/templates/workflow_library.html), [workflow_blueprint.html](../../../flowgency/templates/workflow_blueprint.html) | Same safe GET; Task 9 |
| `/admin/`, `/admin/integrations`, `/admin/dispatch`, `/admin/teams`, team `new` and `edit` | [app.py](../../../flowgency/app.py), [admin_teams.py](../../../flowgency/web/routes/admin_teams.py) and existing admin templates | Same safe GET, live status/listings and metadata; Task 9 |
| `/admin/agent-library`, blueprint detail, skills list/detail, prompts | [admin_library.py](../../../flowgency/web/routes/admin_library.py) and existing library templates | Same GET, source/catalog metadata and read-only details; Task 9 |
| `/admin/memory-channels`, channel detail | [admin_memory.py](../../../flowgency/web/routes/admin_memory.py), [admin_memory_channels.html](../../../flowgency/templates/admin_memory_channels.html), [admin_memory_channel.html](../../../flowgency/templates/admin_memory_channel.html) | Same GET, metadata/listing/read-only content; Task 9 |
| Git evidence retained-artifact viewer | [git_evidence.py](../../../flowgency/web/routes/git_evidence.py), [git_evidence.html](../../../flowgency/templates/git_evidence.html) | Shell-only update; retained immutable evidence is not rewritten; Task 7 |
| `/setup`, `/setup/session`, `/setup/complete/{team}` | Setup lifecycle controllers and standalone templates | Lifecycle status adapter, no generic terminal/content replacement; Task 10 |
| Shared team/workflow/workspace navigation on all base-derived pages | [team_navigation.py](../../../flowgency/web/team_navigation.py), [base.html](../../../flowgency/templates/base.html) | Included in each page snapshot, with current navigation intent preserved; Task 3 |

Redirect-only routes resolve to their live destination. Download responses,
static assets, and retired HTTP-410 surfaces do not poll themselves. New/error
forms, confirmation views and immutable viewers are not excuses to omit their
live shell or metadata.

### Task 1: Snapshot Protocol And Page-Owned Macro Rendering

**Files:** Create `flowgency/web/live.py` and `tests/test_live_refresh.py`;
modify [test_surface_contracts.py](../../../tests/test_surface_contracts.py)
and [test_setup_assets.py](../../../tests/test_setup_assets.py) for deployment
coverage as later assets are introduced.

**Interfaces:** Produce all Python types/helpers above. `render_live_snapshot`
calls only the policy's explicit macros from the existing Jinja environment;
it never parses a full page to discover regions or invokes a route through an
internal HTTP request.

- [ ] **Step 1: Add failing typed-snapshot, macro and ETag tests.** Use an in-memory Jinja template with a page macro and hostile plain text:

```python
def test_live_snapshot_escapes_text_and_changes_etag():
    templates = Environment(
        loader=DictLoader({"sample.html": "{% macro live_rows() %}<p>{{ name }}</p>{% endmacro %}"}),
        autoescape=True,
    )
    policy = LivePagePolicy(
        template_name="sample.html", binding=LiveBinding(page="sample"),
        structure="sample:1", region_macros={"rows": "live_rows"},
        snapshot_url="/sample?__live=1",
    )
    first = render_live_snapshot(templates, {"name": "<script>bad()</script>"}, policy)
    assert "&lt;script&gt;" in first.regions[0].html
    second = render_live_snapshot(templates, {"name": "Changed"}, policy)
    assert live_etag(first) != live_etag(second)
```

Also reject duplicate region IDs, unsupported format/structure, query binding
mismatch, event-handler attributes, executable URLs and unsafe fragment tags.
Cover ordinary HTML GET, explicit JSON GET, conditional 304 and non-cacheable
failure replies.

- [ ] **Step 2: Run the red protocol slice.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py -q
```

- [ ] **Step 3: Implement deterministic rendering and conditional responses.** Hash canonical JSON of the complete visible presentation with SHA-256; include displayed relative/due time labels in the digest. Return `Cache-Control: private, no-cache` and correct ETag/Vary semantics. A 304 is allowed only after the current snapshot is built and authorized, not from config revision alone.

```python
serialized = json.dumps(
    snapshot.model_dump(mode="json"), sort_keys=True,
    separators=(",", ":"), ensure_ascii=True,
).encode("utf-8")
etag = '"' + hashlib.sha256(serialized).hexdigest() + '"'
```

Use macro output that is initially rendered by the same template. Existing
server `ticket_markdown`/log sanitization remains authoritative. Plain fields
stay autoescaped. Validate fragments with a structured HTML parser, rejecting
`script`, `iframe`, `object`, `embed`, `base`, `link`, `style`, `srcdoc`, automatic
focus attributes, `on*` attributes and executable URL schemes. Do not blanket
sanitize away form data attributes or inject new client-side Markdown parsing.
Convert event handlers in live item markup to existing delegated-controller
patterns in the owning family task; do not allow them into snapshots.

The template context retains existing snapshot/config/path-access boundaries.
Read operations must not initialize directories, claim tickets, run jobs,
recompile agents, write memory, preview mutations or repair missing source.

- [ ] **Step 4: Run protocol, authority and package checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py tests/test_surface_contracts.py tests/test_strict_app_authority.py tests/test_setup_assets.py -q
```

- [ ] **Step 5: Review protocol safety and commit.**

```text
feat(web): define read-only live snapshots
```

### Task 2: Shared Coordinator And Protected Keyed Region Adapter

**Files:** Create `tools/live-refresh.js`, generate the bundle/legal output,
modify [package.json](../../../package.json), and create
`tests/ui/live_refresh.spec.ts`. Reuse [layout.ts](../../../tests/ui/layout.ts).

**Interfaces:** Expose `window.FlowgencyLive` with `register(adapter) -> handle`.
The handle supplies `refresh()`, `invalidate()`, `beginAction() -> finishAction`,
`flushDeferred()` and `dispose()`. Adapters supply:

```javascript
const adapter = {
  key: 'page',
  interval: 2000,
  binding: () => initial.binding,
  url: () => initial.snapshotUrl,
  headers: () => ({ Accept: 'application/json' }),
  capture: () => null,
  isCurrent: captured => captured === null,
  apply: snapshot => regionView.apply(snapshot),
  status: value => regionView.setStatus(value),
};
```

`apply` returns `{ accepted: boolean, deferred: boolean }`; only accepted
snapshots can supply the next conditional ETag. Each handle owns its own
request, action and binding generations. Multiple handles in one document are
allowed for distinct transport/binding responsibilities, never duplicate reads
for the same page/region.
`capture()` and `isCurrent(captured)` carry controller-specific navigation and
action generations through asynchronous body parsing; simple regions use the
null contract shown above. Define `LiveRegionView(root, initial)` in this task
with `apply`, `setStatus`, `allowUpdate`, `allowDiscard`, `flushDeferred` and
`dispose`. Expose a read-only `FlowgencyLive.handles` registry for deterministic
tests; add its TypeScript Window declaration in the shared test file.
Shared status kinds are `healthy`, `stale`, `incompatible` and `unavailable`.

- [ ] **Step 1: Write red browser contract tests on a small injected test document.** Hold a real select/node reference, stage delayed fetch responses, advance the Playwright clock and change a read-only label:

```typescript
const original = await page.locator('[data-live-key="held"] select').elementHandle();
await page.locator('[data-live-key="held"] select').focus();
await page.evaluate(() => window.FlowgencyLive.handles.get('test').refresh());
expect(await original!.evaluate(node => node.isConnected && node === document.activeElement)).toBe(true);
await expect(page.locator('[data-live-key="other"]')).toHaveText('Remote update');
```

Test native selection completion, dirty text/caret, parent/order changes,
removal, details/open-dialog state, scroll positions, malformed response,
incompatible binding, 304, ETag acceptance, overlapping reads, delayed body,
action settlement, hidden abort and immediate visible recovery.

- [ ] **Step 2: Run the red shared browser suite.**

```powershell
npm run test:ui -- tests/ui/live_refresh.spec.ts --project=desktop-light
```

- [ ] **Step 3: Install/build the focused utility and implement the coordinator.**

```powershell
npm install --save-dev --save-exact --package-lock=false morphdom@2.7.8
```

Add `build:live` using esbuild with `--bundle --format=iife --target=es2020
--minify --legal-comments=external --outfile=flowgency/static/live-refresh.js`.
Keep generated assets and package wheel tests consistent with terminal bundling.
The build also copies the installed package license using Node's file API:

```javascript
require('node:fs').copyFileSync(
  'node_modules/morphdom/LICENSE',
  'flowgency/static/morphdom.LICENSE.txt',
);
```

Run that copy from `build:live` after bundling; do not rely on legal-comment
extraction to preserve a package license that may not be embedded in its JS.
The wheel test verifies the bundled source asset and retained license bytes.

The coordinator schedules the next passive read after settlement, checks
document visibility and action/binding generations both before application and
after asynchronous body parsing, and aborts superseded/hidden requests. Use one
AbortController per active read and no intervals that overlap slow requests.
Failures retain the old DOM and retry after the normal cadence. Action errors
are separate from passive refresh status.
An entity-unavailable/404 response uses the `unavailable` status without closing
the selection or exposing another record. An incompatible binding/structure
uses `incompatible` and retains no unapplied ETag. Other transient failures use
`stale`. No status transition replays an action or automatically reloads.

Use explicit `[data-live-region]` roots and namespaced `[data-live-key]` values.
Forms, controls, dialogs, xterm, editor surfaces and `[data-live-owned]` subtrees
are controller-owned, even when nested inside a keyed list item. Never morph a
page shell, body, active tab, selected entity, local theme class or history.

```javascript
morphdom(currentRegion, nextRegion, {
  getNodeKey: node => node.nodeType === Node.ELEMENT_NODE
    ? node.getAttribute('data-live-key') || node.id : undefined,
  onBeforeElUpdated: (current, next) => regionView.allowUpdate(current, next),
  onBeforeNodeDiscarded: node => regionView.allowDiscard(node),
});
```

Define `allowUpdate` and `allowDiscard` on the region view. They preserve
controller-owned nodes and local disclosure/report-display attributes. Hooks
alone are insufficient: morphdom moves matched keyed elements before invoking
the update hook. Preflight parent/key/order changes for held or draft-owning
items. Defer their containing list's structural operation while patching
matched items' safe read-only children individually. Retain only the latest
target list and apply it after release if the current binding and local intent
still permit it. Do not keep an unbounded snapshot queue or pause the whole page.

Reject root tag/key/structure incompatibility before partial application. Reject
unsafe fragments again before DOM insertion. Use template elements for parsed
trusted fragments; never insert arbitrary response HTML or raw Markdown.
Preserve scroll anchors and bottom-follow state only when the user was already
following. No blur/refocus trick is allowed.

- [ ] **Step 4: Build and rerun the contract suite.**

```powershell
npm run build:live
npm run test:ui -- tests/ui/live_refresh.spec.ts
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py tests/test_setup_assets.py -q
```

- [ ] **Step 5: Review movement/interaction and async races, then commit.**

```text
feat(web): coordinate non-disruptive live refresh
```

### Task 3: Shared Navigation And Base-Page Registration

**Files:** Modify [team_navigation.py](../../../flowgency/web/team_navigation.py),
[base.html](../../../flowgency/templates/base.html), shared live helpers and
bundle; test [test_server.py](../../../tests/test_server.py),
the new `tests/test_live_refresh.py`,
[team_sidebar_workflows.spec.ts](../../../tests/ui/team_sidebar_workflows.spec.ts)
and the new shared UI suite.

**Interfaces:** Produce `shared_region_macros(context, policy) -> Mapping[str, str]`
in `flowgency/web/live.py` to attach page-scoped navigation regions. Initial
HTML embeds non-secret `LivePagePolicy` registration data through `tojson` in
`#live-initial`. Pages register only when actual live regions exist.

- [ ] **Step 1: Add a navigation regression from an existing fixture.**

```typescript
await page.goto('/newsletter/agents');
const navigation = await page.locator('#sidebar').elementHandle();
await request.post('/__ui/live/change', { data: { case: 'navigation-membership' } });
await expect(page.locator('#sidebar')).toContainText('Research updated');
expect(await navigation!.evaluate(node => node.isConnected)).toBe(true);
```

Task 3 adds this bounded, named fixture case to `tests/ui/server.py` using
ConfigStore. It cannot accept arbitrary filenames, Python source or team paths.
Cover changed workflow count/unavailability, team label, workspace membership,
mobile-menu focus and theme preservation.

- [ ] **Step 2: Run the red shell checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py tests/test_server.py -q -k "live or sidebar"
npm run test:ui -- tests/ui/team_sidebar_workflows.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 3: Extract navigation macros without changing the shell.** Use `build_team_context`/`build_workflow_nav` with the same request ConfigSnapshot and provider records, not reloaded configuration or rich ticket scans. Preserve unavailable counts as unavailable, never zero. Key links by team/workflow/workspace identity. Preserve active navigation, menu visibility/inert state and theme controls outside generic regions.

```html
<div data-live-region="navigation-workflows">
  {{ live_navigation_workflows() }}
</div>
<div data-live-status role="status" hidden>
  <span data-live-status-label></span>
  <button type="button" title="Refresh page" aria-label="Refresh page" data-live-manual-refresh>
    <i data-lucide="refresh-cw" aria-hidden="true"></i>
  </button>
</div>
```

Use existing restrained notice/button classes. Hidden healthy status must not
introduce a permanent banner or page-description copy. Manual refresh is an
explicit user action; passive failure/incompatibility never invokes it.

- [ ] **Step 4: Run both checks and accessibility/navigation regressions.**

```powershell
npm run build:live
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py tests/test_server.py tests/test_surface_contracts.py -q
npm run test:ui -- tests/ui/team_sidebar_workflows.spec.ts tests/ui/live_refresh.spec.ts tests/ui/keyboard.spec.ts
```

- [ ] **Step 5: Review shell identity and context consistency, then commit.**

```text
feat(web): refresh shared navigation in place
```

### Task 4: Coherent Inbox Live Regions

**Files:** Modify [app.py](../../../flowgency/app.py),
[home.html](../../../flowgency/templates/home.html),
[test_dashboard.py](../../../tests/test_dashboard.py),
[test_agent_health_fleet.py](../../../tests/test_agent_health_fleet.py),
the new `tests/test_live_refresh.py`,
[dashboard.spec.ts](../../../tests/ui/dashboard.spec.ts) and UI fixture cases.

**Interfaces:** Produce `build_inbox_context(request, services, snapshot, team) -> dict`
by extracting the current home builder, not replacing fleet health semantics.
Declare macros for fleet, workflow summaries, work queue, attention and activity.
Owner-only setup information remains authorized and can be a separate region.

- [ ] **Step 1: Add the original stale-card regression with real fixture state changes.**

```typescript
await page.goto('/newsletter/');
const main = await page.locator('main').elementHandle();
await request.post('/__ui/live/change', { data: { case: 'inbox-job-completes' } });
await expect(page.locator('[data-live-key="agent:advisor"]')).not.toContainText('scheduled run is pending');
await expect(page.locator('[data-live-region="fleet"]')).toContainText('healthy');
await expect(page.locator('[data-live-region="attention"]')).not.toContainText('scheduled run is pending');
expect(await main!.evaluate(node => node.isConnected)).toBe(true);
```

Seed an actually pending routine/job and complete it through existing durable
job helpers in the test fixture. Match the precise generated health sentence
and positive healthy status in the committed test; do not accept mere text
removal as proof of completion. Also create/move/remove membership and activity,
advance due/relative labels and verify queue/count coherence.

- [ ] **Step 2: Run red Inbox checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard.py tests/test_agent_health_fleet.py tests/test_live_refresh.py -q -k "live or fleet"
npm run test:ui -- tests/ui/dashboard.spec.ts --project=desktop-light
```

- [ ] **Step 3: Reuse one context and macro set for initial HTML and snapshots.** Pass the same canonical snapshot through configuration-dependent builders; reuse `build_dashboard_fleet`, `build_health_items`, `build_ticket_dashboard` and `queue_snapshot`. Do not change overdue/queued/running health rules to conceal the symptom. Use stable namespaced agent/job/ticket keys, not row indices, display names or timestamps.

```jinja2
{% macro live_fleet() %}
  {% for agent_row in fleet_agents %}
    <div data-live-key="agent:{{ agent_row.name }}">
      {{ fleet_card(agent_row) }}
    </div>
  {% endfor %}
{% endmacro %}
```

Extract the existing card markup into `fleet_card` in this same template and
retain existing styling/links. Render full logical lists, counts and empty
states from the same context. Keep unrelated focused regions active.

- [ ] **Step 4: Run Inbox/domain/snapshot and browser checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dashboard.py tests/test_agent_health_fleet.py tests/test_health.py tests/test_live_refresh.py -q
npm run test:ui -- tests/ui/dashboard.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 5: Review the original symptom and consistency guarantees, then commit.**

```text
feat(inbox): reconcile live fleet and activity
```

### Task 5: Agent Roster, All Detail Tabs And Working Forms

**Files:** Modify [agents.py](../../../flowgency/web/routes/agents.py),
[agent_detail.py](../../../flowgency/web/routes/agent_detail.py),
[agent_permissions.py](../../../flowgency/web/routes/agent_permissions.py),
[agent_routines.py](../../../flowgency/web/routes/agent_routines.py),
[agents.html](../../../flowgency/templates/agents.html),
[agent_detail.html](../../../flowgency/templates/agent_detail.html), each of its
nine existing tab templates, [agent_move.html](../../../flowgency/templates/agent_move.html)
and [agent-activity.js](../../../flowgency/static/agent-activity.js).
Test [test_agent_roster.py](../../../tests/test_agent_roster.py),
[test_agent_detail.py](../../../tests/test_agent_detail.py),
[test_agent_permissions.py](../../../tests/test_agent_permissions.py),
[test_agent_routines.py](../../../tests/test_agent_routines.py),
[agent_configuration.spec.ts](../../../tests/ui/agent_configuration.spec.ts),
[agent_permissions.spec.ts](../../../tests/ui/agent_permissions.spec.ts) and
[agent_routines.spec.ts](../../../tests/ui/agent_routines.spec.ts).

**Interfaces:** Split `_detail_context` into
`build_agent_detail_context(..., snapshot: ConfigSnapshot, tab: str, overrides=None) -> dict`
and the existing response wrapper, preserving errors/overrides and public
routes. Produce `build_roster_context(..., snapshot: ConfigSnapshot) -> dict`.
Use existing permission/routine editor presentation builders, not another
effective-policy calculator. Expose `initActivityReports(root)` and
`disposeActivityReports(root)` for inserted/discarded read-only reports.

- [ ] **Step 1: Parameterize all nine tabs and roster/confirmation/error pages.** Remotely update identity, active job, routines, catalog/source digest, log membership, memory revision and report history while an appropriate local form/dialog is held:

```typescript
const revision = page.locator('input[name="revision"]').first();
const loadedRevision = await revision.inputValue();
const source = page.locator('textarea').first();
await source.fill('Local working draft');
await request.post('/__ui/live/change', { data: { case: 'agent-source-and-status' } });
await expect(source).toHaveValue('Local working draft');
await expect(revision).toHaveValue(loadedRevision);
await expect(page.locator('[data-live-region="agent-status"]')).toContainText('updated');
```

Use the actual tab-specific read-only status expected by its fixture. Attempt a
save after a conflicting config/source update and verify the existing conflict
path, not a silently advanced version. Hold native catalog selectors and a run
dialog; update safe status data without replacing their nodes/options.

- [ ] **Step 2: Run the family red checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_agent_roster.py tests/test_agent_detail.py tests/test_agent_permissions.py tests/test_agent_routines.py tests/test_live_refresh.py -q -k live
npm run test:ui -- tests/ui/agent_configuration.spec.ts tests/ui/agent_permissions.spec.ts tests/ui/agent_routines.spec.ts
```

- [ ] **Step 3: Add macros/policies for each tab and keyed roster entries.** Keep read-only header/status/log/report/source-summary regions distinct from editor-owned forms. Preserve all loaded form revisions and drafts unless the owning controller has a complete supported clean-field rebase; generic refresh never rebases them. Show current external-change metadata separately.

```html
<form data-live-owned="config-editor" method="post">
  <input type="hidden" name="revision" value="{{ config_revision }}">
</form>
<div data-live-region="agent-status">{{ live_agent_status() }}</div>
```

Keep roster run/move/remove controls keyed by stable instance identity. New rows
may initialize fresh forms with their own current revisions; existing rows
must not adopt new hidden revisions. Convert the live row's inline confirmation
handler to delegated `data-confirm` handling, retaining its explicit action.
Structural deletion/movement of a row with a draft or held control is deferred.

Activity initialization runs for newly inserted reports and tears down their
ResizeObservers on discard. Preserve expanded reports, not just their text.
Integrate local AJAX actions with `beginAction`/settlement and preserve native
POST/303 fallback. Move-preview POST views embed a shell-only safe GET URL and
retain their original preview revision, source/target and memory-mode intent.

- [ ] **Step 4: Run complete family checks and shared contracts.**

```powershell
npm run build:live
.venv\Scripts\python.exe -m pytest tests/test_agent_roster.py tests/test_agent_detail.py tests/test_agent_permissions.py tests/test_agent_routines.py tests/test_admin_agent_create.py tests/test_agent_run.py tests/test_live_refresh.py -q
npm run test:ui -- tests/ui/agent_configuration.spec.ts tests/ui/agent_permissions.spec.ts tests/ui/agent_routines.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 5: Review every tab's preservation and live target, then commit.**

```text
feat(agents): refresh live detail without losing drafts
```

### Task 6: Jobs, Log Listings And Growing Output

**Files:** Modify [jobs.py](../../../flowgency/web/routes/jobs.py),
[app.py](../../../flowgency/app.py), [jobs.html](../../../flowgency/templates/jobs.html),
[job_detail.html](../../../flowgency/templates/job_detail.html),
[logs.html](../../../flowgency/templates/logs.html),
[log_entries.html](../../../flowgency/templates/log_entries.html),
[log_view.html](../../../flowgency/templates/log_view.html),
[test_job_routes.py](../../../tests/test_job_routes.py),
[test_logs.py](../../../tests/test_logs.py),
[test_log_preview.py](../../../tests/test_log_preview.py),
[dashboard.spec.ts](../../../tests/ui/dashboard.spec.ts) and
[log_view.spec.ts](../../../tests/ui/log_view.spec.ts).

**Interfaces:** Reuse `_job_rows`, `_job_detail_context`, `collect_logs`,
`with_log_links`, `_log_view_context` and bounded `read_log_preview`. Produce
read-only policy regions for job status/results/diagnostics, queue metadata,
log listings and content. Log path/agent/source remain part of the binding.

- [ ] **Step 1: Add remote lifecycle/output cases.**

```typescript
await page.goto('/newsletter/jobs/job-waiting');
await page.getByText('Diagnostics', { exact: true }).click();
const diagnostics = await page.locator('details').elementHandle();
await request.post('/__ui/live/change', { data: { case: 'job-finishes' } });
await expect(page.locator('[data-live-region="job-status"]')).toContainText('Completed');
expect(await diagnostics!.evaluate(node => node.isConnected && node instanceof HTMLDetailsElement && node.open)).toBe(true);
```

Add new/removed jobs/log entries, failure artifacts, memory publication state,
appended log text, truncation and file disappearance. Hold cancellation/resume
intent and scroll away from a log's bottom. Verify malicious path changes are
rejected before content is read.

- [ ] **Step 2: Run red job/log checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_job_routes.py tests/test_logs.py tests/test_log_preview.py tests/test_live_refresh.py -q -k live
npm run test:ui -- tests/ui/dashboard.spec.ts tests/ui/log_view.spec.ts
```

- [ ] **Step 3: Build snapshots from validated read-only views.** Do not poll download/artifact endpoints as pages or replay a resume/cancel POST. Preserve artifact-link authorization and existing selected-log scope/containment checks on every snapshot. Escape plain output and retain the existing sanitized log preview; never put terminal escape/text content into arbitrary HTML.

```javascript
const followsBottom = scrollElement.scrollHeight - scrollElement.scrollTop
  - scrollElement.clientHeight <= 2;
const previousTop = scrollElement.scrollTop;
regionView.apply(snapshot);
scrollElement.scrollTop = followsBottom
  ? scrollElement.scrollHeight : previousTop;
```

Define the log adapter's `scrollElement` from the existing real scrolling
container, not the document by assumption. Retain explicit follow controls if
present; do not add a new product feature solely for this transport. Preserve
selected text, disclosures and horizontal preview scrolling.

- [ ] **Step 4: Run all focused job/log gates.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_job_routes.py tests/test_logs.py tests/test_log_preview.py tests/test_job_artifacts.py tests/test_live_refresh.py -q
npm run test:ui -- tests/ui/dashboard.spec.ts tests/ui/log_view.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 5: Review output safety and action races, then commit.**

```text
feat(jobs): refresh lifecycle and log views
```

### Task 7: Workspaces And Immutable Evidence Viewers

**Files:** Modify [app.py](../../../flowgency/app.py),
[git_evidence.py](../../../flowgency/web/routes/git_evidence.py),
[workspaces.html](../../../flowgency/templates/workspaces.html),
[workspace_detail.html](../../../flowgency/templates/workspace_detail.html),
[git_evidence.html](../../../flowgency/templates/git_evidence.html),
[test_workspaces.py](../../../tests/test_workspaces.py),
[test_git_evidence.py](../../../tests/test_git_evidence.py) and
[git_evidence.spec.ts](../../../tests/ui/git_evidence.spec.ts).

**Interfaces:** Produce workspace-list/status/source-metadata macros using the
existing workspace registry, summary renderer and allowed-file list. File-page
binding includes the workspace's current configuration fingerprint and selected
allowed file, not just a positional index. Evidence viewers register shell-only
regions and never reread mutable source as retained evidence.

- [ ] **Step 1: Add workspace metadata/draft and evidence-shell tests.**

```typescript
const editor = page.locator('textarea').first();
await editor.fill('Unsaved workspace configuration');
await request.post('/__ui/live/change', { data: { case: 'workspace-file-changes' } });
await expect(editor).toHaveValue('Unsaved workspace configuration');
await expect(page.locator('[data-live-region="workspace-metadata"]')).toContainText('changed');
```

Test external file changes, workspace removal/reorder, unavailable source and
allowlist rejection. Change navigation while a retained Git artifact is open
and prove its immutable content and selection remain byte/DOM stable.

- [ ] **Step 2: Run the red workspace/evidence slice.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_workspaces.py tests/test_git_evidence.py tests/test_live_refresh.py -q -k live
npm run test:ui -- tests/ui/git_evidence.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 3: Add safe policies and metadata regions.** Validate the existing plugin allowlist and source identity before each read; never fetch an arbitrary filename supplied by a generic page adapter. Leave workspace textarea content untouched and show external-change metadata. A changed positional workspace binding reports unavailability/incompatibility; it cannot silently switch the editor to another workspace.

Define `workspace_identity(workspace: dict) -> str` from canonical JSON of the
validated workspace configuration. Embed the initially loaded value as
`__live_identity` only in that file page's snapshot URL. Parse it as a strict
SHA-256 hex string for live requests and compare before reading file content.
Ordinary initial HTML uses the currently resolved identity; it is not an
authorization substitute for the existing plugin allowlist.

```python
current_workspace_fingerprint = workspace_identity(ws)
loaded_workspace_fingerprint = request.query_params.get("__live_identity")
if requested_file not in allowed_paths:
    raise HTTPException(status_code=403, detail="File not in workspace config files")
if current_workspace_fingerprint != loaded_workspace_fingerprint:
    return incompatible_workspace_snapshot()
```

Define `incompatible_workspace_snapshot()` as the page's safe incompatibility
presentation; it contains no other workspace's file content. Read-only file
metadata uses the existing resolved allowed path, and file-save behavior is not
rewritten in this task.

- [ ] **Step 4: Run full focused checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_workspaces.py tests/test_git_evidence.py tests/test_live_refresh.py tests/test_path_validation.py -q
npm run test:ui -- tests/ui/git_evidence.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 5: Review source identity and immutable-content boundaries, then commit.**

```text
feat(workspaces): refresh safe workspace metadata
```

### Task 8: Migrate Existing Workflow Polling Without Rewriting Reconciliation

**Files:** Modify [workflow-board.js](../../../flowgency/static/workflow-board.js),
[workflow-board-view.js](../../../flowgency/static/workflow-board-view.js) only
for an explicit shared hook, [workflow_board.html](../../../flowgency/templates/workflow_board.html),
[ticket_detail.html](../../../flowgency/templates/ticket_detail.html),
[workflows.py](../../../flowgency/web/routes/workflows.py),
[tickets.py](../../../flowgency/web/routes/tickets.py),
[workflow_board.spec.ts](../../../tests/ui/workflow_board.spec.ts) and existing
[test_workflow_routes.py](../../../tests/test_workflow_routes.py)/
[test_ticket_routes.py](../../../tests/test_ticket_routes.py).

**Interfaces:** The workflow controller registers its current snapshot URL and
existing `applyBoardSnapshot`/detail logic with Task 2's coordinator. Its
`pendingAction`, navigation and action generations remain authoritative. Shared
shell regions accompany the accepted workflow snapshot or are a distinct
shell-only adapter; they cannot add another workflow-data poll loop.

- [ ] **Step 1: Extend the existing held-selector, delayed-body and visibility tests.**

```typescript
const assignee = await page.locator('#ticket-assignee').elementHandle();
await page.locator('#ticket-assignee').focus();
await forcePoll(page);
expect(await assignee!.evaluate(node => node.isConnected && node === document.activeElement)).toBe(true);
```

Keep `forcePoll` on the actual registered handle, preserving existing test
intent. Count workflow snapshot requests to prove there is one loop. Verify the
exact original-version assignment POST still conflicts after a remote change.

- [ ] **Step 2: Run the focused workflow red checks.**

```powershell
npm run test:ui -- tests/ui/workflow_board.spec.ts
```

- [ ] **Step 3: Replace timer/visibility ownership, not the view logic.** Remove the controller's duplicate scheduled timer/listener. Delegate scheduling/cancellation to the handle, while preserving its snapshot ETag application and `isCurrentRefreshRequest` checks. Integrate navigation/action start/settlement with invalidation so stale replies cannot enter the view.

```javascript
this.liveHandle = window.FlowgencyLive.register({
  key: 'workflow', interval: 2000,
  binding: () => this.currentRefKey(),
  url: () => this.currentSnapshotUrl().toString(),
  headers: () => ({ Accept: 'application/json' }),
  capture: () => ({ page: this.requestCounters.page, action: this.requestCounters.action }),
  isCurrent: captured => !this.pendingAction
    && captured.page === this.requestCounters.page
    && captured.action === this.requestCounters.action,
  apply: board => ({ accepted: this.applyBoardSnapshot(board).applied, deferred: false }),
  status: value => {
    if (value.kind === 'healthy') this.clearRefreshStatus();
    else this.setRefreshStatus(
      value.kind === 'incompatible' ? 'required'
        : value.kind === 'unavailable' ? 'unavailable' : 'failed',
      value.message,
    );
  },
});
```

Translate the shared status kinds through a defined adapter rather than passing
unknown states into existing UI blindly. Do not let the generic region morph
adapter touch the workflow board, inspector, forms, assignee selector or dialog.
Preserve `WorkflowBoardView`'s accepted presentation format and sanitization,
deferred-control release, action errors, draft baseline and ETag acceptance.
Intentional navigation keeps its existing HTML path.

- [ ] **Step 4: Run complete workflow/shared gates.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_workflow_routes.py tests/test_ticket_routes.py tests/test_live_refresh.py -q
npm run test:ui -- tests/ui/workflow_board.spec.ts tests/ui/live_refresh.spec.ts tests/ui/team_sidebar_workflows.spec.ts
```

- [ ] **Step 5: Review original workflow regressions and duplicate-loop absence, then commit.**

```text
refactor(workflows): share refresh lifecycle
```

### Task 9: Administration, Source Libraries And Memory Channels

**Files:** Modify the admin handlers in [app.py](../../../flowgency/app.py),
[admin_teams.py](../../../flowgency/web/routes/admin_teams.py),
[admin_library.py](../../../flowgency/web/routes/admin_library.py),
[admin_memory.py](../../../flowgency/web/routes/admin_memory.py),
[workflow_library.py](../../../flowgency/web/routes/workflow_library.py),
[workflow_settings.py](../../../flowgency/web/routes/workflow_settings.py) and
their existing inventory-listed templates. Preserve the existing
[workflow-editor.js](../../../flowgency/static/workflow-editor.js) and
[workflow-settings.js](../../../flowgency/static/workflow-settings.js) as editor
owners; only add action-settlement/metadata hooks.

**Interfaces:** Every listed admin GET gets an explicit `LivePagePolicy` and
context builder reused by initial rendering. Lists use stable domain keys;
source/memory editors expose current source/config revisions separately from
loaded editable baselines. Storage-preview/check POST results remain local
action output, never passive snapshot operations.

- [ ] **Step 1: Add parameterized live admin/source cases to the shared UI suite and existing family tests.**

```typescript
const originalRevision = await page.locator('input[name="expected_revision"]').inputValue();
await page.locator('textarea').fill('Local source draft');
await request.post('/__ui/live/change', { data: { case: 'workflow-source-changes' } });
await expect(page.locator('textarea')).toHaveValue('Local source draft');
await expect(page.locator('input[name="expected_revision"]')).toHaveValue(originalRevision);
await expect(page.locator('[data-live-region="source-metadata"]')).toContainText('changed');
```

Cover settings, integrations, dispatch, team list/new/edit, agent blueprint/
skill/prompts, workflow list/new/detail/settings and memory channel list/detail.
Remote create/remove/status/source/memory changes must update read-only regions.
Failed POST-rendered forms retain validation errors and snapshots use the
canonical safe GET, not that POST action.

- [ ] **Step 2: Run the red admin checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_admin_dispatch.py tests/test_team_settings.py tests/test_agent_library_routes.py tests/test_memory_channel_routes.py tests/test_workflow_library_routes.py tests/test_workflow_settings.py tests/test_live_refresh.py -q -k live
npm run test:ui -- tests/ui/workflow_library.spec.ts tests/ui/workflow_settings.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 3: Add page-local macros and safe metadata adapters.** Reuse `_render_library_list`, `_render_blueprint_detail`, `_render_blueprint_skill`, `_render_blueprint_prompts`, `_render_channel_list`, `_render_channel_detail` and workflow editor presentation builders by splitting context construction from the response wrapper. Do not turn the common live module into a loader for arbitrary filesystem paths.

```python
policy = LivePagePolicy(
    template_name="admin_memory_channel.html",
    binding=LiveBinding(page="memory-channel", entity=channel_key),
    structure="memory-channel:1",
    region_macros={"source-metadata": "live_source_metadata"},
    snapshot_url=safe_channel_get_url,
)
return respond_live_or_html(request, templates, context, policy)
```

Build `safe_channel_get_url` from the validated channel identity through current
route quoting conventions. Protect all form revisions, source text, directory
choices, preview versions, creation drafts and selected catalogs. Unknown
external changes produce metadata/conflict notices without automatic source
repair, validation mutation or editor rebasing. Keep theme preference local;
no remote snapshot changes the user's light/dark mode.

- [ ] **Step 4: Run existing admin/editor/security gates.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_admin_dispatch.py tests/test_admin_dispatch_xss.py tests/test_team_settings.py tests/test_agent_library_routes.py tests/test_memory_channel_routes.py tests/test_workflow_library_routes.py tests/test_workflow_settings.py tests/test_live_refresh.py -q
npm run test:ui -- tests/ui/workflow_library.spec.ts tests/ui/workflow_settings.spec.ts tests/ui/agent_configuration.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 5: Review every inventory-listed admin page, then commit.**

```text
feat(admin): refresh live metadata without editing drafts
```

### Task 10: Setup Lifecycle Registration And Stream Continuity

**Files:** Modify [setup.html](../../../flowgency/templates/setup.html),
[setup_complete.html](../../../flowgency/templates/setup_complete.html),
[tools/setup-terminal.js](../../../tools/setup-terminal.js) only if a narrow
shared status hook is required, and [setup.spec.ts](../../../tests/ui/setup.spec.ts).
Consume the integrated setup-completion implementation; do not redefine it.

**Interfaces:** Register `/setup/status` with the shared lifecycle scheduler
using its existing 1500-ms cadence and completion decision. Standalone setup
documents load the same coordinator. The terminal's existing WebSocket,
font/fit handling and process ownership stay outside read-only morphing.

- [ ] **Step 1: Add a continuity and visibility regression.**

```typescript
const terminal = await page.locator('#setup-terminal .xterm').elementHandle();
const sockets: string[] = [];
page.on('websocket', socket => sockets.push(socket.url()));
await request.post('/__ui/setup/ready');
await expect(page).toHaveURL(/\/setup\/session$/);
expect(await terminal!.evaluate(node => node.isConnected)).toBe(true);
```

Attach socket observation before launch in the committed test so the initial
connection is counted. Hidden status reads pause, visibility catches up, and
neither event stops/recreates the CLI or answers its pending scheduler prompt.
Explicit completion/exit or approved fallback remains the only redirect gate.

- [ ] **Step 2: Run focused setup/browser checks.**

```powershell
npm run test:ui -- tests/ui/setup.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 3: Register the existing status operation without a second loop.** Remove its standalone passive timer once the handle owns scheduling. Keep waiting/inspection intent, launch generations, ready-versus-complete distinction and owner-only presentation. Reconcile only status labels; xterm nodes, buffers, input and live sockets are never snapshot regions.

```javascript
const applySetupStatus = payload => {
  if (waitingView && payload.redirect && currentLaunchMatches(payload.completion)) {
    window.location.assign(payload.redirect);
  } else {
    statusMessage.textContent = payload.message || payload.completion?.message || '';
  }
  return { accepted: true, deferred: false };
};
```

Define `currentLaunchMatches` in the setup controller using the integrated
completion gate's launch identity. Preserve normal existing-config setup
behavior when there is no tracked attempt, and never redirect an inspection
view merely because its status is ready.

- [ ] **Step 4: Run integrated setup and asset checks.**

```powershell
npm run build:live
.venv\Scripts\python.exe -m pytest tests/test_setup_sessions.py tests/test_setup_security.py tests/test_setup_flow.py tests/test_server.py tests/test_setup_assets.py tests/test_live_refresh.py -q
npm run test:ui -- tests/ui/setup.spec.ts tests/ui/live_refresh.spec.ts
```

- [ ] **Step 5: Review completion/stream independence, then commit.**

```text
refactor(setup): share status refresh lifecycle
```

### Task 11: Complete Coverage Matrix, Docs And Whole-App Gates

**Files:** Extend the new shared UI suite, existing family suites,
[tests/ui/server.py](../../../tests/ui/server.py),
[test_ui_fixture_server.py](../../../tests/test_ui_fixture_server.py),
[test_setup_assets.py](../../../tests/test_setup_assets.py) and
[kb/deployment.md](../../../kb/deployment.md).

**Interfaces:** The named `/__ui/live/change` fixture command has an allowlisted
case schema and calls existing canonical domain helpers. It is test-server-only
and cannot modify user runtime files. Coverage tables in the shared tests record
every mandatory inventory row and its actual changing live region.

- [ ] **Step 1: Add a failing coverage assertion and multi-page test.**

```typescript
const second = await context.newPage();
await page.goto('/newsletter/');
await second.goto('/newsletter/jobs');
await request.post('/__ui/live/change', { data: { case: 'inbox-job-completes' } });
await expect(page.locator('[data-live-region="fleet"]')).toContainText('healthy');
await expect(second.locator('[data-live-region="job-list"]')).toContainText('Completed');
await second.close();
```

Test actual document visibility transitions through browser lifecycle controls,
not focus assumptions. Add delayed request/body races, removed entities,
form-version conflicts, relative-time-only changes, executable fragment
rejection, owner-only setup leakage checks and observer/request cleanup.

- [ ] **Step 2: Run the focused coverage slice.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py tests/test_ui_fixture_server.py tests/test_setup_assets.py -q
npm run test:ui -- tests/ui/live_refresh.spec.ts
```

- [ ] **Step 3: Complete fixture/reset safety and deployment documentation.** Preserve workflow-library and durable-job roots during resets, close extra pages before afterEach cleanup, and wait for confirmed fixture session teardown. Document the generated local bundle/legal output and the visible-page cadence, without claiming hidden tabs or streaming transports share a polling guarantee.

```python
SUPPORTED_LIVE_CASES = {
    "navigation-membership", "inbox-job-completes", "agent-source-and-status",
    "job-finishes", "workspace-file-changes", "workflow-source-changes",
}
if command.case not in SUPPORTED_LIVE_CASES:
    raise HTTPException(status_code=400, detail="Unsupported live fixture change")
```

Extend the explicit allowlist with each tested family case; keep the schema
closed and input limited to fixture identities. Mutation helpers must use
ConfigStore/domain services where those exist, not unsafe direct production
file writes or a general script runner.

- [ ] **Step 4: Run all required gates sequentially and inspect evidence.**

```powershell
npm run build:live
.venv\Scripts\python.exe -m pytest tests/test_live_refresh.py tests/test_repository_boundaries.py tests/test_surface_contracts.py tests/test_setup_assets.py -q
npm run test:ui -- tests/ui/live_refresh.spec.ts
.venv\Scripts\python.exe -m pytest tests/ -q
npm run test:ui
git diff --check
```

Require the complete desktop/mobile headless projects, normal tolerances, no
unrelated screenshot replacement and no Tailwind CDN regressions. Run a focused
desktop native-select smoke check; Playwright `selectOption` alone does not prove
an open browser-managed popup survived a refresh. Inspect retained evidence for
each mandatory page family and the original pending-to-healthy Inbox symptom.

- [ ] **Step 5: Review complete coverage and commit the final tests/docs.**

```text
test(web): verify app-wide live data coverage
```

## Spec Coverage And Task Review Gates

| Approved Requirement | Deliverable |
| --- | --- |
| All pages, tabs and shared live regions | Inventory plus Tasks 3-10; completeness enforced in Task 11 |
| Two-second visible polling, hidden cancellation and immediate catch-up | Task 2; existing setup lifecycle cadence retained in Task 10 |
| No reload/full-page polling; stable keyed UI | Tasks 1-2 and each page's macro/adapter |
| Draft, native-control, dialog, disclosure and scroll preservation | Tasks 2, 5-9 plus real-node browser tests |
| ETag/304 and async/navigation/action races | Tasks 1-2, 8 and 11 |
| Canonical authority, safe paths and sanitized presentation | Tasks 1, 4-7 and 9 |
| Error separation, last-good display, manual incompatibility recovery | Task 2 and family-specific missing/changed binding tests |
| Existing workflow reconciliation and frozen intent | Task 8 with its original regression suite |
| Terminal continuity and independent setup-completion gate | Task 10 after the setup feature is integrated |
| Accessibility, responsive/JS-disabled behavior, packaged assets and full gates | Tasks 3, 11 and existing complete suites |

Do not start a dependent task until its consumed interfaces and focused tests
have been reviewed. Tasks 4-7 and 9 can be delegated only after Tasks 1-3 are
stable and with explicit ownership of their templates/routes; shared app.py or
fixture-server edits must be serialized by the coordinating worker. Task 8
cannot be validated by replacing its existing suite with generic region tests.

## Whole-Branch Review And Integration

- [ ] Confirm the setup-completion feature is integrated; rebase this branch onto current `master` before Task 10 and repeat affected/shared gates.
- [ ] Review the complete branch and mandatory inventory, including POST-rendered pages, immutable viewers, sources, menus, native controls, source/version conflicts, path containment, owner-only data and generated-asset packaging.
- [ ] Run both complete suites sequentially in the worktree after any final rebase or repair. A successful task slice is not an app-wide completion receipt.
- [ ] Preserve dirty `master` changes in a named stash, including unrelated untracked user files but never ignored runtime config/state. Fast-forward `master` only and restore the stash without discarding conflicts.
- [ ] Run complete Python and browser suites sequentially on fast-forwarded `master`, using its validated venv and measured browser pair.
- [ ] Push `master` and `feature/app-live-refresh` after green gates. Integration/publication is pre-authorized; do not ask a new merge-choice question.
- [ ] Remove only the owned `.worktrees/live-app` worktree with `git worktree remove`, verify deregistration and directory removal, prune, and retain the branch. Preserve user/runtime data and archived evidence.