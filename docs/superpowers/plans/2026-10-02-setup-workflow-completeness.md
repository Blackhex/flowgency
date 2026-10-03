# Setup Workflow Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make selected workflow definition creation explicit and prevent setup or CLI validation from accepting unresolved workflow references.

**Architecture:** The setup skill owns approved source creation through existing workflow-library APIs. A shared read-only validator checks the configured references for setup completion and explicit CLI validation. Normal configuration loading and service startup remain tolerant.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, PyYAML, pytest, Jinja2, and the existing four-project Playwright harness.

## Global Constraints

- Approved spec: `docs/superpowers/specs/2026-10-02-setup-workflow-completeness-design.md`, documentation commit `74e7be9`; written-spec review was approved in conversation.
- Worktree: `C:/Projekty/Flowgency/.worktrees/setup-workflow-completeness`.
- Branch: `feature/setup-workflow-completeness`; base: `6cefb50b006dae3de70791c27d159fe57578611d`.
- Run all commands and tests from this worktree root, using `.venv/Scripts/python.exe`.
- Workflow creation remains owned by the setup skill. No new public template installation command or tool is introduced.
- Setup completion and explicit validation must reject missing or invalid referenced workflow definitions.
- Ordinary configuration loading and dashboard startup remain tolerant. A broken workflow must not take unrelated teams or workflows offline.
- Existing runtime data is untouched. This work does not restore the Atreides workflow, change its configuration, modify tickets, or alter the scheduler.
- No completion sidecar, persisted marker, last-good definition, startup conversion, or secondary authority is introduced.
- An invalid unused blueprint cannot prevent setup completion. A team with no workflows is valid and requires no workflow library or definition installation.
- Creation requires existing workflow and path approvals, source validation, safe paths, conflict handling, and the single revision-checked atomic config write.
- Validation and service startup perform no creation or repair.
- Preserve existing setup access controls, session ownership, Stop semantics, and completed-session inspection behavior.
- No approved sketch or visual asset exists for this change; retain the existing setup layout and compare browser behavior with the current page.
- Reuse existing tests and helpers; create only the focused shared-validator module.
- Run full Python and default-headless browser gates sequentially, never concurrently.
- Review each task before starting a dependent task. Review the complete branch before the repository-required integration, verification, push, and worktree cleanup.

## Preparation And Baseline

- [x] Create the ignored named worktree and branch from current `master`.
- [x] Create a local virtual environment and install `.[test]`.
- [x] Install existing Node dependencies with `npm install --no-package-lock --no-audit --no-fund`.
- [x] Run the unchanged full Python baseline: 3,293 passed, 19 skipped, one existing Starlette deprecation warning.
- [x] Retain baseline JUnit at `.superpowers/baseline.junit.xml`.
- [x] Commit and obtain written-spec approval before implementation planning.

