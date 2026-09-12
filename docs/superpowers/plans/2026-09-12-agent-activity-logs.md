# Agent Activity and Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Agent Detail into a combined Activity timeline with exact-job log links and an agent-only Logs tab matching Execution Logs.

**Architecture:** Stamp missing originating-job provenance in existing ticket events. Build read-only projections over one request configuration snapshot and one team job lookup, share log collection and row rendering outside the application entry module, and retain the existing safe log viewer. Do not change job execution or ticket workflow semantics.

**Tech Stack:** Python, FastAPI, Jinja2, existing filesystem stores, existing Lucide assets, small browser JavaScript, pytest, and Playwright.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-09-12-agent-activity-logs-design.md`, approved on 2026-09-12.
- Worktree: `C:/Projekty/Flowgency/.worktrees/agent-activity-logs`; branch: `feature/agent-activity-logs`. Documentation is rebased onto master `034787c`; planning starts at `10c4fba`.
- The specification and this plan must be in separate documentation-only commits before source edits. Do not amend the approved design commit into implementation.
- Run all installation, test, and build commands from this worktree. Use its own `.venv` with pip and declared `.[test]` dependencies. Do not repoint the global editable installation.
- Establish a clean complete Python baseline before implementation. Use focused red/green checks while editing, review each task before dependent work, then run complete Python and browser gates and a whole-branch review.
- The newest 50 combined records are displayed, not 50 per source. Include queued, waiting for memory, running, complete, failed, and cancelled jobs.
- Preserve the tab order: Profile, Blueprint, Runtime, Permissions, Prompts, Routines, Memory, Activity, Logs.
- Direct Activity links are Output then Error. Use exact persisted job paths and exact team/agent/job ownership. Never infer event provenance from a filename, current assignee, time proximity, or latest job.
- Stamp new event provenance only from validated `AgentTicketContext`. Preserve older events without backfill and existing transition/report data.
- No GET may create storage, publish memory, rewrite a record, change configuration, or launch a job. An unreadable source must not hide other readable sources.
- No prompts, permissions, schedules, launch recovery, ticket transitions, log deletion, streaming, search, filters, pagination, or job-success reinterpretation are in scope.
- Preserve both existing themes, shared header/sidebar, typography, wrapping tabs, path restrictions, and bounded sanitized log preview. Do not introduce a new design system, font dependency, or logging format.
- Show only readable regular `.out`/`.err` files confined to the configured log root. Omit zero-byte error files; an error stream does not mean a failed job.
- Retain exact labels: Activity, Execution Logs, Output, Error, Show more, Show less, No logs yet, No logs available, No recent activity, No logs found., Back to Activity, Back to Logs. Existing team entry points retain Back to logs.
- Use structured URL encoding and validated return context, never an arbitrary return URL. Render stored summaries and names as escaped text.
- Manual edits use apply_patch. The controller writes plans/briefs/reports and delegates application/test edits during subagent-driven execution. Do not run nested implementers or concurrent terminal commands.
- One-shot commands use sync mode without timeout. If the host backgrounds a run, record its UUID and await completion; do not poll, duplicate the run, or infer success from partial output.
- Preserve every failed verification result. A focused retry is not a complete green suite. Do not weaken assertions, skip available runtime probes, or raise screenshot tolerances to obtain a pass.
- Preserve main's staged `.vscode/tasks.json`, configuration, locks, runtime data, and unrelated worktrees. Never run `git clean` for setup or cleanup.

## Normative Assets

Directory: `docs/superpowers/specs/assets/2026-09-12-agent-activity-logs/`.

- Source: `agent-activity-logs.html`, with its archived fonts and licenses.
- Desktop: `activity-desktop.png`, `activity-expanded-desktop.png`, `logs-desktop.png`.
- Mobile: `activity-mobile.png`, `activity-expanded-mobile.png`, `logs-mobile.png`.

All six images govern layout, spacing, ordering, labels, and link/report placement. Fixture identities and values are illustrative, not fixed product data. Compare the rendered application against all six before completion; do not merely generate screenshots without inspecting them.

## File Responsibilities

| Files | Responsibility |
| --- | --- |
| `flowgency/tickets/service.py`, `flowgency/tickets/views.py` | Stamp general agent events and expose minimal originating-job provenance. Existing transition/report constructors already stamp job IDs. |
| New `flowgency/web/logs.py` | Shared confined log collection, exact-agent filtering, URL construction, and exact-job stream links. No import from `flowgency.app` or route modules. |
| New `flowgency/web/job_presentation.py` | Read team job records once with scoped diagnostics; share the existing status, trigger, badge, and routine-title presentation functions. |
| `flowgency/app.py`, `flowgency/web/routes/jobs.py` | Delegate shared helpers without changing existing public call shapes; validate log-view return context and retain safe preview. |
| `flowgency/web/routes/agent_detail.py` | Register Logs, reuse the supplied snapshot, and choose read-only tab projections. Do not grow another general-purpose collector in this route. |
| New `flowgency/web/agent_activity.py` | Merge job and ticket-event rows, resolve trustworthy associations, normalize timestamps, and bound/group the final timeline. |
| `flowgency/templates/logs.html`, new `log_entries.html`, new `agent_detail_logs.html` | One log-row presentation used by team and agent pages. |
| `flowgency/templates/agent_detail_activity.html`, `log_view.html`, new `flowgency/static/agent-activity.js` | Approved timeline, accessible report expansion, and safe viewer return navigation. |
| Existing pytest and Playwright files | Regression coverage, deterministic fixture history, screenshots, accessibility, and navigation checks. No duplicate test framework or extra test module is needed. |

## Execution Setup

- [ ] Inspect branch/status/log before installing or editing; do not rely solely on a progress ledger.
- [ ] Create this worktree's environment with `python -m venv .venv`, then `./.venv/Scripts/python.exe -m pip install -e '.[test]'`. Verify `python -P` through that interpreter imports this worktree, not main.
- [ ] Install browser dependencies with `npm ci`; use the existing Playwright browser installation, installing Chromium through Playwright only if missing. Do not regenerate the lockfile.
- [ ] Create an ignored evidence ledger at `.superpowers/sdd/2026-09-12-agent-activity-logs/progress.md` and run the baseline:

```powershell
./.venv/Scripts/python.exe -m pytest tests/ -q --tb=short --junitxml=.superpowers/sdd/2026-09-12-agent-activity-logs/baseline.xml
```

Capture stdout separately as baseline.txt. A passing baseline must include available actual runtime cases, not just capability/preflight checks. Store Python's user-site layout is unsuitable for the installed-wheel proof using `-S`; the local environment avoids that known mismatch without weakening the proof.

## Task 1: Preserve Originating Jobs in Ticket Events

**Files:** Modify `flowgency/tickets/service.py`, `flowgency/tickets/views.py`; extend `tests/test_ticket_service.py`, `tests/test_ticket_routes.py`, and relevant existing transition/report or broker tests.

**Interfaces:** Consume `TicketService._event(kind, actor, summary, event_id, now)`, validated `AgentTicketContext`, `TicketEvent.data`, and `_summary_view`. Produce `TicketEventView.job_id: str | None = None` and a shared `event_job_id(event: TicketEvent) -> str | None` in `tickets/views.py`. The helper accepts only a nonempty safe string job identifier, returning None for missing/malformed values. It does not look up or authorize a job; Task 3 validates actual ownership.

- [ ] **Step 1: Add a failing real-service provenance regression.** Reuse `workflow_env` and its registered agent context rather than calling `_event` in isolation:

```python
def test_agent_created_event_records_originating_job(workflow_env):
    env = workflow_env
    actor = env.agent("builder", "activity-run")
    created = env.service.create(
        actor, env.workflow_id, "Activity provenance", "Body", {"summary": "ready"},
        env.operation("activity-create", actor_name=actor.agent_name),
    )
    stored = env.provider.read(created.ticket.ref)
    assert stored.events[-1].data["job_id"] == actor.job_id
    assert stored.events[-1].actor == actor.agent_name
