# Durable Workflow Outputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generic workflow outputs persist visibly across transitions and make the authoring and agent contracts require durable results explicitly.

**Architecture:** Retain the existing atomic transition mutation and attempt-only input semantics. Classify output fields in the shared ticket view using current declarations and accepted historical outputs, then reuse that projection in the inspector and expanded page. Correct maintained definitions and shared authoring guidance without changing configured workflows or ticket records automatically.

**Tech Stack:** Python >=3.11, FastAPI, Pydantic >=2.8,<3, Jinja2, YAML, existing vanilla JavaScript, pytest, and Playwright.

## Global Constraints

- "This is a contract for every workflow, not special handling for Research, Delivery, particular agents, or fields named `findings` and `conclusion`."
- "Overview shows the latest saved value of each output. History preserves the outputs of earlier accepted transitions and their original context."
- "There are no ticket backfills, history rewrites, automatic input promotion, startup conversions, historical job replays, agent launches, or permission changes."
- "Do not infer results from reports, logs, semantic memory, state names, field names, or agent roles."
- "Existing definitions and ordinary artifact fields retain their behavior without conversion."
- "Do not require at least one output on every transition."
- "Generated build output is not an editable source."
- "Show `Not submitted` for unset output values."
- "Do not redesign polling or unrelated editing behavior in this change."
- "No mockup has been approved; any later approved visual assets must be archived and referenced under the repository's design-asset rules before implementation planning is finalized."
- Work only in `.worktrees/workflow-transition-outputs` on `feature/workflow-transition-outputs`; preserve main-checkout edits and runtime data.
- Use exact, explicitly staged paths and Conventional Commits. Review every task before starting dependent work.
- Keep specification and plan commits separate from implementation. Do not treat this planning document as permission to run live agents or mutate configured ticket storage.

---

## Scope and Execution Order

Source specification: `docs/superpowers/specs/2026-09-18-workflow-transition-outputs-design.md`, design commits `e122e0a` and `e6e52e9`. The user's request for this implementation plan approves that written design.

This is the independently shippable foundation. Git-change capture, project publication policy, and the diff viewer belong to the companion Git-evidence plan. Execute the foundation first; do not add policy, remote operations, or Git artifacts to these tasks.

Companion: [2026-09-18-workflow-git-evidence.md](2026-09-18-workflow-git-evidence.md).

No approved visual asset paths exist. Preserve the current inspector, editor, and expanded-ticket layout instead of inventing a redesign.

## File Ownership

| Files | Responsibility |
| --- | --- |
| `flowgency/tickets/views.py` | Output classification, canonical current values, historical field metadata fallback |
| `flowgency/templates/_ticket_inspector.html` | Shared Overview and History rendering for board and expanded ticket |
| `flowgency/setup_assets/workflows/research/workflow.yaml` | Research input/output declarations |
| `flowgency/setup_assets/workflows/software-delivery/workflow.yaml` | Delivery input/output declarations |
| `.github/skills/flowgency-setup/SKILL.md` and its packaged source copy | Generic workflow-authoring decisions |
| `.github/skills/flowgency-setup/references/ticket-workflow-steps.md` and its packaged source copy | Reusable ticket-working guidance |
| `flowgency/tickets/mcp_server.py` and `flowgency/jobs/tickets.py` | Agent-facing tool and job contract |
| `kb/data-formats.md` | Documented generic output semantics and worked definition |
| Existing ticket, workflow, setup, and browser tests | Regression coverage; reuse existing fixtures |

Do not split the existing service or change `evaluate_transition` merely to move code. The current persistence contract already implements the desired transaction boundary.

## Execution Prerequisite

- [ ] Confirm `git status --short --branch` and `git worktree list`; do not create a second worktree or reset existing changes.
- [ ] Establish an isolated Python environment in this worktree, install `.[test]`, and verify imports resolve here. Run `python -m pytest tests/ -q` from this root and retain the full report before implementation. Installed-runtime probes may use only their isolated fixture resources, not live user jobs.
- [ ] Install the existing browser dependencies with `npm.cmd install` and use the committed Playwright configuration. Establish the complete browser baseline before changing UI behavior. Do not update snapshots to hide a baseline failure.
- [ ] Stop and report any failing baseline; do not incorporate unrelated repairs into this feature.

