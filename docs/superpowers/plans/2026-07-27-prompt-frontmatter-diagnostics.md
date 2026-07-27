# Prompt Frontmatter Diagnostics And Setup Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make malformed blueprint prompts report their own accurate diagnostic, degrade to the agents that reference them instead of blocking the dashboard, and stop a clean setup from producing them in the first place.

**Architecture:** `validate_prompt_catalogs` stops masking structured errors and stops gating startup; it becomes a reporter whose results ride on `AgencyServices.prompt_issues`. Enforcement stays at the existing prompt-resolution call sites, with the two web route adapters degrading per agent. A new `christag-agency validate` command exposes the same issues to operators and to the setup skill, which gains the task-prompt template it never had plus an end-to-end test that runs its own templates through the real loaders.

**Tech Stack:** Python 3, FastAPI, Jinja2, Pydantic, PyYAML, argparse, pytest.

## Global Constraints

- Work in the existing worktree `.worktrees/prompt-frontmatter-diagnostics` on branch `feature/prompt-frontmatter-diagnostics`. Run every command from that directory.
- Run tests with `.venv/Scripts/python -m pytest` from the worktree root. Running from another checkout resolves the wrong local `tests` package.
- `config.yaml` with `schema_version: 4` stays the sole control-plane authority. No directory-shape loaders, no startup conversion, no native identity writers.
- Blueprint source stays hand-authored. Do not add a writer, route, or generator that emits blueprint prompt files.
- `skills/agency-setup/` and `.github/skills/agency-setup/` are the same directory on disk (`.github/skills/agency-setup` resolves to `skills/agency-setup`; `tests/test_agency_setup_skill.py::test_copilot_skill_discovery_resolves_to_canonical_source` asserts this). Edit the canonical path `skills/agency-setup/` only.
- Never stage or modify `config.yaml`, `config.yaml.lock`, group-state directories, logs, or other untracked runtime data.
- Commit after every task. Never use `--no-verify`.

---

## File Structure

**Modified:**
- `agency/prompts/catalog.py` — structured collision issue; uniform `ValidationFailed` collection; dedupe.
- `agency/web/dependencies.py` — `AgencyServices.prompt_issues`; catalog issues no longer raise.
- `agency/web/routes/agents.py` — `_launcher_prompts` degrades per agent instead of failing the roster.
- `agency/web/routes/agent_detail.py` — `_prompts_context` degrades and surfaces issues.
- `agency/templates/agent_detail_prompts.html` — render prompt issues.
- `agency/templates/agents.html` — render prompt issues in the launcher.
- `agency/cli.py` — `cmd_validate` and the `validate` subparser.
- `skills/agency-setup/references/templates.md` — Standard Task Prompt template.
- `skills/agency-setup/SKILL.md` — Phase 5 prompt validation and wording fix.
- `tests/test_agency_setup_skill.py` — assertions for the new skill content.

**Created:**
- `tests/test_prompt_catalog.py` — catalog validator behavior.
- `tests/test_setup_skill_e2e.py` — template conformance end to end.

---

### Task 1: Structured prompt catalog issues

Removes the `except ValueError` clause that relabels every structured error, and gives the name-collision case a first-class `ValidationIssue`.

**Files:**
- Modify: `agency/prompts/catalog.py`
- Test: `tests/test_prompt_catalog.py` (create)