```

- [ ] **Step 2: Run the new test alone and retain the expected missing-job-ID failure.**

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_ticket_service.py -q -k agent_created_event_records_originating_job --tb=short
```

- [ ] **Step 3: Stamp general events at the existing trusted constructor.** Preserve existing event identity, summary, and timestamp. User-authored events have no agent job stamp:

```python
data = {"job_id": actor.job_id} if isinstance(actor, AgentTicketContext) else {}
return TicketEvent(
    id=event_id, kind=kind, actor=actor_name, summary=summary, at=now, data=data,
)
```

Add the optional compact-view field and populate it via `event_job_id`. Reuse the canonical safe job-ID validation already provided by JobStore rather than inventing an incompatible format. Keep transition_event/report_event payloads, session IDs, assessments, and state snapshots intact; these constructors already record their originating job. Immediately rerun the same failing test.

- [ ] **Step 4: Extend boundary coverage through the existing service/protocol fixtures.** Assert create, update, start/end work, report, and transition events retain the trusted job ID. Assert a user event and older event with empty data yield None. Parameterize malformed data values with None, an empty string, a list, a dictionary, and `../other-run`. Check the compact view and full audit view retain their prior fields. Submit a forged top-level job_id through the existing broker request fixture and assert invalid-request, with no persisted mutation; do not add job_id to an input model.