```powershell
python -m venv .venv
$env:Path = (Join-Path $PWD '.venv/Scripts') + [IO.Path]::PathSeparator + $env:Path
& .venv/Scripts/python.exe -m pip install -e ".[test]"
& .venv/Scripts/python.exe -P -c "import flowgency; print(flowgency.__file__)"
& .venv/Scripts/python.exe -m pytest tests/ -q
npm.cmd install
node ./node_modules/@playwright/test/cli.js test
```

Use this worktree's interpreter for all Python commands below. The generic `python` spelling assumes the worktree environment is active; using its absolute interpreter path is equivalent. The browser server must also import this worktree, not the main checkout's editable install.

### Task 1: Preserve Output Classification Across States

**Files:**
- Modify: `flowgency/tickets/views.py` (`TicketFieldValueView`, `_field_rows`).
- Modify: `flowgency/templates/_ticket_inspector.html` (output classification and Overview loops).
- Test: `tests/test_ticket_routes.py` (reuse `workflow_web_env`).
- Test: `tests/test_ticket_transitions.py` (existing persistence and attempt-only coverage).

**Interfaces:**
- Consumes: `TicketView.record.field_values`, `.events`, `.field_provenance`, and optional `.definition`; existing `FieldDefinition` model.
- Produces: `TicketFieldValueView.is_output: bool = False` in HTML context and serialized detail snapshots.
- Produces: `output_field_definitions(view: TicketView) -> dict[str, FieldDefinition | None]`; keys classify outputs, values provide validated historical fallback metadata. Current definitions remain authoritative for current labels/types.

- [ ] **Step 1: Add the terminal-state regression in the existing route tests.**

```python
def test_terminal_ticket_keeps_saved_output_visible(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(values={"verdict": True, "summary": "Initial"})
    actor = env.agent("builder", "output-run")
    env.service.start_work(
        actor, ticket.version, env.operation("start", actor_name="builder")
    )
    env.service.transition(
        actor,
        env.read(ticket.ref).version,
        env.transition_request(outputs={"summary": "Retained result"}),
        env.operation("finish", actor_name="builder"),
    )
    path = f"{env.base_path}/tickets/{ticket.ref.ticket_id}"
    payload = env.client.get(f"{path}/snapshot").json()
    summary = next(field for field in payload["fields"] if field["id"] == "summary")
    assert payload["ticket"]["state_id"] == "done"
    assert summary["value"] == "Retained result"
    assert summary["is_output"] is True
    page = env.client.get(path)
    assert page.status_code == 200
    assert "<h3>Outputs</h3>" in page.text
    assert 'data-ticket-input="summary"' not in page.text
    assert "Retained result" in page.text
```

- [ ] **Step 2: Run only this regression and observe the missing projection flag.**

Run: `python -m pytest tests/test_ticket_routes.py::test_terminal_ticket_keeps_saved_output_visible -q`.
Expected before implementation: failure on `is_output`, not a fixture/import error.

- [ ] **Step 3: Add defensive, generic output metadata projection.**

Import `ValidationError` from Pydantic and `FieldDefinition` from `flowgency.workflows.models`. Add `is_output: bool = False` to `TicketFieldValueView`. Implement the helper with structural checks rather than trusting arbitrary event data:

```python
def output_field_definitions(view: TicketView) -> dict[str, FieldDefinition | None]:
    output_fields: dict[str, FieldDefinition | None] = {}
    for event in view.record.events:
        if event.kind != "transitioned" or not isinstance(event.data, dict):
            continue
        values = event.data.get("effective_outputs")
        if not isinstance(values, dict):
            continue
        snapshot = event.data.get("transition_snapshot")
        definitions = snapshot.get("field_defs") if isinstance(snapshot, dict) else None
        for field_id in values:
            if not isinstance(field_id, str) or not field_id:
                continue
            output_fields[field_id] = None
            raw = definitions.get(field_id) if isinstance(definitions, dict) else None
            if not isinstance(raw, dict):
                continue
            try:
                definition = FieldDefinition.model_validate(raw)
            except ValidationError:
                continue
            if definition.id == field_id:
                output_fields[field_id] = definition
    if view.definition is not None:
        for transition in view.definition.transitions:
            for use in transition.outputs:
                output_fields[use.field_id] = view.definition.field(use.field_id)
    return output_fields
```