The baseline command was:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q --junitxml=.superpowers/baseline.junit.xml
```

## File Responsibilities

| File | Responsibility |
| --- | --- |
| `flowgency/workflows/validation.py` | New pure orchestration entry point for read-only referenced-definition inspection and structured issues. |
| `flowgency/cli.py` | Include workflow issues in the existing validate command and error envelope. |
| `flowgency/web/setup_flow.py` | Require valid configured workflow references before setup becomes ready. |
| `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md` | Make approval-bound workflow materialization and validation explicit. |
| `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/references/ticket-workflow-steps.md` | Clarify that ticket-agent guidance is not blueprint source; identify the packaged-example API. |
| `kb/setup-skill.md` | Document materialization, validation, no-overwrite behavior, and startup compatibility. |
| `tests/test_workflow_setup.py` | Validator, source creation/reuse, guidance, and existing packaged-example coverage. |
| `tests/test_cli_contract.py` | Missing/invalid definition failures in text and JSON, preserving existing CLI contracts. |
| `tests/test_setup_flow.py` | Incomplete-to-ready setup transitions and no-workflow compatibility. |
| `tests/test_setup_skill_e2e.py` | Execute the documented recipe against real temporary storage and validate the resulting config. |
| `tests/test_setup_assets.py` | Installed-wheel proof for the validator and corrected guidance. |
| `tests/ui/server.py` | Extend only the existing private setup-ready fixture operation for a staged missing-definition case. |
| `tests/ui/setup.spec.ts` | Browser regression for incomplete polling, repair, redirect, and session continuity. |

The existing setup routes and template already consume `SetupStatus.message`
and poll again on `incomplete`. Change them only if a focused regression proves
additional wiring is required. Do not add a production fixture endpoint or a
new UI layout.

---

### Task 1: Shared Referenced-Workflow Validator

**Files:**
- Create: `flowgency/workflows/validation.py`
- Test: `tests/test_workflow_setup.py`

**Interfaces:**
- Consumes: `ConfigSnapshot`, `ValidationIssue`, `WorkflowLibrary.inspect(blueprint_id)`, and `ContractError`.
- Produces: `validate_workflow_references(snapshot: ConfigSnapshot) -> tuple[ValidationIssue, ...]`.
- Issue scope: `teams.<team-id>.workflows.<workflow-id>`; field: `blueprint`.
- Issue codes: preserve `ContractError.code`; use `missing-workflow-library`, `workflow-identity-mismatch`, `invalid-workflow-definition`, and `unreadable-workflow-definition` for the additional cases.
- Inspect each distinct referenced blueprint once per invocation; report each affected instance. No cache survives a call.

- [ ] **Step 1: Add the first failing missing-source regression.**

Reuse the existing `workflow_env` fixture. It has two teams referencing the same
`delivery` definition, so it also exposes issue attribution across instances.
Keep the new import inside the first test while observing the red phase.

```python
def test_workflow_reference_validation_rejects_missing_source(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references

    snapshot = workflow_env.store.load()
    source = workflow_env.library.source_path("delivery")
    source.unlink()
    before_config = snapshot.path.read_bytes()

    issues = validate_workflow_references(snapshot)

    assert {issue.code for issue in issues} == {"missing-blueprint"}
    assert {issue.scope for issue in issues} == {
        "teams.newsletter.workflows.board-a",
        "teams.support.workflows.board-a",
    }
    assert all(issue.field == "blueprint" for issue in issues)
    assert snapshot.path.read_bytes() == before_config
    assert not source.exists()
```

- [ ] **Step 2: Run that exact regression and record the failure.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_setup.py::test_workflow_reference_validation_rejects_missing_source -q
```

Expected: fails because the new module or function is absent, not because the
existing fixture cannot initialize.

- [ ] **Step 3: Implement the focused read-only validator.**

Use this structure; it deliberately never includes raw exception text in an
issue and never calls service initialization, directory creation, or publication.

```python
from __future__ import annotations

from pathlib import Path

import yaml

from flowgency.configuration import ConfigSnapshot, ValidationIssue
from flowgency.workflows.library import WorkflowLibrary
from flowgency.workflows.models import ContractError


def validate_workflow_references(
    snapshot: ConfigSnapshot,
) -> tuple[ValidationIssue, ...]:
    root = snapshot.config.flowgency.workflow_library
    library = WorkflowLibrary(Path(root)) if root is not None else None
    failures: dict[str, tuple[str, str] | None] = {}
    issues: list[ValidationIssue] = []

    for team_id, team in sorted(snapshot.config.teams.items()):
        for workflow_id, workflow in sorted(team.workflows.items()):
            blueprint_id = workflow.blueprint
            if blueprint_id not in failures:
                if library is None:
                    failure = (
                        "missing-workflow-library",
                        "the workflow library is not configured",
                    )
                else:
                    try:
                        definition = library.inspect(blueprint_id).definition
                        failure = (
                            (
                                "workflow-identity-mismatch",
                                "the definition identity does not match its reference",
                            )
                            if definition.id != blueprint_id
                            else None
                        )
                    except ContractError as error:
                        failure = (error.code, "the definition is missing or unsafe")
                    except (yaml.YAMLError, ValueError):
                        failure = (
                            "invalid-workflow-definition",
                            "the definition is invalid",
                        )
                    except OSError:
                        failure = (
                            "unreadable-workflow-definition",
                            "the definition cannot be read",
                        )
                failures[blueprint_id] = failure

            failure = failures[blueprint_id]
            if failure is None:
                continue
            code, reason = failure
            issues.append(
                ValidationIssue(
                    code=code,
                    scope=f"teams.{team_id}.workflows.{workflow_id}",
                    field="blueprint",
                    message=(
                        f"Workflow '{workflow.name}' ({team_id}/{workflow_id}) "
                        f"cannot use blueprint '{blueprint_id}': {reason}."
                    ),
                    corrective_hint=(
                        "Configure the approved workflow library and create or "
                        "correct this blueprint definition before completing setup."
                    ),
                )
            )
    return tuple(issues)
```

Improve the safe `ContractError` descriptions with a fixed mapping for
`missing-blueprint`, `unsafe-blueprint`, `corrupt-blueprint`, and
`source-too-large`; the description must distinguish those corrections without
echoing `error.message`. Do not catch arbitrary programmer exceptions as success.

- [ ] **Step 4: Rerun the same regression immediately.**

Expected: passes and leaves both the config and missing source unchanged.

- [ ] **Step 5: Add the schema and identity failure cases, then run them.**

```python
@pytest.mark.parametrize(
    ("source_text", "expected_code"),
    [
        ("states: [", "invalid-workflow-definition"),
        ("schema_version: 1\nid: delivery\n", "invalid-workflow-definition"),
    ],
)
def test_workflow_reference_validation_rejects_invalid_source(
    workflow_env, source_text, expected_code
):
    from flowgency.workflows.validation import validate_workflow_references

    source = workflow_env.library.source_path("delivery")
    source.write_text(source_text, encoding="utf-8")
    issues = validate_workflow_references(workflow_env.store.load())
    assert {issue.code for issue in issues} == {expected_code}


def test_workflow_reference_validation_rejects_identity_mismatch(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references
    from tests._ticket_helpers import delivery_definition

    definition = delivery_definition()
    definition["id"] = "another-delivery"
    workflow_env.library.source_path("delivery").write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )
    issues = validate_workflow_references(workflow_env.store.load())
    assert {issue.code for issue in issues} == {"workflow-identity-mismatch"}
```

- [ ] **Step 6: Cover inspection reuse, unused source, and no-workflow config.**

```python
def test_workflow_reference_validation_inspects_shared_blueprint_once(
    workflow_env, monkeypatch
):
    from flowgency.workflows.validation import validate_workflow_references

    inspected = []
    original = WorkflowLibrary.inspect

    def inspect(library, blueprint_id):
        inspected.append(blueprint_id)
        return original(library, blueprint_id)

    monkeypatch.setattr(WorkflowLibrary, "inspect", inspect)
    assert validate_workflow_references(workflow_env.store.load()) == ()
    assert inspected == ["delivery"]


def test_workflow_reference_validation_ignores_unused_invalid_source(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references

    unused = workflow_env.library.root / "unused" / "workflow.yaml"
    unused.parent.mkdir()
    unused.write_text("states: [", encoding="utf-8")
    assert validate_workflow_references(workflow_env.store.load()) == ()


def test_workflow_reference_validation_accepts_no_workflows(
    tmp_path, raw_config
):
    from flowgency.configuration import ConfigStore
    from flowgency.workflows.validation import validate_workflow_references

    store = ConfigStore(tmp_path / "config.yaml")
    store.create(raw_config)
    assert validate_workflow_references(store.load()) == ()
```

- [ ] **Step 7: Add bounded-source, read-failure, and unsafe-path tests.**

Use the same real fixture for each case. Oversize writes are bounded by the
existing `MAX_BLUEPRINT_SOURCE_BYTES`; read denial is a targeted fake and must
not include private exception text in a public issue.

```python
def test_workflow_reference_validation_rejects_oversized_source(workflow_env):
    from flowgency.workflows.models import MAX_BLUEPRINT_SOURCE_BYTES
    from flowgency.workflows.validation import validate_workflow_references

    workflow_env.library.source_path("delivery").write_bytes(
        b"x" * (MAX_BLUEPRINT_SOURCE_BYTES + 1)
    )
    issues = validate_workflow_references(workflow_env.store.load())
    assert {issue.code for issue in issues} == {"source-too-large"}


def test_workflow_reference_validation_sanitizes_read_failure(
    workflow_env, monkeypatch
):
    from flowgency.workflows.validation import validate_workflow_references

    source = workflow_env.library.source_path("delivery")
    original = Path.read_bytes

    def read_bytes(path):
        if path == source:
            raise PermissionError("private-path-and-secret-value")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    issues = validate_workflow_references(workflow_env.store.load())
    assert {issue.code for issue in issues} == {"unreadable-workflow-definition"}
    assert all("private-path-and-secret-value" not in issue.message for issue in issues)


def test_workflow_reference_validation_rejects_reparse_source(
    workflow_env, monkeypatch
):
    import flowgency.workflows.library as library_module
    from flowgency.workflows.validation import validate_workflow_references

    source = workflow_env.library.source_path("delivery")
    original = library_module.is_symlink_or_reparse
    monkeypatch.setattr(
        library_module,
        "is_symlink_or_reparse",
        lambda path: path == source or original(path),
    )
    issues = validate_workflow_references(workflow_env.store.load())
    assert {issue.code for issue in issues} == {"unsafe-blueprint"}
```

For the missing-root case, create a real parsed config containing workflows and
no `flowgency.workflow_library`; assert `missing-workflow-library` for every
affected instance. If existing config validation rejects that shape earlier,
retain its stricter error and test the helper with the corresponding immutable
snapshot copy, rather than weakening config validation.

- [ ] **Step 8: Run the full touched test file and review the task.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_setup.py -q
git diff --check
```

Expected: all existing packaged-example tests and new validation tests pass.
Check diagnostics for the new module. Commit only its source and tests:

```powershell
git add -- flowgency/workflows/validation.py tests/test_workflow_setup.py
git commit -m "fix(workflows): validate configured references"
```

Complete the task review before its consumers are implemented.

### Task 2: CLI And Setup Completion Consumers

**Files:**
- Modify: `flowgency/cli.py`, `flowgency/web/setup_flow.py`
- Test: `tests/test_cli_contract.py`, `tests/test_setup_flow.py`, `tests/test_workflow_setup.py`
- Modify/test fixture: `tests/ui/server.py`
- Test: `tests/ui/setup.spec.ts`

**Interfaces:**
- Consumes: `validate_workflow_references(snapshot: ConfigSnapshot) -> tuple[ValidationIssue, ...]` from Task 1.
- Produces: existing CLI validation envelope and exit code 3 for referenced-definition failures; existing `SetupStatus(state="incomplete", message=...)` until references validate.
- No change to `ConfigStore.load`, `parse_config`, or `build_services` startup policy.

- [ ] **Step 1: Add failing CLI text and JSON regressions.**

```python
@pytest.mark.parametrize("as_json", [False, True])
def test_validate_rejects_missing_workflow_source(cli_config, cli_runner, as_json):
    snapshot = ConfigStore(cli_config).load()
    source = Path(snapshot.config.flowgency.workflow_library) / "delivery" / "workflow.yaml"
    source.unlink()
    arguments = ["validate"] + (["--json"] if as_json else [])

    result = cli_runner(*arguments, config=cli_config)

    assert result.exit_code == 3
    if as_json:
        payload = json.loads(result.stderr)
        assert payload["code"] == "validation-failed"
        assert any(issue["code"] == "missing-blueprint" for issue in payload["issues"])
    else:
        assert "delivery" in result.stderr
        assert "cannot use blueprint" in result.stderr
        assert "Hint:" in result.stderr
    assert not source.exists()
```

- [ ] **Step 2: Run the exact CLI regressions and confirm false-success failures.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli_contract.py -k "validate_rejects_missing_workflow_source" -q
```

Expected: before wiring, the command incorrectly exits 0. Repair only the
consumer, not the assertions.

- [ ] **Step 3: Merge workflow issues into the existing CLI result.**

Import the Task 1 validator. In `cmd_validate`, after existing prompt and agent
checks, load one current snapshot and add unique workflow issues using the
existing `seen` convention before building `combined`:

```python
snapshot = services.config_store.load()
for issue in validate_workflow_references(snapshot):
    key = (issue.code, issue.field, issue.message)
    if key not in seen:
        seen.add(key)
        issues.append(issue)
```

Retain `CliFailure`, `ExitCode.VALIDATION`, JSON serialization, and the current
success result unchanged. Rerun Step 2 immediately.

- [ ] **Step 4: Add a failing setup incomplete-to-ready regression.**

```python
def test_status_waits_for_workflow_definition(workflow_env):
    source = workflow_env.library.source_path("delivery")
    original = source.read_bytes()
    source.unlink()

    incomplete = inspect_setup_status(workflow_env.store)
    assert incomplete.state == "incomplete"
    assert "Board A" in incomplete.message
    assert not source.exists()

    source.write_bytes(original)
    assert inspect_setup_status(workflow_env.store).state == "ready"
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_setup_flow.py -k "status_waits_for_workflow_definition" -q
```

Expected: before wiring, the missing definition incorrectly produces `ready`.

- [ ] **Step 5: Add the shared setup gate and rerun that same regression.**

Replace `config = store.load().config` with the following, so the existing
no-team check and the new validator share the same parsed snapshot:

```python
snapshot = store.load()
config = snapshot.config
```

Before returning ready:

```python
issues = validate_workflow_references(snapshot)
if issues:
    first = issues[0]
    return SetupStatus(
        state="incomplete",
        message=f"{first.message} {first.corrective_hint}",
    )
```

Keep missing/invalid config handling unchanged. Do not invoke service building
inside the validator or add strict checks to ordinary app startup.

- [ ] **Step 6: Prove route and startup compatibility.**

Add to `tests/test_workflow_setup.py` using `workflow_web_env`, whose
`client` is the real test app, not a second global app instance:

```python
def test_setup_status_blocks_missing_definition_without_blocking_dashboard(
    workflow_web_env,
):
    source = workflow_web_env.library.source_path("delivery")
    original = source.read_bytes()
    source.unlink()

    status = workflow_web_env.client.get("/setup/status").json()
    assert status["state"] == "incomplete"
    assert "redirect" not in status
    assert "Board A" in status["message"]
    assert workflow_web_env.client.get("/newsletter/").status_code == 200

    source.write_bytes(original)
    ready = workflow_web_env.client.get("/setup/status").json()
    assert ready["state"] == "ready"
    assert ready["redirect"] == "/"
```

Also exercise malformed-source correction, no-workflow success, and
`build_services(snapshot.path).startup_error is None` after a referenced source
is removed. Compare config bytes, definition bytes, and seeded ticket contents
before and after repeated status inspection; omit transient lock metadata from
semantic-data comparisons.

- [ ] **Step 7: Add the browser regression with the existing test-only hook.**

Extend `POST /__ui/setup/ready` with an optional query mode
`definition=missing` or `definition=valid`; default behavior stays unchanged.
For missing mode, add one fixture-only local workflow referencing
`software-delivery`, use the existing safe runtime workflow and ticket roots,
and leave its selected source absent. For valid mode, publish the packaged
selected definition through `WorkflowLibrary.create_candidate` only if absent.
Use `_write_runtime_config` and refresh services as the current hook does.
Reject unsupported modes with HTTP 400. This is test-fixture code only.

Add this concrete regression beside the existing completion-redirect test:

```typescript
test('setup waits for its workflow definition before opening the dashboard', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  const missing = await request.post('/__ui/setup/ready?definition=missing');
  expect(missing.status()).toBe(204);
  await expect(page.locator('#status-message')).toContainText('cannot use blueprint');
  await expect(page).toHaveURL(/\/setup\/session$/);
  const incomplete = await (await request.get('/setup/status')).json();
  expect(incomplete.state).toBe('incomplete');
  expect(incomplete.redirect).toBeUndefined();

  const fixed = await request.post('/__ui/setup/ready?definition=valid');
  expect(fixed.status()).toBe(204);
  await expect(page).toHaveURL(/\/newsletter\/$/);
  const state = await page.evaluate(() =>
    fetch('/setup/session/state', { cache: 'no-store' }).then((response) => response.json())
  );
  expect(state.state).toBe('running');
  await assertNoConsoleErrors(page);
});
```

Do not delete the shared workflow-library root during reset. Preserve existing
default fixtures, screenshots, access controls, and native setup tests. If the
snapshot test after this regression sees stale fixture source, clear only the
owned fixture blueprint contents in the existing in-place reset path.

- [ ] **Step 8: Run focused Python and browser checks sequentially.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli_contract.py tests/test_setup_flow.py tests/test_workflow_setup.py -q
npx playwright test tests/ui/setup.spec.ts --grep "workflow definition|opens the dashboard when setup becomes ready|owning browser"
```

Run in the default four projects, without headed mode or snapshot updates.
Inspect diagnostics for touched files. Then run:

```powershell
git diff --check
git add -- flowgency/cli.py flowgency/web/setup_flow.py tests/test_cli_contract.py tests/test_setup_flow.py tests/test_workflow_setup.py tests/ui/server.py tests/ui/setup.spec.ts
git commit -m "fix(setup): require valid workflow references"
```

Review this task before proceeding. Template and route edits are not expected;
if a focused failure requires one, keep it local and rerun the same check.

### Task 3: Explicit Skill-Owned Blueprint Materialization

**Files:**
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/references/ticket-workflow-steps.md`
- Modify: `kb/setup-skill.md`
- Test: `tests/test_workflow_setup.py`, `tests/test_setup_skill_e2e.py`, `tests/test_setup_assets.py`

**Interfaces:**
- Consumes: `workflow_example_root() -> Path`, `WorkflowLibrary.inspect`, `WorkflowLibrary.create_candidate`, and Tasks 1-2 validation behavior.
- Produces: a documented create-or-reuse recipe run only after approval and before the one complete revision-checked config write.
- The canonical skill is the packaged file; repository aliases are links, not independent instruction copies.

- [ ] **Step 1: Add a failing executable-recipe regression.**

The user approved this pre-flight test-strategy correction on 2026-10-03:
execute the documented recipe and review the prose against the spec instead
of asserting exact prose substrings. Reuse the existing recipe-extraction
pattern and add this helper and behavior test to `tests/test_setup_skill_e2e.py`.

```python
def _documented_workflow_recipe():
    from flowgency.setup_assets import copilot_discovery_root

    path = copilot_discovery_root() / ".github/skills/flowgency-setup/SKILL.md"
    document = path.read_text(encoding="utf-8")
    heading = "\n### Approved Workflow Materialization Recipe\n"
    start = document.index(heading) + len(heading)
    section = document[start:]
    fence = "```python\n"
    open_at = section.index(fence) + len(fence)
    close_at = section.index("\n```", open_at)
    namespace = {}
    exec(compile(section[open_at:close_at], str(path), "exec"), namespace)
    return namespace["materialize_approved_workflow"]


def test_documented_recipe_creates_only_selected_blueprint(tmp_path):
    library_root = tmp_path / "workflow-library"
    library_root.mkdir()
    materialize = _documented_workflow_recipe()

    first = materialize(library_root, "software-delivery")
    assert first.definition.id == "software-delivery"
    source = first.source_path
    initial_bytes = source.read_bytes()
    second = materialize(library_root, "software-delivery")

    assert second.definition.id == "software-delivery"
    assert source.read_bytes() == initial_bytes
    assert not (library_root / "research").exists()
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_setup_skill_e2e.py::test_documented_recipe_creates_only_selected_blueprint -q
```

Expected: the current guidance has no executable creation recipe to run.

- [ ] **Step 2: Add the explicit creation step and executable API recipe.**

In the Workflow instances section, replace the claim that shipped examples
come from `ticket-workflow-steps.md` with their actual package API. Add these
normative rules before config publication:

```text
After workflow and path approval, materialize each selected definition in the
approved canonical workflow library before the single atomic config write.
Materialize only the selected approved blueprints.
Do not overwrite an existing workflow definition. Inspect and reuse a valid
existing definition, and stop on invalid, unsafe, unreadable, or mismatched
source. Existing source is not permission to replace it with a shipped example.
Locate shipped examples with workflow_example_root() and create an absent
selected definition with WorkflowLibrary.create_candidate(). Custom definitions
must match the approved transition design and pass the same domain validation.
```

Give the recipe the unique Markdown heading
`### Approved Workflow Materialization Recipe` so its fenced code can be
exercised by the existing end-to-end test file. Include it as Python API
guidance, not a new command. The
`library_root` and `selected_blueprint_id` parameters are the already-approved
in-session values; obtain no new path approval or hidden authority:

```python
from pathlib import Path

from flowgency.setup_assets import workflow_example_root
from flowgency.workflows.library import WorkflowLibrary


def materialize_approved_workflow(
    library_root: Path, selected_blueprint_id: str
):
    destination = WorkflowLibrary(library_root)
    source = destination.source_path(selected_blueprint_id)
    if source.exists():
        existing = destination.inspect(selected_blueprint_id)
        if existing.definition.id != selected_blueprint_id:
            raise ValueError("Existing workflow definition identity does not match")
        return existing

    example = WorkflowLibrary(workflow_example_root()).inspect(selected_blueprint_id)
    return destination.create_candidate(selected_blueprint_id, example.definition)
```

Do not swallow an unsafe-path or creation-conflict error and fall through to
overwrite. Existing API path checks and approved-root validation still govern
all source operations. Custom user definitions are validated and published
directly; they do not need a packaged example with their ID.

In Section 5 require destination inspection, then the single config write,
then the final mechanical validation. No scheduler install or completion claim
may occur on validation failure. In the reference and KB, explain this source
step and that normal dashboard startup does not automatically install examples.

- [ ] **Step 3: Rerun Step 1 immediately, then prove the recipe against real temporary storage.**

Extend `tests/test_setup_skill_e2e.py`, reusing `_materialize` and `_write_config`.
Use `_documented_workflow_recipe` from Step 1 so these tests execute the
documented code, not a duplicate implementation. Review the skill and reference
prose directly against the approved ordering, approval, no-overwrite, and
validation rules; do not add source-text substring assertions.

Create a customized valid existing definition and prove reuse preserves its
bytes. Replace it with malformed source and prove the recipe raises rather
than overwrites. Run the same recipe for a second shipped blueprint in a
separate directory; both must inspect validly.

Register the selected workflow under the test config's existing reviewer team,
using its private temporary ticket root. Execute real `cli.run(["--config",
str(config_path), "validate"])`; expect 0 after recipe creation and exit 3
after removing the source. Verify config bytes are unchanged by validation.

- [ ] **Step 4: Extend the installed-wheel proof.**

Reuse `built_wheel` in `tests/test_setup_assets.py`. The wheel must contain
`flowgency/workflows/validation.py`, the corrected canonical skill/reference,
and the existing shipped example bytes. Follow the existing isolated extracted
wheel test pattern: `-S`, extracted package first, current venv dependency paths
afterward. Assert `flowgency.__file__` points into the extraction, run the
documented recipe there, and inspect the created destination definition. No
checkout-relative template path or editable-finder import is acceptable.

- [ ] **Step 5: Run focused guidance, recipe, and installed-package tests.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_setup.py tests/test_setup_skill_e2e.py tests/test_setup_assets.py tests/test_flowgency_setup_skill.py -q
git diff --check
```

Check relevant file diagnostics. Stage only the listed guidance and test files:

```powershell
git add -- flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md flowgency/setup_assets/copilot/.github/skills/flowgency-setup/references/ticket-workflow-steps.md kb/setup-skill.md tests/test_workflow_setup.py tests/test_setup_skill_e2e.py tests/test_setup_assets.py
git commit -m "fix(setup): materialize approved workflow sources"
```

Review this task before the complete branch gates.

## Complete Branch Verification And Integration

- [ ] Run the entire Python suite from the feature worktree and inspect its final exit/result:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q --junitxml=.superpowers/final-python.junit.xml
```

- [ ] After Python completes, run the entire committed browser matrix in default headless mode:

```powershell
npx playwright test
```

- [ ] Inspect failures without weakening tests, changing unrelated code, or rebaselining screenshots. Keep durable output/receipts; a detached command remains pending until final completion is inspected.
- [ ] Review the complete diff against all seven spec acceptance criteria. Confirm normal startup compatibility, strict explicit validation, skill-owned selected-source creation, and untouched existing runtime data.
- [ ] Confirm only intended source/docs/tests are committed. Do not stage config, locks, credentials, runtime directories, venvs, dependency artifacts, or test reports.
- [ ] Follow `AGENTS.md` integration: rebase onto `master` only if it advanced, rerun the suite if rebased, fast-forward only, preserve and restore unrelated master changes, rerun the complete Python suite on master, push master and the feature branch, then remove/prune the owned worktree while retaining the branch.
- [ ] Verify publication and cleanup rather than inferring them from an intermediate output. Never delete unknown residual files during Windows worktree cleanup.
- [ ] Final report states what changed, actual verification results, and that no existing Atreides definition or other runtime data was repaired.

## Plan Self-Review

- [x] Every spec acceptance criterion maps to Tasks 1-3 and the complete branch gates.
- [x] Shared signatures and issue contracts are consistent across tasks.
- [x] Task 1 owns validation, Task 2 owns consumers, Task 3 owns creation guidance; no task depends on undefined production APIs.
- [x] Existing startup behavior and excluded runtime repair are explicit.
- [x] Concrete red/green checks and code are supplied for each implementation unit.
- [x] Existing test fixtures and installed-package proof patterns are reused.
- [x] The baseline and review-before-dependent-work requirements are recorded.