```python
def test_compact_history_retains_originating_job(workflow_env):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import build_board_view, event_job_id

    env = workflow_env
    actor = env.agent("builder", "activity-view-run")
    created = env.service.create(
        actor, env.workflow_id, "Compact history", "Body", {"summary": "ready"},
        env.operation("compact-create", actor_name=actor.agent_name),
    )
    board = build_board_view(env.service, env.user, env.workflow_id)
    ticket = next(
        ticket for column in board.columns for ticket in column.tickets
        if ticket.ref == created.ticket.ref
    )
    assert ticket.history[-1].job_id == actor.job_id
    old_event = TicketEvent(kind="reported", actor="builder", summary="old")
    assert event_job_id(old_event) is None
```

Extend the existing report/transition tests in place: compare their persisted assessments and transition_snapshot against the same submitted report and evaluated transition used by those tests. The added general-event stamp must not replace either payload.

- [ ] **Step 5: Run the ticket slice, commit only the task files, and request task review.**

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_ticket_service.py tests/test_ticket_routes.py tests/test_ticket_transitions.py tests/test_ticket_reporting.py tests/test_ticket_broker.py -q --tb=short
git diff --check
git commit -m 'feat(tickets): retain event job provenance'
```

Stage only reviewed task files explicitly before committing. Record red/green commands, counts, base/tip, safety boundaries, and any concern in the task report. Resolve review findings before Task 2.

## Task 2: Share Safe Log Views and Add the Agent Logs Tab

**Files:** Create `flowgency/web/logs.py`, `flowgency/web/job_presentation.py`, `flowgency/templates/log_entries.html`, and `flowgency/templates/agent_detail_logs.html`. Modify `flowgency/app.py`, `flowgency/web/routes/agent_detail.py`, `flowgency/web/routes/jobs.py`, `flowgency/templates/logs.html`, and `flowgency/templates/log_view.html`. Extend `tests/test_logs.py`, `tests/test_agent_detail.py`, and `tests/test_job_routes.py`.

**Interfaces:**

- `load_team_jobs(job_store: JobStore | None, team_id: str) -> tuple[tuple[JobRecord, ...], tuple[str, ...]]` in job_presentation reads `JobStore.paths` with `read_job`, rejects linked paths outside the trusted team job root before reading, checks embedded team/job identity against the file, and returns valid records plus scoped warnings. Do not create the job directory or silently discard all jobs because one file is malformed.
- Move existing `_friendly_status`, `_status_badge_classes`, `_friendly_trigger`, and `_routine_title` to public helpers `friendly_status`, `status_badge_classes`, `friendly_trigger`, and `routine_title` in job_presentation. Preserve private import aliases in jobs.py so existing behavior/callers remain unchanged.
- `collect_logs(group: dict) -> dict[str, list[dict]]` retains its existing entry keys: name, path, suffix, size, timestamp. Keep `flowgency.app.collect_logs` available as an imported alias, and preserve `_is_empty_error_log` callers. The shared helper must not import app.py.
- `collect_agent_logs(logs_root: Path, team_id: str, agent_id: str, records: tuple[JobRecord, ...], configured_agent_names: tuple[str, ...]) -> dict[str, list[dict]]` returns the same grouped entries, with no eight-file limit.
- `log_href(team_id: str, path: str, *, agent_id: str | None = None, source: str | None = None) -> str` uses quote/urlencode. `source` is exactly activity or logs when agent context is supplied.
- `job_log_links(team_id: str, agent_id: str, record: JobRecord, logs_root: Path, *, source: str = "activity") -> tuple[dict[str, str], ...]` produces label/href/icon entries for accessible stdout then stderr. Wrong team/agent, missing files, outside-root files, unsupported suffixes, and empty stderr yield no corresponding link.
- `with_log_links(groups: dict[str, list[dict]], team_id: str, *, agent_id: str | None = None, source: str | None = None) -> dict[str, list[dict]]` returns presentation rows with href, leaving neutral collector callers valid. Both templates include log_entries.html.

- [ ] **Step 1: Add and run the first route regression.** Extend the current stable-tab test with Logs and reuse `_seed_activity_app` for an agent file:

```python
def test_agent_logs_tab_uses_execution_logs(monkeypatch, tmp_path, raw_config):
    client, config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()
    response = client.get("/newsletter-prod/agents/advisor/logs")
    assert response.status_code == 200
    assert 'aria-current="page">Logs' in response.text
    assert "Execution Logs" in response.text
    assert log_file.name in response.text
    assert "1 file" in response.text
    assert config_path.read_bytes() == before