In `_field_rows`, build `output_fields = output_field_definitions(view)`. Keep current field order, append remaining `record.field_values` keys, then any remaining output IDs. For each ID, choose current field definition first, then validated fallback, otherwise the ID and `type=None`. Set `value` only from `record.field_values.get(field_id)`, provenance only from `record.field_provenance`, and `is_output=field_id in output_fields`. Never read historical values into current values.

Replace the template's current-state `output_ns` calculation with:

```jinja2
{% set output_fields = detail.fields | selectattr('is_output') | list %}
```

Change the Inputs condition to `field.type != "artifact" and not field.is_output`; render Outputs when `output_fields` is nonempty and iterate that list. Keep `transitions` for Requirements only. Preserve typed `render_value`, field ordering, and current History event values. Update exact detail-snapshot expectations to include `is_output` without weakening existing provenance assertions.

Guard History's `effective_outputs` iteration with Jinja's `is mapping` test. A malformed non-mapping payload must not call `.items()` or crash the entire inspector, even when the History panel is hidden. Preserve the event summary and do not invent values from an invalid payload.

- [ ] **Step 4: Rerun the terminal regression, then cover historical fallback and falsey values.**

Run the same focused command first. Add tests in `tests/test_ticket_routes.py` that use the same fixture and `save_blueprint` pattern already in `test_detail_snapshot_exposes_current_definition_fields_and_retained_audit_snapshots`:

```python
def test_output_projection_ignores_report_payloads(workflow_web_env):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import output_field_definitions

    env = workflow_web_env
    ticket = env.create(values={"verdict": True})
    view = env.read(ticket.ref)
    report = TicketEvent(
        kind="reported", actor="builder", summary="Not an accepted transition",
        data={"effective_outputs": {"invented-result": "do not project"}},
    )
    copied = view.model_copy(update={
        "record": view.record.model_copy(update={"events": view.record.events + (report,)})
    })
    assert "invented-result" not in output_field_definitions(copied)
```

Also exercise a removed output declaration after acceptance, a valid renamed current field, malformed snapshot metadata, an accepted output reused by a later transition as input, two accepted values for the same field, and `False`, `0`, `None`, and artifact references. Assert current values against storage and each old value against its own History event. For a missing current definition, retain a saved field and validated historical label/type without synthesizing any value.

Use this table-driven projection regression, keeping the existing HTTP test above as the real-provider proof:

```python
@pytest.mark.parametrize(
    ("field_type", "current_value"),
    [("boolean", False), ("number", 0), ("text", None), ("text", "Newest")],
)
def test_retired_output_uses_saved_value_and_snapshot_label(
    workflow_web_env, field_type, current_value
):
    from flowgency.tickets.models import TicketEvent
    from flowgency.tickets.views import _field_rows

    env = workflow_web_env
    ticket = env.create()
    view = env.read(ticket.ref)
    definition = {"id": "custom-result", "label": "Recorded result", "type": field_type}
    accepted = TicketEvent(
        kind="transitioned", actor="builder", summary="Accepted",
        data={
            "effective_outputs": {"custom-result": "Historical payload only"},
            "transition_snapshot": {"field_defs": {"custom-result": definition}},
        },
    )
    record = view.record.model_copy(update={
        "field_values": {"custom-result": current_value}, "events": (accepted,)
    })
    rows = _field_rows(view.model_copy(update={"record": record, "definition": None}))
    assert len(rows) == 1
    assert rows[0].is_output is True
    assert rows[0].label == "Recorded result"
    assert rows[0].type == field_type
    assert rows[0].value == current_value
    assert rows[0].provenance is None
```