**Interfaces:**
- Consumes: `ValidationIssue`, `ValidationFailed` from `agency.configuration.issues`; `AssetValidationError` (subclass of `ValidationFailed`) raised by `agency.prompts.assets.parse_prompt_document`; `PromptNotFoundError` (subclass of `RuntimeError`) from `agency.prompts.store`.
- Produces: `validate_prompt_catalogs(snapshot, library, store) -> tuple[ValidationIssue, ...]`, unchanged signature, now never masking. `_validate_effective_catalog` raises `ValidationFailed` rather than bare `ValueError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompt_catalog.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml

from agency.blueprints import BlueprintLibrary
from agency.configuration import ConfigStore
from agency.prompts import PromptStore, validate_prompt_catalogs


VALID_PROMPT = "---\nname: diff-review\ndescription: Review the change set.\n---\n\nReview it.\n"
NO_FRONTMATTER_PROMPT = "# Diff Review\n\nReview the change set.\n"


def _write_blueprint(library_root: Path, key: str, prompt_source: str) -> None:
    blueprint = library_root / key
    prompts = blueprint / ".agents" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {key.title()}\n", encoding="utf-8")
    (prompts / "diff-review.prompt.md").write_text(prompt_source, encoding="utf-8")


def _write_config(tmp_path: Path, agents: list[dict]) -> Path:
    workspace = tmp_path / "workspace"
    group_root = tmp_path / "groups" / "reviewers"
    workspace.mkdir(parents=True, exist_ok=True)
    group_root.mkdir(parents=True, exist_ok=True)
    raw = {
        "schema_version": 4,
        "agency": {
            "title": "Agency",
            "default_group": "reviewers",
            "ai_backend": "copilot",
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(tmp_path / "memory-store"),
            "prompt_store": str(tmp_path / "prompts"),
        },
        "groups": {
            "reviewers": {
                "name": "Reviewers",
                "workspace_path": str(workspace),
                "path": str(group_root),
                "default_integration": "copilot",
                "agents": agents,
            }
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def _validate(tmp_path: Path, config_path: Path):
    snapshot = ConfigStore(config_path).load()
    library = BlueprintLibrary(tmp_path / "agent-library")
    store = PromptStore(tmp_path / "prompts")
    return validate_prompt_catalogs(snapshot, library, store)


def _agent(name: str, blueprint: str) -> dict:
    return {"name": name, "blueprint": blueprint, "integration": "copilot"}


def test_malformed_blueprint_prompt_keeps_its_own_message_and_hint(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == "invalid-prompt-frontmatter"
    assert issue.field == ".agents/prompts/diff-review.prompt.md"
    assert issue.message == (
        "Prompt markdown frontmatter is incomplete: .agents/prompts/diff-review.prompt.md."
    )
    assert issue.corrective_hint == "Terminate the YAML frontmatter before the prompt body."
    assert issue.scope == "groups.reviewers.agents.reviewer"


def test_one_broken_blueprint_shared_by_two_agents_reports_once(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(
        tmp_path,
        [_agent("first", "reviewer"), _agent("second", "reviewer")],
    )

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1


def test_valid_blueprint_prompt_reports_no_issues(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    assert _validate(tmp_path, config_path) == ()


def test_name_collision_across_scopes_keeps_its_own_code_and_hint(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    private = tmp_path / "prompts" / "reviewers" / "reviewer"
    private.mkdir(parents=True, exist_ok=True)
    (private / "diff-review.prompt.md").write_text(VALID_PROMPT, encoding="utf-8")
    agent = _agent("reviewer", "reviewer")
    agent["prompts"] = ["diff-review"]
    config_path = _write_config(tmp_path, [agent])

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == "invalid-prompt-catalog"
    assert issue.scope == "groups.reviewers.agents.reviewer"
    assert issue.field == "prompts"
    assert issue.corrective_hint == "Use unique prompt names across blueprint and instance scopes."


def test_missing_instance_prompt_still_reports_its_own_code(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    agent = _agent("reviewer", "reviewer")
    agent["prompts"] = ["absent"]
    config_path = _write_config(tmp_path, [agent])

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1
    assert issues[0].code == "missing-instance-prompt"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -v`

Expected: `test_malformed_blueprint_prompt_keeps_its_own_message_and_hint` FAILS because `issue.code` is `invalid-prompt-catalog` and the hint is the unique-names hint. `test_one_broken_blueprint_shared_by_two_agents_reports_once` FAILS with 2 issues. The other three PASS.

- [ ] **Step 3: Replace the collision raise with a structured failure**

In `agency/prompts/catalog.py`, change the import line

```python
from agency.configuration.issues import ValidationIssue
```

to

```python
from dataclasses import dataclass, replace

from agency.configuration.issues import ValidationFailed, ValidationIssue
```

(the file already imports `dataclass`; merge the names into the existing `from dataclasses import dataclass` line rather than duplicating it).

Replace `_validate_effective_catalog` with:

```python
def _validate_effective_catalog(
    prompts: tuple[CatalogPrompt, ...],
    group_id: str,
    agent_id: str,
) -> tuple[CatalogPrompt, ...]:
    seen: dict[str, str] = {}
    for item in prompts:
        prior_scope = seen.get(item.document.name)
        if prior_scope is not None and prior_scope != item.scope:
            raise ValidationFailed(
                (
                    ValidationIssue(
                        code="invalid-prompt-catalog",
                        scope=f"groups.{group_id}.agents.{agent_id}",
                        field="prompts",
                        message=(
                            f"Prompt '{item.document.name}' exists in both blueprint and "
                            f"instance scopes for {group_id}/{agent_id}."
                        ),
                        corrective_hint="Use unique prompt names across blueprint and instance scopes.",
                    ),
                )
            )
        seen[item.document.name] = item.scope
    return prompts
```

- [ ] **Step 4: Replace the validator with uniform collection and dedupe**

Replace `validate_prompt_catalogs` with:

```python
def _agent_scoped(issue: ValidationIssue, group_id: str, agent_id: str) -> ValidationIssue:
    scope = f"groups.{group_id}.agents.{agent_id}"
    return issue if issue.scope == scope else replace(issue, scope=scope)


def validate_prompt_catalogs(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    store: PromptStore,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for group_id, group in snapshot.config.groups.items():
        for agent_id in group.agents:
            try:
                effective_prompt_catalog(snapshot, library, store, group_id, agent_id)
            except PromptNotFoundError as exc:
                collected: tuple[ValidationIssue, ...] = (
                    ValidationIssue(
                        code="missing-instance-prompt",
                        scope=f"groups.{group_id}.agents.{agent_id}",
                        field="prompts",
                        message=str(exc),
                        corrective_hint="Register only prompt names that exist in the configured prompt store.",
                    ),
                )
            except ValidationFailed as exc:
                collected = tuple(_agent_scoped(issue, group_id, agent_id) for issue in exc.issues)
            else:
                continue
            for issue in collected:
                key = (issue.code, issue.field, issue.message)
                if key in seen:
                    continue
                seen.add(key)
                issues.append(issue)
    return tuple(issues)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -v`

Expected: 5 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`

Expected: no new failures. Record any pre-existing failures as the baseline.

- [ ] **Step 7: Commit**

```bash
git add agency/prompts/catalog.py tests/test_prompt_catalog.py
git commit -m "Preserve structured prompt asset issues in catalog validation"
```

---

### Task 2: Non-fatal prompt issues at startup

Stops one malformed prompt file from taking the whole dashboard down to the setup page.

**Files:**
- Modify: `agency/web/dependencies.py`
- Test: `tests/test_prompt_catalog.py`

**Interfaces:**
- Consumes: `validate_prompt_catalogs` from Task 1.
- Produces: `AgencyServices.prompt_issues: tuple[ValidationIssue, ...]`, defaulting to `()`. Consumed by Tasks 3 and 4.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_catalog.py`:

```python
def test_build_services_reports_prompt_issues_without_failing_startup(tmp_path):
    from agency.web.dependencies import build_services

    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    services = build_services(config_path)

    assert services.startup_error is None
    assert services.instances is not None
    assert services.blueprint_library is not None
    assert [issue.code for issue in services.prompt_issues] == ["invalid-prompt-frontmatter"]


def test_build_services_reports_no_prompt_issues_for_a_valid_library(tmp_path):
    from agency.web.dependencies import build_services

    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    services = build_services(config_path)

    assert services.startup_error is None
    assert services.prompt_issues == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -k build_services -v`

Expected: both FAIL with `AttributeError: 'AgencyServices' object has no attribute 'prompt_issues'`, and the first also has a non-`None` `startup_error`.

- [ ] **Step 3: Add the field**

In `agency/web/dependencies.py`, extend the dataclass:

```python
    integrations: Mapping[str, BaseIntegration]
    startup_error: Exception | None = None
    prompt_service: PromptService | None = None
    prompt_issues: tuple[ValidationIssue, ...] = ()
```

Add the import next to the existing configuration imports:

```python
from agency.configuration import ConfigStore, ValidationFailed
from agency.configuration.issues import ValidationIssue
```

- [ ] **Step 4: Stop raising and start reporting**

In `build_services`, replace

```python
        catalog_issues = validate_prompt_catalogs(snapshot, blueprint_library, prompt_store)
        if catalog_issues:
            raise ValidationFailed(catalog_issues)
```

with

```python
        catalog_issues = validate_prompt_catalogs(snapshot, blueprint_library, prompt_store)
```

and pass it through on the success return:

```python
            integrations=REGISTRY,
            startup_error=None,
            prompt_issues=catalog_issues,
        )
```

Leave the failure return untouched; `prompt_issues` defaults to `()` there.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -v`

Expected: 7 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`

Expected: no new failures beyond the Task 1 baseline. If a test asserted that a malformed prompt produces a startup error, update it to assert `prompt_issues` instead, and note the change in the commit body.

- [ ] **Step 7: Commit**

```bash
git add agency/web/dependencies.py tests/test_prompt_catalog.py
git commit -m "Report prompt catalog issues without failing startup"
```

---

### Task 3: Per-agent route degradation

Keeps the roster and agent detail rendering when one agent's catalog cannot resolve, and shows the diagnostic where the operator is looking.

**Files:**
- Modify: `agency/web/routes/agents.py:160-176`
- Modify: `agency/web/routes/agent_detail.py:490-530`
- Modify: `agency/templates/agents.html:149`
- Modify: `agency/templates/agent_detail_prompts.html:1-6`
- Test: `tests/test_prompt_catalog.py`