```

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_agent_detail.py -q -k agent_logs_tab_uses_execution_logs --tb=short
```

The expected initial failure is the missing Logs route. Do not start with broad source cleanup.

- [ ] **Step 2: Implement the shared collector and thin Logs route, then rerun that test immediately.** Register GET `/{team}/agents/{agent}/logs`, append Logs after Activity, and pass the existing request snapshot through `_detail_context`. Load team jobs once for exact ownership. Collect only safe files before creating date groups; group newest first, sort by modification time descending and OUT before ERR for ties. Preserve one stream if the other disappears.

For job-owned files, persisted record ownership takes precedence over every filename. Never let fallback matching re-admit a file belonging to another record. For files without a record, inspect the existing writer's naming convention and recognize a complete agent boundary, considering all configured names and known recorded owners; `advisor-extra` must not match advisor. Reject ambiguous names rather than guessing. Preserve the currently supported `advisor-run.out` fixture case. Add a regression for overlapping names before finalizing that matcher.

Keep helpers read-only and tolerate expected file-disappearance/unreadable-file errors. Resolve candidate paths and enforce log-root confinement; do not follow escaping file, date-directory, symlink, or junction paths. A directory with an `.out` suffix is not a log. Skip hidden dates/files and unsupported extensions before counting.

- [ ] **Step 3: Share the existing rows and add encoded links.** Move the existing log group/row markup into log_entries.html with the same time, badge, filename, KB, borders, and hover treatment. Remove filtering from the template because filtering already occurs in the collector. Use a flexible min-width-zero filename column and fixed/shrink-zero time, badge, and size columns. Add title/full accessible name to truncated filenames. Agent content has an h2 Execution Logs and singular/plural file count; team content keeps its current heading and sidebar behavior.