Parameterize malformed `transition_snapshot` and `field_defs` with `None`, strings, lists, and a field definition with a mismatching ID. Retain output classification from the accepted output key, use the ID/type fallback, and never crash or replace its stored value. Build the HTTP repeat-transition case by adding a `done -> review` transition with no outputs to the fixture definition, accepting `complete`, reopening, and accepting `complete` with a new summary. Assert both `effective_outputs` entries against distinct events, not only the final field.

Also parameterize `effective_outputs` as `None`, a string, and a list; neither the helper nor server-rendered History may classify their contents as output fields or raise an exception. For a malformed latest metadata snapshot, use the safe ID/type fallback rather than silently retaining an older label as if it came from the latest transition.

- [ ] **Step 5: Run the touched Python slice and commit only the tested files.**

Run: `python -m pytest tests/test_ticket_routes.py tests/test_ticket_transitions.py -q`.
Expected: all pass, including `test_attempt_only_inputs_do_not_update_current_fields_or_provenance`.

```powershell
git add flowgency/tickets/views.py flowgency/templates/_ticket_inspector.html tests/test_ticket_routes.py tests/test_ticket_transitions.py
git commit -m "fix(tickets): retain outputs across workflow states"
```

- [ ] **Step 6: Review this task before dependent changes.** Confirm no record mutation on GET, no historical input promotion, and no current-state dependency in output classification. Repair any finding with a failing test and rerun the same focused slice before proceeding.

### Task 2: Make Durable Results Explicit in Definitions and Agent Guidance

**Files:**
- Modify: `flowgency/setup_assets/workflows/research/workflow.yaml`.
- Modify: `flowgency/setup_assets/workflows/software-delivery/workflow.yaml`.
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`.
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/references/ticket-workflow-steps.md`.
- Modify: `.github/skills/flowgency-setup/SKILL.md`.
- Modify: `.github/skills/flowgency-setup/references/ticket-workflow-steps.md`.
- Modify: `flowgency/tickets/mcp_server.py` (`ticket_transition`, `ticket_report`, `ticket_update` descriptions).
- Modify: `flowgency/jobs/tickets.py` (`_ticket_task_input`).
- Modify: `kb/data-formats.md` (worked definition and transition contract).
- Test: `tests/test_workflow_setup.py`, `tests/test_setup_assets.py`, `tests/test_ticket_mcp.py`, `tests/test_ticket_jobs.py`.

**Interfaces:**
- Consumes: `WorkflowLibrary(workflow_example_root()).inspect(blueprint_id).definition` and the existing `inputs`/`outputs` `FieldUse` lists.
- Produces: required output declarations for Research Findings/Conclusion and Delivery Notes/Review notes; no new field IDs or schema.
- Produces: consistent published tool descriptions and job/setup guidance explaining temporary inputs, durable outputs, and informational reports.

- [ ] **Step 1: Add a failing shipped-definition contract test.** Add `import pytest` to `tests/test_workflow_setup.py` and use the public example-library API:

```python
@pytest.mark.parametrize(
        ("blueprint_id", "transition_id", "expected_outputs"),
        [
                ("research", "begin-synthesis", {"findings": True}),
                ("research", "close", {"conclusion": True}),
                ("software-delivery", "submit-review", {"notes": True}),
                ("software-delivery", "approve", {"review-notes": True}),
                ("software-delivery", "return-to-progress", {"review-notes": True}),
        ],
)
def test_shipped_result_transitions_require_durable_outputs(
        blueprint_id, transition_id, expected_outputs
):
        from flowgency.setup_assets import workflow_example_root

        definition = WorkflowLibrary(workflow_example_root()).inspect(blueprint_id).definition
        transition = definition.transition(transition_id)
        assert {use.field_id: use.required for use in transition.outputs} == expected_outputs
        assert not (set(expected_outputs) & {use.field_id for use in transition.inputs})
```

- [ ] **Step 2: Run the new test, then edit only the declared result roles.**

Run: `python -m pytest tests/test_workflow_setup.py -k require_durable_outputs -q`.
Expected before the YAML edit: five failures because every shipped output list is empty.

Move `findings`, `conclusion`, `notes`, and `review-notes` to the indicated output lists. Set every one required. Keep `approved` as required input to `approve`, preserving its `equals true` precondition. Keep state graphs, IDs, labels, and criteria unchanged. Example final Research transition:

```yaml
- id: begin-synthesis
    name: Begin synthesis
    from_state: exploring
    to_state: synthesizing
    inputs: []
    outputs:
        - field_id: findings
            required: true
    preconditions: []
    criteria:
        - id: sufficient-evidence
            description: Enough evidence gathered to support synthesis
```

Rerun the exact test immediately. Add assertions that `start`, `start-exploration`, and `reopen` still have no outputs and that Delivery's approval gate remains unchanged.

- [ ] **Step 3: Add output validation tests against these definitions, without rewriting the engine.**

```python
def test_research_close_requires_output_even_when_input_is_supplied():
        from flowgency.setup_assets import workflow_example_root
        from flowgency.workflows.models import ContractError
        from flowgency.workflows.rules import evaluate_transition

        definition = WorkflowLibrary(workflow_example_root()).inspect("research").definition
        with pytest.raises(ContractError) as failure:
                evaluate_transition(
                        definition, "close", "synthesizing", {},
                        {"conclusion": "Attempt context only"}, {}, (),
                )
        assert failure.value.field_id == "conclusion"
        accepted = evaluate_transition(
                definition, "close", "synthesizing", {}, {},
                {"conclusion": "Durable result"}, (),
        )
        assert dict(accepted.effective_outputs) == {"conclusion": "Durable result"}
```

Keep the existing real-provider tests for missing output, invalid type, stale revisions, receipts, and attempt-only inputs. Do not edit the evaluator if those tests already pass.

- [ ] **Step 4: Write a failing packaged-guidance assertion, then replace the misleading wording.**

```python
def test_packaged_ticket_guidance_distinguishes_durable_outputs():
        reference = (
                copilot_discovery_root()
                / ".github/skills/flowgency-setup/references/ticket-workflow-steps.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(reference.split())
        assert "required inputs and optional outputs" not in normalized
        assert "required and optional outputs" in normalized
        assert "Attempt-only inputs do not update saved ticket fields." in normalized
        assert "Reports and logs do not populate output fields." in normalized
```

Run: `python -m pytest tests/test_workflow_setup.py -k packaged_ticket_guidance -q`.

Make both source copies use these semantics:

```text
Inspect the current transition's inputs, required and optional outputs,
preconditions, and criteria before starting work. Submit durable results in
the transition's outputs mapping. Attempt-only inputs do not update saved
ticket fields. Reports and logs do not populate output fields. If required
results are unavailable, report the blocker and leave the state unchanged.
```

The generic setup authoring checklist must ask what each transition consumes, what it produces, which produced values are required, and whether no results are intentional. Replace the instruction to write a generic `notes` field on failure with `ticket_report`; arbitrary workflows need not have that field. Preserve all consent and authority wording. Update both the package-owned source and `.github` discovery copy, not `build/lib`.

Rerun the same guidance test immediately. Add a source/package byte-parity assertion for the two touched Markdown files to `tests/test_setup_assets.py`; retain wheel packaging tests.

- [ ] **Step 5: Expose the same contract in runtime-facing descriptions and the knowledge base.**

Add docstrings used by MCP schema generation:

```python
"""Advance a ticket with its declared inputs, durable outputs, and assessments.

Required outputs must be supplied in outputs. Inputs are attempt-only context;
reports and logs do not save outputs. Accepted outputs and state commit together.
"""
```

Use that docstring on `ticket_transition`. Describe `ticket_report` as informational with no field or state mutation and `ticket_update` as an explicit field edit, not a substitute for required transition output submission. Insert the same three-sentence transition contract into `_ticket_task_input` before the serialized ticket/definition blocks. Keep all current snapshot refresh instructions.

Extend the existing MCP tool-list test to assert these descriptions are actually returned in the schema, and the job-input test to assert the guidance precedes `## Current ticket`. In `kb/data-formats.md`, move the sample review notes to a required output, keep `approved` as an input precondition, and document optional output omission, canonical field persistence, and History retention.

- [ ] **Step 6: Run the complete affected slice, commit, and review.**

