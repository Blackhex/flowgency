# Eager Ticket MCP Tool Exposure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose the already-authorized first-party ticket catalog initially, without changing its permissions or strict acceptance checks.

**Architecture:** Add one field to generated Copilot MCP server metadata. Extend existing serializer and supervised-launch tests; keep all launch/policy and live-test surfaces unchanged. The controller owns real acceptance and full baseline gates.

**Tech Stack:** Existing Python JSON projection, pytest, and installed Copilot CLI.

## Global Constraints

- Work from `C:/Projekty/Flowgency/.worktrees/workflow-live-reconciliation`, on the existing feature branch.
- Modify only `flowgency/integrations/ticket_tools.py` and `tests/test_copilot_ticket_tools.py`.
- The only production change is `"deferTools": "never"` in the generated ticket server entry.
- Preserve all type/URL/header/tools/atomic-write and launch assertions; no grant, auth, sandbox, model, prompt, transport, lifecycle, configuration-schema, SDK, or dependency changes.
- Do not change the readonly live task, assertions, markers, or acceptance criteria, and do not add skips/retries or mocks of live success.
- Use `.venv/Scripts/python.exe` from this worktree root. Delegates run offline checks only; paid/full gates are controller-owned.
- First edit changes the expected serializer/supervised behavior; immediately run the focused checks red, then add the one-field implementation and rerun the same checks green.
- Use apply_patch, preserve unrelated work, commit the source/test correction atomically with Conventional Commits, and keep this plan's reports/briefs ignored and uncommitted.
- No repeated live attempt without evidence or another workaround if strict acceptance still fails. Original UI implementation remains gated on complete green baselines.

## Task 1: Make The Fixed Ticket Server Eagerly Visible

**Files:** Modify the two files named in Global Constraints. Reuse existing tests; no new test file or public API.

**Interfaces:** Preserve `write_copilot_ticket_config(launch, path) -> Path`; its JSON server entry adds `deferTools` while retaining all existing fields and atomic publication.

- [ ] **Step 1: Extend the existing expected JSON and supervised payload checks.**

In `test_write_copilot_ticket_config_writes_expected_json`, add the independently
specified field to its literal expected server dictionary:

```python
                "tools": ["*"],
                "deferTools": "never",
```

Retain every other expected field. In `test_copilot_ticket_tools_use_http_config`,
retain its existing checks and assert the actual captured server projection:

```python
    assert captured["config_payload"]["mcpServers"]["flowgency-tickets"]["deferTools"] == "never"
```

- [ ] **Step 2: Run the focused red checks immediately.**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_copilot_ticket_tools.py -q -k 'writes_expected_json or use_http_config'
```

Expected: the generated field is absent. Test setup/import failures do not
count as evidence of this missing behavior.

- [ ] **Step 3: Add the one generated metadata field.**

In `write_copilot_ticket_config`'s server dictionary:

```python
                "tools": ["*"],
                "deferTools": "never",
```

No other production edit is authorized. Do not change canonical config or any
CLI setting/profile, schema, prompt, permission, or version gate.

- [ ] **Step 4: Rerun the same focused checks green, then nearby contracts.**

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_copilot_ticket_tools.py -q -k 'writes_expected_json or use_http_config'
& .\.venv\Scripts\python.exe -m pytest tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q
```

Run sequentially. Retain actual red/green output and exit codes; check touched
file diagnostics. Expected: passing unchanged consent/isolation/grant/header/
lifecycle assertions alongside eager metadata. Do not suppress existing warnings
or modify unrelated tests to make output pristine.

- [ ] **Step 5: Self-review and atomic commit; independent task review.**

Commit only the two touched source/test files as
`fix(copilot): expose ticket tools eagerly`. Write the full report with actual
TDD chronology, commands/results, commit, scope and concerns in this own ignored
SDD workspace. Controller generates recorded BASE..HEAD package and dispatches
a read-only reviewer for spec and quality before real acceptance.

- [ ] **Step 6: Controller runs the original strict live acceptance once.**

```powershell
& .\.venv\Scripts\python.exe -m pytest 'tests/test_ticket_runtime_live.py::test_restricted_agent_reads_ticket_over_http_without_editing[copilot]' -q --junitxml=.superpowers/evidence/workflow-live-reconciliation/readonly-eager-live.xml
```

Expect genuine authenticated read and every existing scoped/read-only/hash/MCP
inventory assertion passing. Inspect the actual exit/JUnit. Keep the original
task, configured model, sandbox, grants, auth, consent, and transport unchanged.
If it fails, stop this hypothesis without repeating or weakening acceptance.

- [ ] **Step 7: Obtain the original UI baseline gate before resuming its tasks.**

Controller runs complete isolated Python, then complete default-headless browser
suites sequentially with retained receipts. A focused pass does not replace a
clean full baseline. Once both pass, record the reviewed prerequisite and resume
Task1 of the approved workflow live-reconciliation plan without another check-in.
Do not publish or clean the feature until the entire UI implementation, reviews,
full gates and repository's pre-authorized integration sequence are complete.