```python
query = {"path": path}
if agent_id is not None and source in {"activity", "logs"}:
    query.update({"agent": agent_id, "source": source})
return f"/{quote(team_id, safe='')}/logs/view?{urlencode(query)}"
```

Use that construction only after validating the context pair. In templates use the prepared `entry.href`; do not concatenate raw paths into an href.

- [ ] **Step 4: Extend the existing viewer without weakening its authorization.** Add optional agent/source query parameters to log_view and _log_view_context. Neither parameter present retains current team behavior. An incomplete pair or source outside {activity, logs} is a 400; an unknown configured agent is a 404. Valid agent context requires that the selected file is actually available to that agent's scoped projection, otherwise 403. Keep `validate_file_access` and `read_log_preview` on every direct request, including requests with malformed return context.

```python
return_label = "Back to Activity" if source == "activity" else "Back to Logs"
return_href = f"/{quote(team, safe='')}/agents/{quote(agent, safe='')}/{source}"
```

For team requests use Back to logs and `/{team}/logs`. No return_to URL parameter is introduced. Keep preview work off the event loop using the current threadpool boundary. Preserve the complete wrapping filename and bounded sanitized content rendering.

- [ ] **Step 5: Add focused safety regressions and rerun the log slice.** Cover more than eight files, empty error-only groups, hidden and non-files, modification-time ties, exact agent overlaps, a persisted wrong-owner path with a misleading name, a removed instance's recorded paths, missing files, and path escapes. Use the repository's existing Windows link/junction helpers for link tests. Round-trip an ampersand/space/non-ASCII filename with request params, including literal backslashes where supported. Assert invalid source/agent context never becomes an external redirect. Read config/job/ticket bytes before and after tab GETs. Preserve existing team collection and safe-preview tests.

```python
response = client.get(
    "/newsletter-prod/logs/view",
    params={"path": str(log_file), "agent": "advisor", "source": "logs"},
)
assert response.status_code == 200
assert "Back to Logs" in response.text
assert "/newsletter-prod/agents/advisor/logs" in response.text
```

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_logs.py tests/test_agent_detail.py tests/test_job_routes.py -q --tb=short
git diff --check
git commit -m 'feat(agents): add scoped execution logs tab'
```

Stage only the task's files, record evidence, and complete task review before Task 3. Update Python tests whose exact old tab count changed, but do not update browser snapshots yet.

## Task 3: Build the Combined Activity Timeline

**Files:** Create `flowgency/web/agent_activity.py` and `flowgency/static/agent-activity.js`. Modify `flowgency/web/routes/agent_detail.py` and `flowgency/templates/agent_detail_activity.html`. Extend `tests/test_agent_detail.py`, reusing `workflow_web_env`, `_seed_app`, and the durable-job factory in `tests/test_job_routes.py`. Reuse suitable shared helpers rather than duplicating a large JobSpec constructor.

**Interfaces:** Consume Task 1's `event_job_id`, Task 2's job loader/presentation functions and `job_log_links`, `resolve_workflow_binding(snapshot, team_id, workflow_id)`, and `services.tickets.storage_factory(binding.storage).list(team_id, workflow_id)`. Do not invoke build_board_view, detail/reservation projections, or repeatedly call services.config_store.load for each source.

Produce `build_agent_activity(snapshot: ConfigSnapshot, team_id: str, agent_id: str, paths: ResolvedTeamPaths, services: FlowgencyServices) -> dict[str, Any]` with activity_groups, activity_count, and activity_warnings. A group has date_label and entries. Each entry has identity, kind, at, time_label, title, href, summary, metadata, status_label, status_classes, icon, log_links, and no_logs_label. identity is prefixed with job or ticket plus namespace/event identity; never use a bare event ID globally.

- [ ] **Step 1: Add a failing route-level mixed-history regression.** Use the existing job-route fixture and factory to write a completed durable job with recorded timestamps/summary, and load Activity through TestClient. Extend the fixture only where its current return values are insufficient.

```python
from tests.test_job_routes import _seed_app as seed_job_app, _write_job_record