**Interfaces:**
- Consumes: `AgencyServices.prompt_issues` from Task 2; `_issue_dicts(exc: ValidationFailed | tuple) -> list[dict[str, str]]` already defined at `agency/web/routes/agent_detail.py:109`, producing dicts with keys `code`, `field`, `message`, `hint`.
- Produces: template context key `prompt_issues` (a list of those dicts) on both the agent detail Prompts tab and each roster instance row.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_catalog.py`:

```python
def _client(monkeypatch, tmp_path, config_path):
    from fastapi.testclient import TestClient

    from agency import app as app_mod

    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app)


def test_roster_renders_when_one_agent_has_a_broken_catalog(monkeypatch, tmp_path):
    library_root = tmp_path / "agent-library"
    _write_blueprint(library_root, "reviewer", NO_FRONTMATTER_PROMPT)
    _write_blueprint(library_root, "auditor", VALID_PROMPT)
    config_path = _write_config(
        tmp_path,
        [_agent("reviewer", "reviewer"), _agent("auditor", "auditor")],
    )

    response = _client(monkeypatch, tmp_path, config_path).get("/reviewers/agents")

    assert response.status_code == 200
    assert "Terminate the YAML frontmatter before the prompt body." in response.text
    assert "auditor" in response.text


def test_agent_detail_prompts_tab_shows_the_diagnostic(monkeypatch, tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    response = _client(monkeypatch, tmp_path, config_path).get(
        "/reviewers/agents/reviewer/prompts"
    )

    assert response.status_code == 200
    assert "Prompt markdown frontmatter is incomplete" in response.text
    assert "Terminate the YAML frontmatter before the prompt body." in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -k "roster_renders or prompts_tab" -v`

Expected: the roster test FAILS with status 409; the detail test FAILS with a 500 raised from `AssetValidationError`.

- [ ] **Step 3: Degrade the roster launcher**

In `agency/web/routes/agents.py`, replace `_launcher_prompts` with:

```python
def _launcher_prompts(
    services: AgencyServices, snapshot, group_id: str, agent_id: str
) -> tuple[tuple[dict[str, str], ...], tuple[dict[str, str], ...]]:
    if services.prompt_service is None:
        raise HTTPException(status_code=409, detail="Prompt service unavailable")
    try:
        catalog = services.prompt_service.catalog(snapshot, group_id, agent_id)
    except ValidationFailed as exc:
        return (), tuple(
            {
                "code": issue.code,
                "field": issue.field,
                "message": issue.message,
                "hint": issue.corrective_hint,
            }
            for issue in exc.issues
        )
    except (OSError, PromptNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return (
        tuple(
            {
                "scope": item.scope,
                "name": item.document.name,
                "description": item.document.description,
                "argument_hint": item.document.argument_hint or "",
                "source_path": item.source_path,
            }
            for item in catalog
        ),
        (),
    )
```

`AssetValidationError` derives from `ValidationFailed`, so the first clause catches it; keep that clause above the `ValueError` clause. The `AssetValidationError` import at the top of the file becomes unused — remove it.

In `_instance_rows`, change the call site:

```python
        prompt_rows, prompt_issues = _launcher_prompts(services, snapshot, group_id, instance.name)
```

and add one key to the `row.update({...})` mapping, next to `"has_saved_prompts"`:

```python
                "prompt_issues": prompt_issues,
```

- [ ] **Step 4: Render the roster notice**

In `agency/templates/agents.html`, immediately after the line

```html
        <section data-saved-panel {% if instance.default_mode != 'saved' %}hidden{% endif %} class="space-y-4">
```

insert:

```html
          {% if instance.prompt_issues %}
          <div class="rounded-xl border border-amber-200 bg-amber-50 p-3 space-y-2">
            <p class="text-xs font-semibold uppercase tracking-wide text-amber-900">Prompt catalog unavailable</p>
            {% for issue in instance.prompt_issues %}
            <div class="text-sm text-amber-900">
              <div class="font-mono text-xs text-amber-800">{{ issue.field }}</div>
              <div>{{ issue.message }}</div>
              <div class="text-xs text-amber-700">{{ issue.hint }}</div>
            </div>
            {% endfor %}
          </div>
          {% endif %}
```

- [ ] **Step 5: Degrade the agent detail Prompts tab**

In `agency/web/routes/agent_detail.py`, inside `_prompts_context`, replace

```python
    catalog = services.prompt_service.catalog(snapshot, group_id, agent_id)
```

with

```python
    try:
        catalog = services.prompt_service.catalog(snapshot, group_id, agent_id)
    except ValidationFailed as exc:
        return {
            "shared_prompts": (),
            "private_prompts": (),
            "prompt_issues": _issue_dicts(exc),
            "create_prompt_name": create_name,
            "create_prompt_source": create_source,
        }
```

and add `"prompt_issues": [],` to the mapping returned at the end of the function.

- [ ] **Step 6: Render the detail notice**

In `agency/templates/agent_detail_prompts.html`, insert after the closing `</div>` of the heading block (the one containing `<h2 class="text-lg font-semibold text-gray-900">Prompts</h2>`) and before `<div class="grid gap-4 lg:grid-cols-2">`:

```html
  {% if prompt_issues %}
  <div class="rounded-lg border border-amber-200 bg-amber-50 p-4 space-y-3">
    <p class="text-sm font-semibold text-amber-900">Prompt catalog unavailable</p>
    {% for issue in prompt_issues %}
    <div class="text-sm text-amber-900">
      <div class="font-mono text-xs text-amber-800">{{ issue.field }}</div>
      <div>{{ issue.message }}</div>
      <div class="text-xs text-amber-700">{{ issue.hint }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -v`

Expected: 9 passed.

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`

Expected: no new failures. `tests/test_agent_roster.py` and `tests/test_agent_detail.py` exercise both changed functions; if either asserts the old `_launcher_prompts` single-value return, update the assertion.

- [ ] **Step 9: Commit**

```bash
git add agency/web/routes/agents.py agency/web/routes/agent_detail.py agency/templates/agents.html agency/templates/agent_detail_prompts.html tests/test_prompt_catalog.py
git commit -m "Degrade prompt catalog failures to the affected agent"
```

---

### Task 4: The validate command

Gives operators and the setup skill one mechanical check with a meaningful exit code.

**Files:**
- Modify: `agency/cli.py`
- Test: `tests/test_prompt_catalog.py`

**Interfaces:**
- Consumes: `AgencyServices.prompt_issues` from Task 2; `_services(args) -> AgencyServices` at `agency/cli.py:121`, which already raises `CliFailure(ExitCode.VALIDATION, ...)` for fatal config errors; `CliFailure` at `agency/cli.py:87`; `ExitCode` from `agency.cli_output`.
- Produces: `cmd_validate(args: Namespace) -> int` and the `validate` subcommand. Exit code 0 when clean, 3 (`ExitCode.VALIDATION`) when any issue exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_catalog.py`:

```python
def test_validate_command_reports_the_prompt_issue(tmp_path, capsys):
    from agency import cli

    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    exit_code = cli.run(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()

    assert exit_code == 3
    assert "Prompt markdown frontmatter is incomplete" in captured.err
    assert "Terminate the YAML frontmatter before the prompt body." in captured.err


def test_validate_command_succeeds_for_a_valid_library(tmp_path, capsys):
    from agency import cli

    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    exit_code = cli.run(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No validation issues found." in captured.out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -k validate_command -v`

Expected: both FAIL with exit code 2 (argparse usage error for the unknown `validate` command).

- [ ] **Step 3: Add the handler**

In `agency/cli.py`, add above `def cmd_serve(args: Namespace) -> int:`:

```python
def cmd_validate(args: Namespace) -> int:
    services = _services(args)
    issues = tuple(services.prompt_issues)
    if issues:
        raise CliFailure(ExitCode.VALIDATION, "validation-failed", "Validation failed", issues)
    if getattr(args, "json", False):
        print(json.dumps({"issues": []}, sort_keys=True))
    else:
        print("No validation issues found.")
    return int(ExitCode.SUCCESS)
```

- [ ] **Step 4: Register the subcommand**

In `build_parser`, directly after the `status` block:

```python
    validate = subparsers.add_parser("validate", help="Validate the config and configured assets")
    _add_config(validate)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=cmd_validate)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_prompt_catalog.py -v`

Expected: 11 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add agency/cli.py tests/test_prompt_catalog.py
git commit -m "Add a validate command for config and asset diagnostics"
```

---

### Task 5: Setup skill task prompt template

The prevention fix. Without this, a clean setup keeps producing frontmatter-less prompt files.

**Files:**
- Modify: `skills/agency-setup/references/templates.md`
- Modify: `skills/agency-setup/SKILL.md`
- Test: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Consumes: the prompt contract enforced by `agency/prompts/assets.py::parse_prompt_document`.
- Produces: a `## Standard Task Prompt` section in `references/templates.md` containing one fenced ` ```markdown ` block. Task 6 extracts that block by heading name.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agency_setup_skill.py`:

```python
def test_templates_define_the_task_prompt_contract():
    templates = TEMPLATES_PATH.read_text(encoding="utf-8")
    assert "## Standard Task Prompt" in templates
    assert "{agent_library}/{blueprint}/.agents/prompts/{prompt}.prompt.md" in templates
    for required in (
        "name: {prompt}",
        "description: {ONE_LINE_PURPOSE}",
        "argument-hint: {OPTIONAL_ARGUMENT_SUMMARY}",
    ):
        assert required in templates
    assert "exactly equals the file slug" in templates
    assert "at most 1024 characters" in templates


def test_phase_five_validates_prompt_documents():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "christag-agency validate --config" in skill
    assert "prompt document" in skill
    assert "routine skill," not in skill
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agency_setup_skill.py -k "task_prompt_contract or phase_five" -v`

Expected: both FAIL on the first assertion.

- [ ] **Step 3: Add the template section**

In `skills/agency-setup/references/templates.md`, replace the closing paragraph of the "Standard Agent Skill" section

```markdown
Use standard `scripts/`, `references/`, and `assets/` subdirectories when needed. Task prompts live separately under `{agent_library}/{blueprint}/.agents/prompts/{prompt}.prompt.md`, and routines select a scoped prompt rather than a skill.
```

with

```markdown
Use standard `scripts/`, `references/`, and `assets/` subdirectories when needed. Routines select a scoped prompt rather than a skill.

## Standard Task Prompt

Create each routine task as a portable prompt document at `{agent_library}/{blueprint}/.agents/prompts/{prompt}.prompt.md`:

```markdown
---
name: {prompt}
description: {ONE_LINE_PURPOSE}
argument-hint: {OPTIONAL_ARGUMENT_SUMMARY}
---

# {Prompt Title}

{TASK_INSTRUCTIONS}
```

Agency rejects any prompt document that breaks this contract:

- The file lives at `.agents/prompts/{prompt}.prompt.md` under the blueprint root. No other location is accepted.
- The YAML frontmatter opens with `---` on its own line and is terminated by `---` on its own line before the body.
- `name` exactly equals the file slug.
- The slug uses 1 to 64 lowercase letters, digits, and single hyphen separators.
- `description` is a non-empty string of at most 1024 characters.
- `argument-hint` is optional and, when present, a string. Omit the key entirely when the prompt takes no arguments.
- No keys other than `name`, `description`, and `argument-hint` are permitted.
- The markdown body after the closing `---` is non-empty.
```

- [ ] **Step 4: Add Phase 5 validation**

In `skills/agency-setup/SKILL.md`, in the `## 5. Verify And Schedule` section, replace

```text
Validate every blueprint and Agent Skill, config cross-reference, registered explicit integration, effective root union, complete tool override, routine skill, channel, workspace, group naming, and storage path.
```

with

```text
Validate every blueprint, Agent Skill, and prompt document, plus config cross-reference, registered explicit integration, effective root union, complete tool override, routine prompt selection, channel, workspace, group naming, and storage path. Confirm every prompt document against the Standard Task Prompt contract in `references/templates.md` before writing the config. After the config write and revision confirmation, run the mechanical check and stop on a non-zero exit:

```text
christag-agency validate --config "{config_path}"
```

A non-zero exit means the created blueprint source is invalid. Report the printed issues and correct them; do not present setup as complete.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agency_setup_skill.py -v`

Expected: all pass. If `test_setup_writes_routines_directly_from_assignments` or another existing test asserted the old "routine skill," wording, update it to the new wording in the same commit.

- [ ] **Step 6: Confirm the mirrored skill path is unchanged**

Run: `.venv/Scripts/python -m pytest tests/test_agency_setup_skill.py::test_copilot_skill_discovery_resolves_to_canonical_source -v`

Expected: PASS. `.github/skills/agency-setup` resolves to the same directory, so no second edit is needed.

- [ ] **Step 7: Commit**

```bash
git add skills/agency-setup/references/templates.md skills/agency-setup/SKILL.md tests/test_agency_setup_skill.py
git commit -m "Specify the task prompt contract in the setup skill"
```

---

### Task 6: Setup skill end-to-end template conformance

Runs the skill's own templates through the real loaders, so template drift fails the suite instead of shipping.

**Files:**
- Create: `tests/test_setup_skill_e2e.py`

**Interfaces:**
- Consumes: `## Blueprint AGENTS.md`, `## Standard Agent Skill`, and `## Standard Task Prompt` sections of `skills/agency-setup/references/templates.md` (Task 5); `build_services` and `AgencyServices.prompt_issues` (Task 2); the `validate` command (Task 4).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_setup_skill_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path
import re

import yaml

from agency import cli
from agency.web.dependencies import build_services


REPO_ROOT = Path(__file__).parents[1]
TEMPLATES_PATH = REPO_ROOT / "skills" / "agency-setup" / "references" / "templates.md"

SUBSTITUTIONS = {
    "{ROLE_NAME}": "Reviewer",
    "{LANGUAGE_OR_DOMAIN}": "Python",
    "{REUSABLE_ROLE_MISSION}": "Review change sets for defects.",
    "{RESPONSIBILITY}": "Report findings with file and line citations.",
    "{skill}": "diff-review",
    "{Skill Title}": "Diff Review",
    "{CONCRETE_TRIGGER_CONDITION}": "reviewing a change set",
    "{EXPECTED_RESULT}": "Findings recorded through the configured pipeline.",
    "{TASK}": "the review",
    "{TASK_SPECIFIC_BOUNDARY}": "Ignore style and formatting.",
    "{prompt}": "diff-review",
    "{Prompt Title}": "Diff Review",
    "{ONE_LINE_PURPOSE}": "Review the current change set for defects.",
    "{OPTIONAL_ARGUMENT_SUMMARY}": "Optional review focus",
    "{TASK_INSTRUCTIONS}": "Review the current change set and report findings.",
}


def _template_block(document: str, heading: str) -> str:
    marker = f"\n## {heading}\n"
    start = document.index(marker) + len(marker)
    section = document[start:]
    next_heading = section.find("\n## ")
    if next_heading != -1:
        section = section[:next_heading]
    fence = "```markdown\n"
    open_at = section.index(fence) + len(fence)
    close_at = section.index("\n```", open_at)
    return section[open_at:close_at] + "\n"


def _render(block: str) -> str:
    rendered = block
    for placeholder, value in SUBSTITUTIONS.items():
        rendered = rendered.replace(placeholder, value)
    leftover = re.search(r"\{[A-Za-z_ ]+\}", rendered)
    assert leftover is None, f"Unsubstituted placeholder {leftover.group(0)!r}"
    return rendered


def _materialize(tmp_path: Path) -> Path:
    document = TEMPLATES_PATH.read_text(encoding="utf-8")
    library_root = tmp_path / "agent-library"
    blueprint = library_root / "reviewer"
    skill_dir = blueprint / ".agents" / "skills" / "diff-review"
    prompt_dir = blueprint / ".agents" / "prompts"
    skill_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(
        _render(_template_block(document, "Blueprint AGENTS.md")), encoding="utf-8"
    )
    (skill_dir / "SKILL.md").write_text(
        _render(_template_block(document, "Standard Agent Skill")), encoding="utf-8"
    )
    (prompt_dir / "diff-review.prompt.md").write_text(
        _render(_template_block(document, "Standard Task Prompt")), encoding="utf-8"
    )
    return library_root


def _write_config(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    group_root = tmp_path / "groups" / "reviewers"
    workspace.mkdir(parents=True, exist_ok=True)
    group_root.mkdir(parents=True, exist_ok=True)
    raw = {
        "schema_version": 4,
        "agency": {
            "title": "Agency",
            "default_group": "reviewers",
            "ai_backend": "copilot",
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(tmp_path / "memory-store"),
            "prompt_store": str(tmp_path / "prompts"),
        },
        "groups": {
            "reviewers": {
                "name": "Reviewers",
                "workspace_path": str(workspace),
                "path": str(group_root),
                "default_integration": "copilot",
                "agents": [
                    {
                        "name": "reviewer",
                        "blueprint": "reviewer",
                        "integration": "copilot",
                        "routines": [
                            {
                                "id": "diff-review",
                                "prompt": {"scope": "blueprint", "name": "diff-review"},
                                "schedule": {"at": "09:00"},
                            }
                        ],
                    }
                ],
            }
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def test_setup_templates_produce_a_library_the_app_accepts(tmp_path):
    _materialize(tmp_path)
    config_path = _write_config(tmp_path)

    services = build_services(config_path)

    assert services.startup_error is None
    assert services.prompt_issues == ()

    inspection = services.blueprint_library.inspect("reviewer")
    assert inspection.title == "Reviewer"
    assert list(inspection.skills) == ["diff-review"]
    prompt = next(item for item in inspection.prompts if item.name == "diff-review")
    assert prompt.description == "Review the current change set for defects."
    assert prompt.argument_hint == "Optional review focus"
    assert prompt.body.strip()


def test_validate_accepts_a_library_built_from_the_templates(tmp_path, capsys):
    _materialize(tmp_path)
    config_path = _write_config(tmp_path)

    exit_code = cli.run(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No validation issues found." in captured.out


def test_validate_rejects_the_same_library_without_prompt_frontmatter(tmp_path, capsys):
    library_root = _materialize(tmp_path)
    prompt_path = library_root / "reviewer" / ".agents" / "prompts" / "diff-review.prompt.md"
    body = prompt_path.read_text(encoding="utf-8").split("\n---\n", 1)[1].lstrip("\n")
    prompt_path.write_text(body, encoding="utf-8")
    config_path = _write_config(tmp_path)

    exit_code = cli.run(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()

    assert exit_code == 3
    assert "Prompt markdown frontmatter is incomplete" in captured.err
    assert "Terminate the YAML frontmatter before the prompt body." in captured.err


def test_both_skill_copies_expose_identical_templates():
    canonical = REPO_ROOT / "skills" / "agency-setup" / "references" / "templates.md"
    discovery = REPO_ROOT / ".github" / "skills" / "agency-setup" / "references" / "templates.md"
    assert discovery.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_setup_skill_e2e.py -v`

Expected: 4 passed. `BlueprintInspection` exposes `title: str`, `skills: tuple[str, ...]` of directory slugs, and `prompts: tuple[PromptDocument, ...]`, so these assertions match the real shapes.

- [ ] **Step 3: Verify the negative control actually controls**

Temporarily revert Task 1 by re-adding an `except ValueError` clause that relabels issues, run `.venv/Scripts/python -m pytest tests/test_setup_skill_e2e.py::test_validate_rejects_the_same_library_without_prompt_frontmatter -v`, and confirm it FAILS on the hint assertion. Restore the Task 1 code and re-run to confirm it passes.

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`

Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_setup_skill_e2e.py
git commit -m "Validate setup skill templates end to end"
```

---

### Task 7: Repair the existing agent library prompts

A data fix outside the repository, performed last so the `validate` command from Task 4 can confirm it.

**Files:**
- Modify (outside the repo): `{agent_library}/reviewer/.agents/prompts/diff-review.prompt.md`
- Modify (outside the repo): `{agent_library}/architect/.agents/prompts/authority-audit.prompt.md`
- Modify (outside the repo): `{agent_library}/test-engineer/.agents/prompts/suite-health.prompt.md`
- Modify (outside the repo): `{agent_library}/docs-writer/.agents/prompts/docs-audit.prompt.md`

**Interfaces:**
- Consumes: the `validate` command from Task 4.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Resolve the library root**

Read `agency.agent_library` from the operator's `config.yaml` (do not modify that file). Confirm the four prompt files exist under it.

- [ ] **Step 2: Confirm the current failure**

Run: `.venv/Scripts/python -m agency.cli validate --config <config_path>`

Expected: exit code 3, with four `invalid-prompt-frontmatter` issues naming the four files.

- [ ] **Step 3: Prepend frontmatter, preserving each body verbatim**

For each file, insert a frontmatter block above the existing content. Do not edit the bodies and do not add `argument-hint`; none of the four takes arguments.

`reviewer/.agents/prompts/diff-review.prompt.md`:

```markdown
---
name: diff-review
description: Review the current change set for high-confidence bugs, security vulnerabilities, and logic errors.
---
```

`architect/.agents/prompts/authority-audit.prompt.md`:

```markdown
---
name: authority-audit
description: Audit that config.yaml remains the sole control-plane authority.
---
```

`test-engineer/.agents/prompts/suite-health.prompt.md`:

```markdown
---
name: suite-health
description: Run the pytest suite and report failing, flaky, or uncovered failure paths.
---
```

`docs-writer/.agents/prompts/docs-audit.prompt.md`:

```markdown
---
name: docs-audit
description: Audit documentation for drift from current behavior and the canonical config shape.
---
```

Each file must end up as the frontmatter block, one blank line, then the original body unchanged.

- [ ] **Step 4: Confirm the repair**

Run: `.venv/Scripts/python -m agency.cli validate --config <config_path>`

Expected: exit code 0 and `No validation issues found.`

- [ ] **Step 5: Confirm the dashboard reaches its normal pages**

Run: `.venv/Scripts/python -m agency.app`

Expected: the dashboard serves the group roster rather than redirecting to `/setup`, and the agent detail Prompts tab lists each repaired prompt with its description. Stop the server afterwards.

- [ ] **Step 6: Confirm nothing in the repo changed**

Run: `git status --short`

Expected: empty. This task touches only files outside the repository. If anything is listed, do not commit it — investigate first.

---

## Completion

- [ ] Run the complete suite one final time: `.venv/Scripts/python -m pytest tests/ -q`
- [ ] Review the whole branch before integrating: `git log --oneline master..HEAD` and `git diff master...HEAD`
- [ ] Fast-forward `master` to the reviewed tip. Do not merge, squash, or rebase.
- [ ] Re-run the complete suite on the fast-forwarded `master`.
- [ ] Remove the worktree: `git worktree remove .worktrees/prompt-frontmatter-diagnostics`. Keep the branch.