Run: `python -m pytest tests/test_workflow_setup.py tests/test_setup_assets.py tests/test_ticket_mcp.py tests/test_ticket_jobs.py tests/test_ticket_transitions.py tests/test_workflow_rules.py -q`.

```powershell
git add flowgency/setup_assets/workflows/research/workflow.yaml flowgency/setup_assets/workflows/software-delivery/workflow.yaml flowgency/setup_assets/copilot/.github/skills/flowgency-setup .github/skills/flowgency-setup flowgency/tickets/mcp_server.py flowgency/jobs/tickets.py kb/data-formats.md tests/test_workflow_setup.py tests/test_setup_assets.py tests/test_ticket_mcp.py tests/test_ticket_jobs.py
git commit -m "fix(workflows): require durable transition results"
```

Before commit, inspect those staged directories and exclude unrelated files. The review must confirm no live configured library is silently updated, no required-output rule depends on names, and start/reopen transitions remain valid without outputs.

### Task 3: Verify the Generic Authoring and Display Flow in the Browser

**Files:**
- Test: `tests/ui/workflow_board.spec.ts`.
- Test: `tests/ui/workflow_library.spec.ts`.
- Modify only if a focused test demonstrates a defect: `flowgency/static/workflow-editor.js` or `flowgency/templates/_ticket_inspector.html`.

**Interfaces:**
- Consumes: Task 1's `fields[].is_output` and existing editor field-use `required` flags.
- Consumes: existing `detailSnapshot`, `operationId`, `openTransitions`, `saveEditor`, `readEditorPayload`, and layout helpers in their respective test files.
- Produces: browser regression gates for all four configured viewport/theme projects; no new production endpoints or fixture modes.

- [ ] **Step 1: Add a required/optional output round-trip browser test.**

```typescript
test('required output choice survives save and reload', async ({ page }) => {
    await page.goto('/admin/workflow-library/blueprints/delivery');
    await openTransitions(page);
    await page.getByLabel('Add output', { exact: true }).click();
    await page.getByLabel('New field label').fill('Independent result');
    await page.getByLabel('New field type').selectOption('text');
    await page.getByRole('button', { name: 'Create and add', exact: true }).click();
    await expect(page.getByLabel('Output required 3', { exact: true })).toBeChecked();
    await page.getByLabel('Output required 3', { exact: true }).uncheck();
    await saveEditor(page);
    await page.reload();
    await openTransitions(page);
    await expect(page.getByLabel('Output required 3', { exact: true })).not.toBeChecked();
    await page.getByLabel('Output required 3', { exact: true }).check();
    await saveEditor(page);
    await page.reload();
    const payload = await readEditorPayload(page);
    const transition = payload.draft.transitions.find(
        (row: { name: string }) => row.name === 'Complete review',
    );
    expect(transition.outputs[2].required).toBe(true);
    expect(transition.inputs).toHaveLength(1);
    await assertNoConsoleErrors(page);
});
```

Run: `node ./node_modules/@playwright/test/cli.js test tests/ui/workflow_library.spec.ts --grep "required output choice"`.
Expected: pass if the existing editor correctly preserves flags; do not introduce a production edit solely to make a new test fail. A failure here requires a small local repair and the same command before proceeding.

- [ ] **Step 2: Add terminal-ticket output rendering and snapshot parity.** The HTTP mutation below touches only the isolated UI fixture's ordinary field-edit route, not the user's tickets and not an agent transition endpoint.

```typescript
test('terminal output remains read-only in inspector and expanded view', async ({ page, request }) => {
    const ticketId = 'fixture-done-1';
    const current = await detailSnapshot(request, ticketId);
    const updated = await request.post(`/newsletter/workflows/delivery/tickets/${ticketId}/update`, {
        headers: { Accept: 'application/json' },
        form: { payload: JSON.stringify({
            version: current.ticket.version,
            operation_id: operationId('terminal-result'),
            patch: { field_values: { 'review-verdict': 'Verified durable result' } },
        }) },
    });
    expect(updated.ok()).toBeTruthy();
    for (const url of [
        `/newsletter/workflows/delivery?ticket=${ticketId}`,
        `/newsletter/workflows/delivery/tickets/${ticketId}`,
    ]) {
        await page.goto(url);
        const overview = page.locator('[data-ticket-panel="overview"]');
        await expect(overview.getByRole('heading', { name: 'Outputs', exact: true })).toBeVisible();
        await expect(overview.getByText('Verified durable result', { exact: true })).toBeVisible();
        await expect(overview.locator('[data-ticket-input="review-verdict"]')).toHaveCount(0);
        await expect(overview.getByText('Not submitted', { exact: true })).toBeVisible();
        await assertNoLayoutIssues(page);
        await assertNoConsoleErrors(page);
    }
});
```