def test_activity_includes_completed_job(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = seed_job_app(monkeypatch, tmp_path, raw_config)
    job_path = _write_job_record(team_root, config_path, job_id="activity-complete")
    record = read_job(job_path)
    record.status = "complete"
    record.completed_at = "2026-07-16T13:00:00+00:00"
    record.execution_summary = "Retained completed run summary"
    write_job(job_path, record)
    response = client.get("/newsletter/agents/advisor/activity")
    assert response.status_code == 200
    assert "Retained completed run summary" in response.text
    assert "Complete" in response.text
    assert "1 record" in response.text
```

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_agent_detail.py -q -k activity_includes_completed_job --tb=short
```

Expected red: the existing active-only collector omits the completed row.

- [ ] **Step 2: Implement the read-only projection and rerun that test.** Load team jobs once, check exact selected-agent ownership, then add configured-workflow event rows attributed to that agent. Resolve bindings from the supplied snapshot and read raw records from the provider, retaining event data needed for historical labels. Catch only known storage/validation/read exceptions at individual source boundaries; keep valid job records, logs, and other workflows with a scoped warning. Do not change a provider's fail-closed contract merely to make partial history appear complete.

Normalize recorded datetime/string values with the application's local-time semantics. A nonempty malformed preferred timestamp is unknown, not silently replaced by the current time. Use completion, otherwise start, otherwise creation when the preferred field is absent. For ordering, known timestamps precede unknown timestamps and ties use stable identity:

```python
entries.sort(
    key=lambda entry: (
        entry["at"] is not None,
        entry["at"].timestamp() if entry["at"] is not None else 0,
        entry["identity"],
    ),
    reverse=True,
)
entries = entries[:50]
```

Only then group by local calendar date and compute activity_count. Unknown timestamps form the final unknown-date group and show an unknown time. Aware and naive inputs must never be compared directly. Keep full summary text in the projection.

- [ ] **Step 3: Add exact event-to-log joins and factual row metadata.** Index the already-loaded selected-agent records by exact job_id; a matching event ID alone does not authorize another team's or agent's record. Use persisted stdout/stderr only, never filename fallbacks for Activity. An older event lacking trustworthy job provenance still gets its ticket URL and No logs available. An active job without accessible logs gets No logs yet; terminal jobs get No logs available. The Error stream does not change status.

Reuse friendly_status, status_badge_classes, friendly_trigger, and routine_title. Display only recorded duration_seconds, execution_summary, and short job IDs. Use finite nonnegative duration values; do not estimate progress. Ticket metadata contains the configured workflow name and recorded ticket number. Use recorded transition names and state snapshot names only when historical data establishes them; otherwise show the factual event label. Never map historical state IDs to today's renamed workflow labels.

- [ ] **Step 4: Replace the mixed-column template with the approved timeline.** Use heading Activity, a record count, date headings, and an unframed semantic list with time/icon/content columns. Keep the approved tab/header/sidebar structures. Use existing Lucide symbols with decorative icons hidden from accessibility APIs. Output and Error stay below the report and outside the expanding text region. Remove the obsolete `_ActivityItem`, `_recent_log_rows`, and board-based activity code only after the new route passes.

Each distinct report is escaped text with a stable region ID. A sample disclosure structure is:

```html
<div data-activity-report>
  <div id="{{ entry.identity }}-report" data-report-text>{{ entry.summary }}</div>
  <button type="button" data-report-toggle hidden aria-expanded="false"
          aria-controls="{{ entry.identity }}-report">Show more</button>
</div>
```

The script measures real overflow after fonts are ready and on ResizeObserver notifications. Collapse only overflowing reports to two lines, then expose the native button; short reports have no control. Toggle Show more/Show less and aria-expanded, preserve focus, and leave log links in place. When JavaScript is unavailable, render the full report rather than inaccessible clipped text. Do not insert report HTML with innerHTML. Use stable dimensions/min-width-zero and wrapping for long titles/identities.

- [ ] **Step 5: Extend the focused suite for the complete contract.** Parameterize all six job statuses. Seed mixed jobs/events with timezone offsets, naive times, equal times, absent/malformed values, and more than 50 combined entries. Assert exact ordering/count after combination, deterministic ties, unknown-last display, and no clock-now fallback. Test completed/failed jobs and long escaped summaries; identical event label/summary is not repeated.

Test two events from one job share exact log links, historical unassociated events have none, and changed assignee/latest job cannot redirect provenance. Include a forged/wrong-owner job reference, path escape, missing stream, empty error stream, unreadable workflow, and malformed job file. Use the real durable job serialization; do not monkeypatch the whole activity collector. Assert config, job, ticket, and log contents remain unchanged after GETs. Update the old Recent activity heading assertion to the approved Activity heading.

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_agent_detail.py tests/test_logs.py tests/test_job_routes.py tests/test_ticket_routes.py -q --tb=short
git diff --check
git commit -m 'feat(agents): unify run and ticket activity'
```

Stage exact task files, record evidence, and review the task before browser work.

## Task 4: Verify the Approved UI and Refresh Affected Snapshots

**Files:** Extend `tests/ui/agent_configuration.spec.ts`, `tests/ui/log_view.spec.ts`, `tests/ui/server.py`, and existing accessibility/keyboard cases where required. Update only affected existing snapshot directories. Update `AGENTS.md` and the existing relevant agent/log guide in `kb/agent-identity.md` if their surface descriptions need the new Logs tab. Do not edit the agent setup skill, runtime configuration, or unrelated guides.

**Interfaces:** Reuse `/__ui/reset`, `_job_spec`, `_seed_jobs`, ticket fixture constructors, the existing runtime root, `installBasePageSetup`, `tabTo`, `expectBodyFocus`, `assertNoLayoutIssues`, `assertNoConsoleErrors`, and AxeBuilder. The fixture is not permission to launch real user agents. Preserve existing queue/fleet fixture expectations while adding completed history and directly associated events; prefer an opt-in isolated activity fixture variant if global seed changes would affect unrelated pages.

- [ ] **Step 1: Add browser behavior checks before adjusting presentation.** Include Logs in stable-tab keyboard coverage. Add deterministic Activity cases for a running job without logs, a completed job with both streams, distinct long report text, a historical event without provenance, and a failed job. Set recorded timestamps and file modification times explicitly.

```typescript
await page.goto('/newsletter/agents/advisor/activity');
await expect(page.getByRole('heading', { name: 'Activity', exact: true })).toBeVisible();
const toggle = page.getByRole('button', { name: 'Show more', exact: true }).first();
await toggle.focus();
await page.keyboard.press('Enter');
await expect(toggle).toHaveAttribute('aria-expanded', 'true');
await expect(toggle).toHaveText('Show less');
const output = page.getByRole('link', { name: 'Output', exact: true }).first();
await output.click();
await expect(page.locator('[data-log-content]')).toBeVisible();
await page.getByRole('link', { name: 'Back to Activity', exact: true }).click();
await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/activity$/);
```

Run only the new cases first with the existing Playwright server. Retain a genuine failure for any missing behavior rather than updating assertions to match a defect.

```powershell
npm run test:ui -- tests/ui/agent_configuration.spec.ts tests/ui/log_view.spec.ts --project=desktop-light --grep 'activity|agent logs|all agent detail tabs'
```

- [ ] **Step 2: Exercise both tab round trips and accessibility.** Open Output and Error from associated job/event rows and return to Activity. Open files from agent Logs and return to that agent's Logs. Team-wide entries retain their original return. Check collapsed/expanded keyboard focus, a short report with no toggle, a long unbroken report/title, missing files, empty views, hostile text, encoded filenames, and browser Back. Run layout/no-console/Axe checks in light/dark at desktop, 390px, 320px, and tablet width. Changing viewport must not detach log links or leave a stale disclosure state.

```typescript
await page.setViewportSize({ width: 320, height: 844 });
await assertNoLayoutIssues(page);
const results = await new AxeBuilder({ page })
  .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
expect(results.violations).toEqual([]);
await assertNoConsoleErrors(page);
```

- [ ] **Step 3: Compare against every approved visual asset.** Capture normal/expanded Activity and Logs at the reference desktop/mobile dimensions; inspect rendered files with view_image, not just a successful screenshot command. Confirm nonblank loaded fonts/icons/styles. The archive images are a layout reference, not replacement golden files. Preserve actual filename basenames; normalize only nondeterministic fixture content and checkout prefixes. Do not mask entire controls, loosen tolerances, or approve an unstyled page because an external asset request failed.

- [ ] **Step 4: Refresh only affected snapshots, then run the no-update browser gate.** The added tab can affect screenshots of neighboring Agent Detail views; enumerate those diff files and review each. Other pages may not change incidentally because fixture records were added. No snapshot refresh is a substitute for behavioral assertions.

```powershell
npm run test:ui:update -- tests/ui/agent_configuration.spec.ts tests/ui/agent_permissions.spec.ts tests/ui/agent_routines.spec.ts
npm run test:ui
```

Use narrower named cases for the update when possible, and include another existing snapshot file only if the actual tab change affects it. Keep the full gate retries=0 and current maxDiffPixels. Preserve output/artifacts and summarize actual passed/skipped counts.

- [ ] **Step 5: Run complete Python verification, commit scoped UI coverage/docs, and review the task.**

```powershell
./.venv/Scripts/python.exe -m pytest tests/ -q --tb=short --junitxml=.superpowers/sdd/2026-09-12-agent-activity-logs/full.xml
git diff --check
git commit -m 'test(agents): verify activity and log navigation'
```

Use a separate documentation-only Conventional Commit for any user guide changes; do not combine test and docs scopes in one commit. Capture every full-suite result and independently verify available runtime cases actually ran. If a source fix is needed during UI testing, delegate it to the owning task, run its narrow regression immediately, rereview that change, and rerun the required final gates on the resulting tree.

## Whole-Branch Delivery

- [ ] Reconcile specification coverage against all tasks, inspect the exact branch diff, and complete a whole-branch review. Resolve findings with focused tests before final verification.
- [ ] If master moved, rebase this feature onto master as the repository requires, rerun the complete worktree suite, and review any changed integration context. Do not make a merge commit or squash.
- [ ] Back up/stash main's local changes with index preservation, fast-forward master only, restore with `git stash apply --index`, and compare original staged blob and working bytes. Never fold the task settings into this feature.
- [ ] Run the full Python suite and full no-update browser gate from main with a main-bound environment and isolated fixture server. Confirm source provenance with `python -P`.
- [ ] Publish master and feature/agent-activity-logs to origin atomically after all gates, and read back both exact remote refs. Integration is pre-authorized; do not offer merge/PR/keep choices.
- [ ] Preserve ignored evidence and any non-disposable worktree data outside this checkout with hash verification. Remove only this feature worktree with ordinary `git worktree remove` and prune. Retain the feature branch and unrelated data.
- [ ] Start an authorized local preview only with isolated fixture configuration, on a free port, and provide its URL. Do not restart the user's live dispatcher or let preview startup run their agents.
- [ ] Report actual commits, test counts, visual checks, known warnings, and preservation results. No completion claim until required gates, publication, and cleanup are verified.