Add `is_output: boolean` to the local TypeScript snapshot field shape and assert the saved `review-verdict` field has that flag. Task 1 supplies the actual transition/provider proof; this browser test proves the UI is independent of outgoing transitions. Extend the case with a 320px viewport and a no-JavaScript browser context using the same server-rendered URL and escaped text checks.

- [ ] **Step 3: Run focused browser tests, then the two full browser files.**

```powershell
node ./node_modules/@playwright/test/cli.js test tests/ui/workflow_board.spec.ts --grep "terminal output"
node ./node_modules/@playwright/test/cli.js test tests/ui/workflow_board.spec.ts tests/ui/workflow_library.spec.ts
```

Expected: all four project variants pass. Use existing keyboard, clipping, and console checks. Review any snapshot change against the unchanged visual structure; only output classification and explicit values should change. Do not weaken screenshot tolerances or hide text to obtain a pass.

- [ ] **Step 4: Commit test coverage and review the foundation as a unit.**

```powershell
git add tests/ui/workflow_board.spec.ts tests/ui/workflow_library.spec.ts
git commit -m "test(workflows): cover durable output authoring"
```

If a focused production repair was necessary, commit it separately with its regression test and an appropriate `fix` subject. Do not fold unrelated UI fixes into this coverage commit.

## Foundation Verification and Handoff

- [ ] Run `python -m pytest tests/ -q` and `node ./node_modules/@playwright/test/cli.js test` from the worktree; retain complete outputs and exact failure/skip counts.
- [ ] Perform a whole-foundation review against specification acceptance points 1-9. Confirm History preservation, malformed metadata behavior, snapshot parity, artifact links, and no live-data mutation.
- [ ] Continue to the companion Git-evidence plan only after this foundation is reviewed and green. If delivering the foundation alone, follow the integration sequence below; otherwise integrate only after both plans are complete.

## Integration Sequence

The repository authorizes integration after implementation, review, and green gates. This section is not an instruction to integrate planning-only commits now.

1. Record and preserve all main-checkout staged, unstaged, and untracked user work. Never fold it into the feature.
2. If `master` advanced, rebase this feature onto it as required by repository policy, resolve only this feature's conflicts, and rerun complete gates in the worktree.
3. Stash main's uncommitted changes if needed, fast-forward `master` only, and restore the stash with its staged state preserved. Never use `git reset --hard`, `git clean`, or a forced worktree removal.
4. Run both complete suites on the fast-forwarded main checkout. Verify tested imports and fixture roots belong to that checkout.
5. Push `master` and `feature/workflow-transition-outputs` to `origin` only after green results. Verify both remote tips.
6. Archive needed reports and inspect runtime/untracked worktree contents before ordinary `git worktree remove .worktrees/workflow-transition-outputs` and `git worktree prune`. Keep the branch. Preserve user/runtime files rather than deleting them to force removal.

## Coverage Map

| Specification requirement | Task |
| --- | --- |
| Generic input/output authoring and required flags | 2, 3 |
| Atomic persistence, revision and replay behavior | 1, 2 plus retained service tests |
| Attempt-only inputs and reports remain nonpersistent | 1, 2 |
| Latest canonical output values across states and definition changes | 1, 3 |
| Earlier accepted values and metadata in History | 1 |
| Falsey values, artifacts, escaping, and malformed metadata | 1, 3 |
| Shipped definitions and packaged agent guidance | 2 |
| Git artifacts, publication policy, and viewer | Companion Git-evidence plan |