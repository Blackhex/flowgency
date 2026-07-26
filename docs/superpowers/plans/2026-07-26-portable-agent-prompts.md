# Portable Agent Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add portable shared and instance-private Markdown prompts, prompt-backed schedules and manual launches, native prompt projection, and an operations-first Agents roster.

**Architecture:** Canonical prompt parsing lives in a focused `agency.prompts` package. Blueprint prompts are digest-addressed shared source, private prompts are config-registered files in `agency.prompt_store`, and job schema v4 snapshots task text plus private prompt sources before the worker creates a job-local native overlay. Config accepts only schema v4; durable jobs write v4 while retaining explicit read support for historical v3 records.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, Jinja2, PyYAML, `tomli-w`, portalocker, pytest, TypeScript, Playwright, Tailwind utility classes.

## Global Constraints

- Run every command from `C:\Users\black\Projects\christag-agency\.worktrees\portable-agent-prompts`; use `..\..\.venv\Scripts\python.exe` when the worktree has no local virtual environment.
- Keep `config.yaml` with schema version 4 as the sole control-plane authority; do not add config v3 aliases, conversion, dual reads, fallback paths, or startup migration.
- Keep durable job v3 read compatibility separate from config compatibility; create only durable job v4 records after the cutover.
- Shared prompts live only at `<agent_library>/<blueprint>/.agents/prompts/<slug>.prompt.md`.
- Registered private prompts live only at `<prompt_store>/<group>/<instance>/<slug>.prompt.md`; config never accepts an arbitrary prompt path or body.
- Do not discover private prompts by listing directories. Only `AgentInstance.prompts` registrations enter catalogs, projections, routines, or launches.
- Prompts define tasks. `AGENTS.md` and Agent Skills remain orthogonal behavior source; new jobs do not select or explicitly activate a skill.
- Never write native prompt files into a project workspace or user home. Generated native files belong to the shared compilation cache or a private job launch view.
- Preserve immutable task input, prompt provenance, runtime policy, semantic memory binding, concurrent jobs, decisions, proposals, observations, dashboard, workspace, and activity behavior.
- Reuse revision-checked config patches, source digests, portalocker locks, atomic replacement, root-containment checks, and Windows reparse-point protection.
- Do not edit historical design specifications or implementation plans. Update only current product docs, examples, setup assets, and tests.
- Do not begin a dependent task until the previous task's focused tests and reviewer gate pass.
- The clean baseline is `1265 passed, 2 skipped`.

## File Structure

| File | Responsibility |
| --- | --- |
| `agency/prompts/assets.py` | Parse, validate, serialize, and digest canonical prompt Markdown; construct deterministic task input. |
| `agency/prompts/store.py` | Resolve safe private paths and perform locked atomic source CRUD/namespace copies. |
| `agency/prompts/catalog.py` | Build effective shared/private catalogs, resolve explicit selectors, validate registrations/references, and create immutable prompt provenance. |
| `agency/prompts/projection.py` | Render canonical prompt documents to Copilot, Claude, and Gemini native formats and project job snapshots. |
| `agency/prompts/service.py` | Coordinate private prompt files with revision-checked config registration and usage checks. |
| `agency/prompts/__init__.py` | Export the public prompt-domain interfaces used by services, jobs, and web routes. |
| `agency/configuration/models.py` | Own schema-v4 `prompt_store`, `PromptSelector`, private registrations, and prompt-backed routines. |
| `agency/configuration/paths.py` | Validate and initialize the disjoint prompt-store authority root. |
| `agency/configuration/patches.py` | Register and unregister private prompt slugs without exposing paths. |
| `agency/blueprints/library.py` / `models.py` | Include validated shared prompt documents in blueprint inspection and digest-backed catalogs. |
| `agency/blueprints/projectors.py` / `agency/projector_capabilities.py` | Declare prompt projection capability and include shared prompts in runtime output inventories. |
| `agency/jobs/models.py` | Write durable job v4, read v3/v4, and carry immutable private prompt snapshots. |
| `agency/jobs/resolution.py` / `prompts.py` / `submission.py` | Resolve scoped prompt source, build task input, bind provenance, and submit immutable jobs. |
| `agency/jobs/execution.py` | Render private prompt snapshots into a job-local launch view before integration execution. |
| `agency/instances.py` | Move and remove private prompt namespaces with instance lifecycle operations. |
| `agency/web/routes/admin_library.py` | Own shared prompt authoring in the Agent Library. |
| `agency/web/routes/agent_detail.py` | Own private prompt authoring and scoped routine editing. |
| `agency/web/routes/agents.py` / `agency/templates/agents.html` | Build the effective launch catalog, hide creation in a dialog, and expose fully expanded launch controls. |
| `agency/app.py` | Accept saved-prompt or one-off manual launch submissions at the existing run endpoint. |

---

### Task 1: Canonical Prompt Assets And Blueprint Catalog

**Files:**
- Create: `agency/prompts/__init__.py`
- Create: `agency/prompts/assets.py`
- Create: `tests/test_prompt_assets.py`
- Modify: `agency/blueprints/models.py:6-14`
- Modify: `agency/blueprints/library.py:11-169`
- Modify: `tests/test_blueprint_library.py:1-220`

**Interfaces:**
- Produces: `PromptDocument(name: str, description: str, argument_hint: str | None, body: str, source: bytes, digest: str)`.
- Produces: `parse_prompt_document(path: str | PurePosixPath, payload: bytes) -> PromptDocument`.
- Produces: `prompt_source_path(name: str) -> PurePosixPath`.
- Produces: `build_prompt_task_input(body: str, *, arguments: tuple[str, ...] = (), invocation_input: str = "") -> str`.
- Produces: `BlueprintInspection.prompts: tuple[PromptDocument, ...]` for Tasks 2, 4, 7, 8, and 9.

- [ ] **Step 1: Write failing canonical-format tests**

```python
from pathlib import PurePosixPath

import pytest

from agency.fs.snapshot import AssetValidationError
from agency.prompts.assets import parse_prompt_document


def prompt_bytes(name: str = "pr-review", body: str = "Review the pull request.\n") -> bytes:
    return (
        f"---\nname: {name}\ndescription: Review pull requests.\n"
        "argument-hint: Optional review focus\n---\n\n"
        f"{body}"
    ).encode("utf-8")


def test_parse_prompt_document_returns_portable_metadata_and_digest():
    document = parse_prompt_document(
        PurePosixPath(".agents/prompts/pr-review.prompt.md"),
        prompt_bytes(),
    )

    assert document.name == "pr-review"
    assert document.description == "Review pull requests."
    assert document.argument_hint == "Optional review focus"
    assert document.body == "Review the pull request.\n"
    assert len(document.digest) == 64


@pytest.mark.parametrize(
    ("path", "payload", "code"),
    [
        (".agents/prompts/Bad.prompt.md", prompt_bytes("Bad"), "invalid-prompt-name"),
        (".agents/prompts/pr-review.md", prompt_bytes(), "invalid-prompt-location"),
        (".agents/prompts/pr-review.prompt.md", prompt_bytes("other"), "prompt-name-mismatch"),
        (".agents/prompts/pr-review.prompt.md", prompt_bytes(body="  \n"), "missing-prompt-body"),
        (".agents/prompts/pr-review.prompt.md", b"\xff\xfe", "invalid-prompt-encoding"),
    ],
)
def test_parse_prompt_document_rejects_noncanonical_source(path, payload, code):
    with pytest.raises(AssetValidationError) as excinfo:
        parse_prompt_document(path, payload)

    assert excinfo.value.issues[0].code == code
```

- [ ] **Step 2: Run the parser tests and verify the import fails**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_prompt_assets.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'agency.prompts'`.

- [ ] **Step 3: Implement the immutable prompt parser and task-input builder**

```python
# agency/prompts/assets.py
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re

import yaml

from agency.configuration.issues import ValidationIssue
from agency.fs.snapshot import AssetValidationError

PROMPT_PREFIX = PurePosixPath(".agents/prompts")
PROMPT_SUFFIX = ".prompt.md"
_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class PromptDocument:
    name: str
    description: str
    argument_hint: str | None
    body: str
    source: bytes
    digest: str


def prompt_source_path(name: str) -> PurePosixPath:
    return PROMPT_PREFIX / f"{name}{PROMPT_SUFFIX}"


def build_prompt_task_input(
    body: str,
    *,
    arguments: tuple[str, ...] = (),
    invocation_input: str = "",
) -> str:
    additions: list[str] = []
    if arguments:
        additions.append(yaml.safe_dump(list(arguments), sort_keys=False).strip())
    if invocation_input.strip():
        additions.append(invocation_input.strip())
    task = body.rstrip()
    return task if not additions else task + "\n\n## Invocation input\n\n" + "\n\n".join(additions)
```

Implement `parse_prompt_document()` with these exact checks in order: canonical path and suffix, 1-64-character slug, UTF-8 decoding, terminated YAML frontmatter, mapping metadata, allowed keys `{name, description, argument-hint}`, exact name match, nonblank description no longer than 1024 characters, string-or-null argument hint, and nonblank body. Construct every failure as one `ValidationIssue(scope="prompt", field=<source path>, ...)` inside `AssetValidationError`. Compute `digest` as `sha256(source).hexdigest()` and preserve the decoded body bytes apart from removing only the one separator newline after frontmatter.

- [ ] **Step 4: Add deterministic task-input tests**

```python
from agency.prompts.assets import build_prompt_task_input


def test_build_prompt_task_input_appends_one_deterministic_section():
    assert build_prompt_task_input(
        "Review now.\n",
        arguments=("security", "correctness"),
        invocation_input="Focus on PR 42.",
    ) == (
        "Review now.\n\n## Invocation input\n\n"
        "- security\n- correctness\n\nFocus on PR 42."
    )
```

- [ ] **Step 5: Extend blueprint inspection with prompt documents**

```python
# agency/blueprints/models.py
@dataclass(frozen=True)
class BlueprintInspection:
    key: str
    path: Path
    title: str
    skills: tuple[str, ...]
    prompts: tuple[PromptDocument, ...]
    snapshot: TreeSnapshot
```

In `inspect_blueprint()`, parse every file whose parent is exactly `.agents/prompts`, reject files or subdirectories under that prefix that do not match the canonical shape, sort documents by `name`, and pass the tuple to `BlueprintInspection`. Do not reject unrelated source outside `.agents/prompts`; the existing snapshot/digest behavior remains unchanged.

- [ ] **Step 6: Run focused source tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_prompt_assets.py tests/test_blueprint_library.py -v`

Expected: PASS, including a new assertion that editing prompt bytes changes `inspection.snapshot.digest`.

- [ ] **Step 7: Commit the prompt asset contract**

```bash
git add agency/prompts agency/blueprints/models.py agency/blueprints/library.py tests/test_prompt_assets.py tests/test_blueprint_library.py
git commit -m "feat(prompts): add canonical blueprint prompt assets"
```

### Task 2: Native Prompt Rendering And Shared Runtime Projection

**Files:**
- Create: `agency/prompts/projection.py`
- Modify: `agency/prompts/__init__.py`
- Modify: `agency/projector_capabilities.py:1-14`
- Modify: `agency/blueprints/projectors.py:1-138`
- Modify: `agency/integrations/__init__.py:260-295`
- Modify: `pyproject.toml:5-20`
- Modify: `tests/test_runtime_projectors.py:35-190`
- Modify: `tests/test_compilation_cache.py`
- Modify: `tests/test_cache_locking.py`
- Modify: `tests/test_job_submission.py:20-65`

**Interfaces:**
- Consumes: `PromptDocument` and `BlueprintInspection.prompts` from Task 1.
- Produces: `PromptProjectionFormat = Literal["prompt-markdown", "markdown-command", "gemini-toml"]`.
- Produces: `render_prompt(document: PromptDocument, *, target: PurePosixPath, format: PromptProjectionFormat) -> tuple[PurePosixPath, bytes]`.
- Produces: `StaticRuntimeProjector.project_prompt_documents(documents: Iterable[PromptDocument], destination: Path) -> tuple[PurePosixPath, ...]` for Task 5.
- Extends: `ProjectorCapabilities` with `prompts_target`, `prompt_format`, and explicit `discovers_prompts`.

- [ ] **Step 1: Add `tomli-w` as the structured TOML writer**

Add `"tomli-w>=1,<2"` to `project.dependencies`, then install the editable worktree environment:

Run: `..\..\.venv\Scripts\python.exe -m pip install -e .`

Expected: exit 0 and `python -c "import tomli_w"` succeeds.

- [ ] **Step 2: Write failing native-renderer tests**

```python
import tomllib

from agency.prompts.assets import parse_prompt_document
from agency.prompts.projection import render_prompt


def prompt_document():
    return parse_prompt_document(
        ".agents/prompts/pr-review.prompt.md",
        (
            "---\nname: pr-review\ndescription: Review pull requests.\n---\n\n"
            "Review the pull request.\n"
        ).encode("utf-8"),
    )


def test_gemini_renderer_uses_structured_toml():
    path, payload = render_prompt(
        prompt_document(),
        target=PurePosixPath(".gemini/commands"),
        format="gemini-toml",
    )

    assert path == PurePosixPath(".gemini/commands/pr-review.toml")
    assert tomllib.loads(payload.decode("utf-8")) == {
        "description": "Review pull requests.",
        "prompt": "Review the pull request.\n",
    }
```

Add parameterized assertions for Copilot `.github/prompts/pr-review.prompt.md` and Claude `.claude/commands/pr-review.md`, both preserving `PromptDocument.source` exactly.

- [ ] **Step 3: Run renderer tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_runtime_projectors.py -k "prompt" -v`

Expected: FAIL because prompt projection capability and renderer do not exist.

- [ ] **Step 4: Implement explicit projector prompt capability**

```python
# agency/projector_capabilities.py
from typing import Literal

PromptProjectionFormat = Literal[
    "prompt-markdown",
    "markdown-command",
    "gemini-toml",
]


@dataclass(frozen=True)
class ProjectorCapabilities:
    instruction_target: PurePosixPath
    skills_target: PurePosixPath
    prompts_target: PurePosixPath | None
    prompt_format: PromptProjectionFormat | None
    discovers_instructions: bool
    discovers_skills: bool
    discovers_prompts: bool
    activates_selected_skill: bool
```

Set Copilot to `.github/prompts` / `prompt-markdown` / discovered, Claude to `.claude/commands` / `markdown-command` / discovered, Gemini to `.gemini/commands` / `gemini-toml` / discovered, and every `_default_projector()` to `None` / `None` / false. Increment the three named projector versions from version 1 to version 2.

- [ ] **Step 5: Render prompts through the projector inventory**

Implement `render_prompt()` with exact filename rules and `tomli_w.dumps({"description": document.description, "prompt": document.body})`. Extend `_mapped_paths()` to parse canonical prompt files and merge generated outputs. Extend `validate_output()` inventory roots with `prompts_target` when non-null. Keep instruction and skill byte comparisons unchanged; compare prompt output to deterministic generated bytes.

- [ ] **Step 6: Update every `ProjectorCapabilities(...)` constructor**

Update constructors in `agency/integrations/__init__.py`, `tests/test_runtime_projectors.py`, `tests/test_compilation_cache.py`, `tests/test_cache_locking.py`, and `tests/test_job_submission.py` with explicit prompt fields. Do not add defaults that hide missing capability declarations.

- [ ] **Step 7: Run projector and cache tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_runtime_projectors.py tests/test_compilation_cache.py tests/test_cache_locking.py -v`

Expected: PASS; cache manifests list native prompt paths and source-only files remain absent.

- [ ] **Step 8: Commit native projection**

```bash
git add pyproject.toml agency/prompts agency/projector_capabilities.py agency/blueprints/projectors.py agency/integrations/__init__.py tests/test_runtime_projectors.py tests/test_compilation_cache.py tests/test_cache_locking.py tests/test_job_submission.py
git commit -m "feat(projectors): render portable prompt assets"
```

### Task 3: Safe Private Prompt Store

**Files:**
- Create: `agency/prompts/store.py`
- Create: `tests/test_prompt_store.py`
- Modify: `agency/prompts/__init__.py`

**Interfaces:**
- Consumes: `PromptDocument` and `parse_prompt_document()` from Task 1.
- Produces: `StoredPrompt(document: PromptDocument, path: Path)`.
- Produces: `PromptStore(root: Path)` with `path()`, `read()`, `create()`, `update()`, `delete()`, `copy_namespace()`, and `delete_namespace()`.
- Produces: `PromptConflictError` and `PromptNotFoundError` for service/web handling.

- [ ] **Step 1: Write failing path and CRUD tests**

```python
def private_prompt_bytes() -> bytes:
    return (
        "---\nname: local-triage\ndescription: Triage local work.\n---\n\n"
        "Review the private work queue.\n"
    ).encode("utf-8")


def test_prompt_store_round_trips_registered_source(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )

    assert created.path == (
        tmp_path / "prompts" / "newsletter" / "reviewer" / "local-triage.prompt.md"
    ).resolve()
    assert store.read("newsletter", "reviewer", "local-triage") == created


def test_prompt_store_rejects_stale_digest(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )

    with pytest.raises(PromptConflictError, match="changed; reload"):
        store.update(
            "newsletter",
            "reviewer",
            "local-triage",
            expected_digest="0" * 64,
            payload=private_prompt_bytes(),
        )
```

Add tests for invalid group/instance/prompt slugs, traversal, case-fold collisions, symlink/reparse roots and descendants, non-regular files, concurrent updates, namespace copy collision, rollback cleanup, and deletion with an expected digest.

- [ ] **Step 2: Run store tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_prompt_store.py -v`

Expected: FAIL during collection because `PromptStore` does not exist.

- [ ] **Step 3: Implement safe deterministic storage**

```python
@dataclass(frozen=True)
class StoredPrompt:
    document: PromptDocument
    path: Path


class PromptStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve(strict=False)

    def path(self, group: str, instance: str, name: str) -> Path:
        return self._contained(group, instance, f"{name}.prompt.md")

    def read(self, group: str, instance: str, name: str) -> StoredPrompt:
        path = self.path(group, instance, name)
        payload = self._read_regular_file(path)
        return StoredPrompt(parse_prompt_document(prompt_source_path(name), payload), path)

    def create(self, group: str, instance: str, name: str, payload: bytes) -> StoredPrompt:
        document = parse_prompt_document(f".agents/prompts/{name}.prompt.md", payload)
        return self._write(group, instance, name, document, require_absent=True, expected_digest=None)

    def update(self, group: str, instance: str, name: str, *, expected_digest: str, payload: bytes) -> StoredPrompt:
        document = parse_prompt_document(f".agents/prompts/{name}.prompt.md", payload)
        return self._write(group, instance, name, document, require_absent=False, expected_digest=expected_digest)
```

Use `exclusive_lock(<root>/.locks/<sha256 logical key>.lock, wait=True)` and `atomic_write_bytes()`. Validate every path component with `lstat`, reject reparse points before and after directory creation, resolve containment using normalized case on Windows, and never enumerate source files to create a catalog. `copy_namespace()` accepts an explicit tuple of registered names and expected digests; it stages/copies only those names and returns created target paths for caller rollback.

- [ ] **Step 4: Run private store tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_prompt_store.py -v`

Expected: PASS on Windows, including simulated reparse and concurrent-write tests.

- [ ] **Step 5: Commit the private source store**

```bash
git add agency/prompts tests/test_prompt_store.py
git commit -m "feat(prompts): add safe private prompt store"
```

### Task 4: Schema-V4 And Prompt-Backed Execution Cutover

This task is one atomic reviewer gate. Removing `Routine.skill` earlier would leave dispatch, CLI, web runs, and most fixtures broken; do not split or commit the intermediate red state.

**Files:**
- Create: `agency/prompts/catalog.py`
- Modify: `agency/prompts/__init__.py`
- Modify: `agency/configuration/models.py:10-175,688-1040`
- Modify: `agency/configuration/paths.py:1-385`
- Modify: `agency/configuration/patches.py:1-370`
- Modify: `agency/configuration/__init__.py`
- Modify: `agency/web/state.py`
- Modify: `agency/web/dependencies.py:20-90`
- Modify: `agency/jobs/models.py:10-260`
- Modify: `agency/jobs/prompts.py`
- Modify: `agency/jobs/resolution.py:1-205`
- Modify: `agency/jobs/submission.py:1-135`
- Modify: `agency/jobs/__init__.py`
- Modify: `agency/dispatch/run.py:1-155`
- Modify: `agency/cli.py:320-550,720-785`
- Modify: `agency/app.py:35-50,1605-1670`
- Modify: `agency/app.py:1480-1520`
- Modify: `agency/web/setup_flow.py:15-65`
- Modify: `skills/agency-setup/SKILL.md`
- Modify: `skills/agency-setup/references/templates.md`
- Modify: `config.yaml.example`
- Test: `tests/test_config.py`
- Test: `tests/test_config_normalization.py`
- Test: `tests/test_path_validation.py`
- Test: `tests/test_job_models.py`
- Test: `tests/test_job_authority.py`
- Test: `tests/test_job_submission.py`
- Test: `tests/test_dispatch_run.py`
- Test: `tests/test_agent_run.py`
- Test: `tests/test_cli_contract.py`
- Test: `tests/test_agency_setup_skill.py`
- Test: `tests/test_setup_flow.py`
- Test: `tests/test_surface_contracts.py`
- Test: `tests/conftest.py`
- Mechanically update current config fixtures in: `tests/test_admin_agent_create.py`, `tests/test_admin_dispatch_xss.py`, `tests/test_admin_dispatch.py`, `tests/test_admin_org_sandbox.py`, `tests/test_agent_detail.py`, `tests/test_agent_library_routes.py`, `tests/test_agent_roster.py`, `tests/test_cli.py`, `tests/test_config_store.py`, `tests/test_dashboard.py`, `tests/test_decision_verify.py`, `tests/test_execute_decision.py`, `tests/test_group_settings.py`, `tests/test_instances.py`, `tests/test_job_detached_process.py`, `tests/test_job_routes.py`, `tests/test_job_systemd_integration.py`, `tests/test_logs.py`, `tests/test_memory_channel_routes.py`, `tests/test_proposal_questions.py`, `tests/test_server.py`, `tests/test_surface_contracts.py`, and `tests/test_workspaces.py`.

**Interfaces:**
- Consumes: `PromptDocument`, `PromptStore`, and projector output from Tasks 1-3.
- Produces: config `PromptSelector(scope: Literal["blueprint", "instance"], name: str)`.
- Produces: `CatalogPrompt(scope, document, source_path)` and `effective_prompt_catalog()`, `resolve_catalog_prompt()`, `validate_prompt_catalogs()`.
- Changes: config `CONFIG_SCHEMA_VERSION = 4`, required `AgencySettings.prompt_store`, `AgentInstance.prompts`, and `Routine.prompt`.
- Changes: durable job `SCHEMA_VERSION = 4`, `SUPPORTED_SCHEMA_VERSIONS = {3, 4}`, v3/v4 validation, and `private_prompts: tuple[PromptSnapshot, ...]`.
- Changes: `JobRequest` accepts `task_input`, `prompt`, and `invocation_input` as mutually constrained inputs.
- Changes: `resolve_job_request(..., prompt_store: PromptStore, ...) -> JobSpec`.

- [ ] **Step 1: Write failing schema-v4 tests**

```python
def test_schema_four_requires_prompt_store_and_scoped_routine(raw_config, config_paths):
    raw_config["schema_version"] = 4
    raw_config["agency"]["prompt_store"] = str(config_paths["prompt_store"])
    raw_config["groups"]["newsletter"]["agents"][0]["routines"][0] = {
        "id": "daily-review",
        "prompt": {"scope": "blueprint", "name": "daily-review"},
        "schedule": {"at": "09:00"},
        "memory": {"scope": "routine"},
    }

    parsed = parse_config(raw_config, config_paths["config_path"])

    routine = parsed.resolved.groups["newsletter"].agents["builder"].routines[0]
    assert routine.prompt == PromptSelector(scope="blueprint", name="daily-review")
    assert not hasattr(routine, "skill")


def test_schema_three_is_rejected_with_fresh_v4_hint(raw_config, config_paths):
    raw_config["schema_version"] = 3

    with pytest.raises(ValidationFailed) as excinfo:
        parse_config(raw_config, config_paths["config_path"])

    assert excinfo.value.issues[0].code == "unsupported-schema-version"
    assert "schema_version: 4" in excinfo.value.issues[0].corrective_hint
```

- [ ] **Step 2: Implement config schema v4 and prompt-store authority**

```python
PromptScope = Literal["blueprint", "instance"]
CONFIG_SCHEMA_VERSION = 4


class PromptSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: PromptScope
    name: str


class Routine(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    id: str
    prompt: PromptSelector
    arguments: tuple[str, ...] = ()
    schedule: ScheduleRule
    memory: MemorySelector | None = None
    enabled: bool = True
```

Add `prompt_store: Path | None` to `AgencySettings`, `prompts: tuple[str, ...] = ()` to `AgentInstance`, and `schema_version: Literal[4]` to `AgencyConfig`. Validate prompt/private registration identifiers and duplicate registrations in `_validate_raw_config()`. Resolve `agency.prompt_store` in `_prepare_for_model()`. Add prompt store to every required-path loop, overlap authority set, sandbox-overlap check, and `initialize_storage_directories()`.

Add `prompt_store: str` to `AgencySettingsPatch`; preserve it in
`admin_save_settings()` and expose it from `agency_settings()` in
`agency/web/state.py`. This admin POST does not let the form silently erase the
new required path.

- [ ] **Step 3: Add prompt store to shared test fixtures**

In `tests/conftest.py`, add `prompt_store`, set `schema_version` to 4, add the agency path, replace routine `skill` with a scoped prompt selector, and create `.agents/prompts/daily-review.prompt.md` in test blueprint helpers. Apply that exact shape to every current config fixture listed under **Files**. Do not alter historical docs under `docs/superpowers/`.

Update `config.yaml.example`, `build_setup_prompt()`, the canonical
`skills/agency-setup` skill/template, and their three contract tests in this
same cutover. They must derive five storage paths, author task prompts separately
from optional cross-task skills, write scoped routine selectors, and require
schema v4. Do not edit `.github/skills/agency-setup` separately because it is a
symlink to the canonical skill.

- [ ] **Step 4: Write failing catalog-resolution tests**

```python
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agency.blueprints.library import BlueprintLibrary
from agency.configuration.store import ConfigStore
from agency.prompts.store import PromptStore


@pytest.fixture
def prompt_env(tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = Path(raw["agency"]["agent_library"])
    blueprint = library_root / "reviewer"
    prompt_dir = blueprint / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Reviewer\n", encoding="utf-8")
    (prompt_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Review PRs.\n---\n\nReview pull requests.\n",
        encoding="utf-8",
    )
    agent = raw["groups"]["newsletter"]["agents"][0]
    agent["name"] = "reviewer"
    agent["blueprint"] = "reviewer"
    agent["prompts"] = ["local-triage"]
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    store = PromptStore(Path(raw["agency"]["prompt_store"]))
    store.create(
        "newsletter",
        "reviewer",
        "local-triage",
        (
            "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
            "Review local work.\n"
        ).encode("utf-8"),
    )
    snapshot = ConfigStore(config_path).load()
    return SimpleNamespace(
        snapshot=snapshot,
        library=BlueprintLibrary(library_root),
        store=store,
    )


def test_effective_catalog_contains_shared_and_only_registered_private_prompts(prompt_env):
    catalog = effective_prompt_catalog(
        prompt_env.snapshot,
        prompt_env.library,
        prompt_env.store,
        "newsletter",
        "reviewer",
    )

    assert [(item.scope, item.document.name) for item in catalog] == [
        ("blueprint", "pr-review"),
        ("instance", "local-triage"),
    ]
    assert "unregistered" not in {item.document.name for item in catalog}
```

Add tests for missing registered files, unknown routine targets, shared/private collisions, explicit scope, and no directory discovery.

- [ ] **Step 5: Implement catalog and service construction**

```python
@dataclass(frozen=True)
class CatalogPrompt:
    scope: Literal["blueprint", "instance"]
    document: PromptDocument
    source_path: str


def effective_prompt_catalog(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    store: PromptStore,
    group_id: str,
    agent_id: str,
) -> tuple[CatalogPrompt, ...]:
    group = snapshot.config.groups[group_id]
    instance = group.agents[agent_id]
    inspection = library.inspect(instance.blueprint)
    shared = tuple(
        CatalogPrompt("blueprint", document, prompt_source_path(document.name).as_posix())
        for document in inspection.prompts
    )
    private = tuple(
        CatalogPrompt("instance", store.read(group_id, agent_id, name).document, str(store.path(group_id, agent_id, name)))
        for name in instance.prompts
    )
    return _validate_effective_catalog(shared + private, group_id, agent_id)
```

Add `prompt_store: PromptStore | None` to `AgencyServices`; construct it from the required path, call `validate_prompt_catalogs()` during `build_services()`, and pass it to job resolution. Update the two manual `AgencyServices(...)` constructors in `agency/cli.py`.

- [ ] **Step 6: Write durable job v3/v4 compatibility tests**

```python
def test_job_spec_reads_v3_routine_record_and_writes_v4_prompt_record(tmp_path):
    current = make_spec(tmp_path)
    current_data = current.to_dict()
    historical_data = dict(current_data)
    historical_data.update(
        schema_version=3,
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=["--brief"],
        prompt_source={"type": "routine", "routine_id": "daily-review"},
    )
    historical_data.pop("private_prompts", None)

    historical = JobSpec.from_dict(historical_data)
    reloaded_current = JobSpec.from_dict(current_data)

    assert historical.schema_version == 3
    assert historical.skill == "daily-review"
    assert reloaded_current.schema_version == 4
    assert reloaded_current.skill is None
    assert reloaded_current.prompt_source["type"] == "blueprint_prompt"
```

Update `tests/test_job_models.py::make_spec()` to construct a valid v4 manual
blueprint-prompt job by default, including `private_prompts=()`. Add v4
validation tests: scheduled jobs require routine plus saved prompt source;
manual jobs accept saved prompt or `ad_hoc`; decision jobs reject
routine/prompt selection; new jobs keep `skill is None` and
`skill_arguments == ()`; v3 authority digests remain unchanged when read.

- [ ] **Step 7: Implement job request, schema, and prompt resolution**

```python
@dataclass(frozen=True)
class PromptSnapshot:
    name: str
    content: str
    source_digest: str


@dataclass(frozen=True)
class JobRequest:
    config_path: Path
    group_key: str
    agent_name: str
    trigger: str
    task_input: str = ""
    prompt: PromptSelector | None = None
    invocation_input: str = ""
    job_id: str = field(default_factory=lambda: uuid4().hex)
    routine_id: str | None = None
    memory_override: Any | None = None
    timeout_override: int | None = None
    trigger_context: dict[str, Any] | None = None
```

Set job `SCHEMA_VERSION = 4` and `SUPPORTED_SCHEMA_VERSIONS = frozenset({3, 4})`. Add `private_prompts: tuple[PromptSnapshot, ...] = ()` at the end of `JobSpec`, serialize it, and reconstruct it in `from_dict()`. Keep v3 validation in a dedicated `_validate_v3_prompt_contract()` and add `_validate_v4_prompt_contract()`; never interpret a config-v3 document.

`JobSpec.to_dict()` must add `private_prompts` only when
`schema_version >= 4`. Omitting that key for v3 is required so
`immutable_digest()` reproduces the original authority digest of persisted v3
records. `from_dict()` treats the absent key as an empty tuple. Add a regression
that loads a complete v3 `JobRecord` with its precomputed digest and verifies
round-trip `to_dict()` equality before accepting this task.

In `resolve_job_request()`:

1. Resolve/validate a routine for scheduled jobs or optional manual routine runs.
2. Use `routine.prompt` for routine runs, `request.prompt` for saved manual runs, or nonblank `request.task_input` for ad hoc/decision runs.
3. Build task input from canonical body plus routine arguments/manual invocation input.
4. Build exact shared/private/ad-hoc provenance.
5. Snapshot every registered private prompt's canonical UTF-8 source and digest into `private_prompts`.
6. Pass `skill=None` and `skill_arguments=()` to new integration validation/specs.
7. Preserve routine/default memory selection when a routine exists and agent/default memory otherwise.

- [ ] **Step 8: Cut dispatch, CLI, and web run submission to the new request contract**

Scheduled dispatch submits only `routine_id`; resolution loads the routine prompt. `cmd_agent_run --routine` continues to run an existing routine early but stops constructing task text itself. `cmd_agent_show` reports `{"scope": routine.prompt.scope, "name": routine.prompt.name}` instead of skill.

Change `POST /{group}/agents/{agent}/run` to accept exactly one of:

```text
mode=saved, prompt_scope=blueprint|instance, prompt_name=<slug>, invocation_input=<optional>
mode=one-off, task_input=<required>
```

Accept optional `memory_scope` plus `memory_channel`; require a declared channel
when scope is `channel`, reject `memory_channel` for every other scope, and reject
`routine` scope when no routine is selected. Return
`202 {"status":"started","job_id":"..."}`. Reject both/neither mode inputs
with 400 and unknown scoped prompts with 404.

- [ ] **Step 9: Run the atomic cutover suite**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_config_normalization.py tests/test_path_validation.py tests/test_job_models.py tests/test_job_authority.py tests/test_job_submission.py tests/test_dispatch_run.py tests/test_agent_run.py tests/test_cli_contract.py tests/test_agency_setup_skill.py tests/test_setup_flow.py tests/test_surface_contracts.py -v`

Expected: PASS.

Run: `rg -n "schema_version:\s*3|\"schema_version\"\s*:\s*3|routine\.skill|\"skill\"\s*:" agency tests --glob "!tests/test_job_models.py" --glob "!tests/test_job_authority.py"`

Expected: no config/routine matches; any remaining `skill` matches belong only to integration runtime capability tests, not `Routine`.

- [ ] **Step 10: Run the complete Python suite before committing the cutover**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/ -q`

Expected: PASS. Fix every current config fixture before committing; do not defer schema fallout to later tasks.

- [ ] **Step 11: Commit the schema/execution cutover**

```bash
git add agency tests skills/agency-setup config.yaml.example
git commit -m "feat(config): adopt prompt-backed schema v4"
```

### Task 5: Immutable Private Prompt Launch Overlays

**Files:**
- Modify: `agency/prompts/projection.py`
- Modify: `agency/jobs/execution.py:300-420`
- Modify: `tests/test_job_execution.py`
- Modify: `tests/test_runtime_projectors.py`

**Interfaces:**
- Consumes: `JobSpec.private_prompts` and `StaticRuntimeProjector.project_prompt_documents()`.
- Produces: `project_prompt_snapshots(projector: RuntimeProjector, snapshots: tuple[PromptSnapshot, ...], destination: Path) -> tuple[PurePosixPath, ...]`.
- Preserves: v3 jobs with no private snapshots and historical selected skills.

- [ ] **Step 1: Write failing overlay-isolation tests**

```python
def test_worker_projects_private_prompt_snapshot_without_rereading_source(
    tmp_path, monkeypatch
):
    path, spec = queued_job(
        tmp_path,
        private_prompt_content="Original private task.\n",
    )
    decoy = tmp_path / "prompt-store" / "test" / "product" / "local-triage.prompt.md"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("Changed after submission.\n", encoding="utf-8")

    class Integration:
        projector = get_projector("copilot")

        def run(self, request: IntegrationRunRequest):
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=Path(spec.workspace_root),
        group_root=Path(spec.group_root),
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: context,
    )

    record = execute_job(_authority(spec))
    projected = path.with_suffix("") / "launch" / ".github" / "prompts" / "local-triage.prompt.md"

    assert record.status == "complete"
    assert b"Original private task." in projected.read_bytes()
    assert b"Changed after submission." not in projected.read_bytes()
```

Extend the existing `queued_job()` helper with an optional
`private_prompt_content: str | None` parameter. When present, create a v4
`PromptSnapshot` whose `content` is complete canonical Markdown and whose digest
is the SHA-256 of those UTF-8 bytes. Add tests that the cache artifact remains
byte-identical, another instance's snapshots are absent, shared/private
collisions fail before execution, v3 jobs still pass `spec.skill`, and v4 jobs
pass `skill=None`.

- [ ] **Step 2: Run focused execution tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_job_execution.py -k "private_prompt or historical_skill" -v`

Expected: FAIL because private snapshots are not projected.

- [ ] **Step 3: Project snapshots after copying the shared launch view**

```python
if launch_view is None:
    launch_view = create_launch_view(artifact, launch_dir)
if spec.private_prompts:
    project_prompt_snapshots(
        integration.projector,
        spec.private_prompts,
        launch_view,
    )
```

Reparse each snapshot's canonical content with `parse_prompt_document()`, verify its SHA-256 equals `source_digest`, render through the integration projector, and reject an existing target path rather than overwriting shared output. Do not instantiate `PromptStore` in the worker.

- [ ] **Step 4: Run execution/projector tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_job_execution.py tests/test_runtime_projectors.py tests/test_job_authority.py -v`

Expected: PASS.

- [ ] **Step 5: Commit immutable private overlays**

```bash
git add agency/prompts/projection.py agency/jobs/execution.py tests/test_job_execution.py tests/test_runtime_projectors.py
git commit -m "feat(jobs): project immutable private prompts"
```

### Task 6: Private Prompt CRUD And Instance Lifecycle

**Files:**
- Create: `agency/prompts/service.py`
- Create: `tests/test_prompt_service.py`
- Modify: `agency/prompts/__init__.py`
- Modify: `agency/configuration/patches.py:85-370`
- Modify: `agency/configuration/__init__.py`
- Modify: `agency/instances.py:1-580`
- Modify: `agency/web/dependencies.py:20-90`
- Modify: `agency/web/routes/agents.py:210-235`
- Modify: `tests/test_instances.py`
- Modify: `tests/test_agent_roster.py`

**Interfaces:**
- Produces: `register_agent_prompt()` and `unregister_agent_prompt()` config patches.
- Produces: `PromptMutationResult(snapshot: ConfigSnapshot, document: PromptDocument, orphaned_path: Path | None = None)`.
- Produces: `PromptService.create_private()`, `update_private()`, `delete_private()`, and `catalog()`.
- Changes: `AgencyServices.prompt_service: PromptService | None`; successful service construction wires it from the same `ConfigStore`, `BlueprintLibrary`, and `PromptStore` instances.
- Changes: `InstanceService(..., prompt_store: PromptStore)` and move/remove result metadata.

- [ ] **Step 1: Write failing service transaction tests**

```python
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agency.blueprints.library import BlueprintLibrary
from agency.configuration import ConfigStore, ValidationFailed
from agency.prompts import PromptService, PromptStore


@pytest.fixture
def prompt_service_env(tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = Path(raw["agency"]["agent_library"])
    blueprint = library_root / "reviewer"
    blueprint.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Reviewer\n", encoding="utf-8")
    agent = raw["groups"]["newsletter"]["agents"][0]
    agent["name"] = "reviewer"
    agent["blueprint"] = "reviewer"
    agent["prompts"] = []
    agent["routines"] = []
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    config_store = ConfigStore(config_path)
    store = PromptStore(Path(raw["agency"]["prompt_store"]))
    service = PromptService(
        config_store=config_store,
        library=BlueprintLibrary(library_root),
        store=store,
    )
    return SimpleNamespace(config_store=config_store, store=store, service=service)


def local_triage_source() -> bytes:
    return (
        "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
        "Review local work.\n"
    ).encode("utf-8")


def test_create_private_publishes_then_registers(prompt_service_env):
    snapshot = prompt_service_env.config_store.load()
    result = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=snapshot.revision,
    )

    assert result.document.name == "local-triage"
    assert "local-triage" in result.snapshot.config.groups["newsletter"].agents["reviewer"].prompts


def test_delete_private_rejects_referenced_routine(prompt_service_env):
    created = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=prompt_service_env.config_store.load().revision,
    )
    prompt_service_env.config_store.patch(
        created.snapshot.revision,
        lambda raw: raw["groups"]["newsletter"]["agents"][0].update(
            routines=[
                {
                    "id": "morning-triage",
                    "prompt": {"scope": "instance", "name": "local-triage"},
                    "schedule": {"at": "09:00"},
                }
            ]
        ),
    )
    with pytest.raises(ValidationFailed) as excinfo:
        prompt_service_env.service.delete_private(
            "newsletter",
            "reviewer",
            "local-triage",
            expected_revision=prompt_service_env.config_store.load().revision,
            expected_digest=prompt_service_env.store.read(
                "newsletter", "reviewer", "local-triage"
            ).document.digest,
        )

    assert excinfo.value.issues[0].code == "prompt-in-use"
```

Add tests for config conflict cleanup, stale edit digest, unregistered-file cleanup guard, delete orphan reporting, and shared prompt delete usage lookup.

- [ ] **Step 2: Implement config registration patches and `PromptService`**

```python
def register_agent_prompt(store, expected_revision, group_id, agent_id, prompt_name):
    def apply(raw):
        agent = _agent(_group(raw, group_id), agent_id)
        prompts = agent.setdefault("prompts", [])
        if prompt_name in prompts:
            raise ValueError(f"Prompt already registered: {prompt_name}")
        prompts.append(prompt_name)
    return store.patch(expected_revision, apply)
```

`create_private()` validates/publishes the file, then registers it; if registration fails, remove the new file only while holding its lock and only when its digest still matches. `update_private()` changes source under expected digest without rewriting config. `delete_private()` scans only configured routines for exact `{scope: instance, name}` references, unregisters under expected revision, then removes the matching file; return an orphan path when cleanup fails.

- [ ] **Step 3: Write failing move/remove namespace tests**

Extend `tests/test_instances.py` so move copies only registered names, rejects a target namespace, rolls copied targets back on config failure, removes source only after success, and removal reports/deletes the private namespace while leaving semantic memory behavior unchanged.

- [ ] **Step 4: Integrate prompt namespaces with `InstanceService`**

Add prompt names/digests to `MovePreview`, accept `PromptStore` in `InstanceService`, and acquire prompt namespace locks in deterministic normalized-path order inside the existing group-operation `ExitStack`. Copy registered source prompts before `_apply_move_patch()`, clean target copies on patch failure, and remove source after a successful config move. Extend `RemoveInstanceResult` with `orphaned_prompt_namespace: Path | None`.

Update `agent_remove()` to inspect `RemoveInstanceResult`. Redirect normally when
prompt cleanup succeeds; when config removal succeeds but source cleanup leaves
an orphan, render the updated roster with status 409 and a warning naming the
orphan path. Add a route regression proving the removed instance stays removed
while the cleanup warning remains visible.

- [ ] **Step 5: Run service and lifecycle tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_prompt_service.py tests/test_instances.py tests/test_config_patches.py tests/test_agent_roster.py -v`

Expected: PASS, including existing memory move/remove tests.

- [ ] **Step 6: Commit prompt CRUD/lifecycle**

```bash
git add agency/prompts agency/configuration agency/instances.py agency/web/dependencies.py agency/web/routes/agents.py tests/test_prompt_service.py tests/test_instances.py tests/test_config_patches.py tests/test_agent_roster.py
git commit -m "feat(prompts): manage private prompt lifecycle"
```

### Task 7: Shared Prompt Authoring In Agent Library

**Files:**
- Create: `agency/templates/admin_blueprint_prompts.html`
- Modify: `agency/templates/admin_agent_library.html`
- Modify: `agency/templates/admin_blueprint_detail.html`
- Modify: `agency/web/routes/admin_library.py:1-700`
- Modify: `tests/test_agent_library_routes.py`

**Interfaces:**
- Consumes: `BlueprintInspection.prompts`, canonical parser, and existing blueprint lock/staging helpers.
- Produces: `GET /admin/agent-library/blueprints/{key}/prompts`.
- Produces: `POST /admin/agent-library/blueprints/{key}/prompts/{prompt}/delete`.
- Extends: existing source-save route to exact prompt paths.

- [ ] **Step 1: Write failing shared prompt route tests**

```python
def test_blueprint_prompts_page_lists_and_edits_canonical_source(
    monkeypatch, tmp_path, raw_config
):
    client, _, _, _ = _seed_library_app(monkeypatch, tmp_path, raw_config)
    response = client.get("/admin/agent-library/blueprints/advisor/prompts")

    assert response.status_code == 200
    assert "pr-review.prompt.md" in response.text
    assert ".agents/prompts/pr-review.prompt.md" in response.text


def test_blueprint_prompt_delete_blocks_configured_routine(
    monkeypatch, tmp_path, raw_config
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch, tmp_path, raw_config
    )
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest
    response = client.post(
        "/admin/agent-library/blueprints/advisor/prompts/pr-review/delete",
        data={"expected_digest": digest},
    )

    assert response.status_code == 409
    assert "prompt is used by" in response.text.lower()
    assert (library_root / "advisor" / ".agents" / "prompts" / "pr-review.prompt.md").is_file()
```

Add tests for create, edit, stale digest, malformed source, invalid path, path traversal, unreferenced delete, and full-tree rollback.

Update this test module's `_write_blueprint()` helper to create
`.agents/prompts/pr-review.prompt.md`, and replace its routine `skill` field with
`prompt: {scope: blueprint, name: pr-review}` before running these tests.

- [ ] **Step 2: Run route tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_library_routes.py -k "prompt" -v`

Expected: FAIL with 404 for the prompts route.

- [ ] **Step 3: Extend safe source staging and routing**

Allow only `AGENTS.md`, `.agents/skills/<slug>/...`, or exact `.agents/prompts/<slug>.prompt.md`. Add prompt-specific render/error branches and redirect saved prompt source back to the prompts view. Add a staged-delete helper that copies every snapshot file except the exact target, validates the staged blueprint, then publishes through `_publish_stage()` under the existing blueprint lock.

- [ ] **Step 4: Build the shared prompt editor**

The template contains a prompt list, raw canonical Markdown editor, Add prompt form with slug and initial canonical content, source digest, validation issues, and confirmed delete control. Blueprint cards/detail show prompt names separately from skills. Compatibility rows add prompt target and discovery columns.

- [ ] **Step 5: Run Agent Library tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_library_routes.py tests/test_blueprint_library.py -v`

Expected: PASS.

- [ ] **Step 6: Commit shared prompt authoring**

```bash
git add agency/web/routes/admin_library.py agency/templates/admin_agent_library.html agency/templates/admin_blueprint_detail.html agency/templates/admin_blueprint_prompts.html tests/test_agent_library_routes.py
git commit -m "feat(library): author shared blueprint prompts"
```

### Task 8: Agent Detail Private Prompts And Scoped Routines

**Files:**
- Create: `agency/templates/agent_detail_prompts.html`
- Modify: `agency/templates/agent_detail.html`
- Modify: `agency/templates/agent_detail_routines.html`
- Modify: `agency/web/routes/agent_detail.py:20-780`
- Modify: `tests/test_agent_detail.py`

**Interfaces:**
- Consumes: `PromptService`, `effective_prompt_catalog()`, and schema-v4 `PromptSelector`.
- Produces: Agent Detail `Prompts` tab plus create/update/delete POST actions.
- Changes: `_parse_routines_payload(form, available_prompts)` validates explicit scope/name instead of skill.

- [ ] **Step 1: Write failing Prompts-tab tests**

```python
def test_agent_prompts_tab_separates_shared_and_private(
    monkeypatch, tmp_path, raw_config
):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    response = client.get("/newsletter/agents/advisor/prompts")

    assert response.status_code == 200
    assert "Shared from blueprint" in response.text
    assert "Private to this instance" in response.text
    assert "pr-review" in response.text
    assert "local-triage" in response.text
```

Add route tests for private create/edit/delete, expected revision/digest conflicts, retained form text, shared read-only links, referenced-delete errors, and unknown agent/prompt responses.

Update `_seed_app()` to create `pr-review.prompt.md`, create the configured
prompt-store root, publish/register `local-triage.prompt.md`, and use scoped
routine selectors so every Prompts/Routines test starts from a valid catalog.

- [ ] **Step 2: Add the tab and prompt mutation routes**

Add `"prompts": "Prompts"` to `_TAB_LABELS`, `_prompts_context()`, GET `/prompts`, and explicit POST `/prompts/create`, `/prompts/{name}/save`, and `/prompts/{name}/delete` handlers. Route every mutation through `PromptService`; render `ValidationFailed`, `ConfigConflictError`, and `PromptConflictError` as 409 with field-specific issues.

- [ ] **Step 3: Replace skill-based routine parsing tests**

```python
def test_routine_editor_accepts_explicit_blueprint_and_instance_prompts(
    monkeypatch, tmp_path, raw_config
):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={
            "revision": ConfigStore(config_path).load().revision,
            "routines_json": yaml.safe_dump([
                {
                    "id": "morning-review",
                    "prompt": {"scope": "blueprint", "name": "pr-review"},
                    "schedule": {"at": "09:00"},
                },
                {
                    "id": "local-review",
                    "prompt": {"scope": "instance", "name": "local-triage"},
                    "schedule": {"every": "6h"},
                },
            ]),
        },
    )

    assert response.status_code == 200
```

Test unknown scope/name, shorthand strings, collisions, arguments, enabled, schedule, and memory behavior.

- [ ] **Step 4: Implement scoped routine editor context and validation**

Pass `available_prompts: frozenset[tuple[str, str]]` into `_parse_routines_payload()`. Require a mapping with exact `scope`/`name`, validate membership, and emit the same object in the returned routine payload. Update the Routines template to list shared and private prompt chips and describe the prompt/schedule relationship; remove skill-choice wording.

- [ ] **Step 5: Run Agent Detail tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_detail.py -v`

Expected: PASS across Profile, Blueprint, Runtime, Prompts, Routines, Memory, and Activity.

- [ ] **Step 6: Commit Agent Detail prompt ownership**

```bash
git add agency/web/routes/agent_detail.py agency/templates/agent_detail.html agency/templates/agent_detail_prompts.html agency/templates/agent_detail_routines.html tests/test_agent_detail.py
git commit -m "feat(agents): manage private prompts and routines"
```

### Task 9: Operations-First Roster And Manual Launchers

**Files:**
- Modify: `agency/web/routes/agents.py:80-310`
- Modify: `agency/templates/agents.html`
- Modify: `tests/test_agent_roster.py`
- Modify: `tests/test_agent_run.py`

**Interfaces:**
- Consumes: effective prompt catalogs and the Task 4 run endpoint.
- Produces: `instances[*].prompts`, `active_jobs`, `active_job_count`, and launch form state.
- Changes: `_render_roster(..., creation_open=False, creation_values=None, creation_issues=None)`.

- [ ] **Step 1: Write failing roster behavior tests**

```python
def test_roster_hides_creation_form_until_add_agent_dialog(
    monkeypatch, tmp_path, raw_config
):
    client, _, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    response = client.get("/newsletter/agents")

    assert response.status_code == 200
    assert 'data-action="open-add-agent"' in response.text
    assert '<dialog id="add-agent-dialog"' in response.text
    assert '<dialog id="add-agent-dialog" open' not in response.text


def test_roster_renders_expanded_saved_and_one_off_launcher(
    monkeypatch, tmp_path, raw_config
):
    client, _, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    response = client.get("/newsletter/agents")

    assert "Saved prompt" in response.text
    assert "One-off" in response.text
    assert "Shared from blueprint" in response.text
    assert "Private to this instance" in response.text
    assert "Run prompt" in response.text
    assert "Run one-off" in response.text
```

Add tests for no-prompt default, prompt descriptions, memory selectors, multiple active-job count/links, concurrent controls remaining enabled, and creation-error dialog reopening with values.

Update this module's `_write_blueprint()` and `_seed_app()` helpers with one
shared prompt and one registered private prompt. Keep the builder instance's
catalog empty in the dedicated no-prompt test.

- [ ] **Step 2: Extend roster rows with catalog and active jobs**

Build one effective catalog per instance, serialize options as `{scope, name, description, argument_hint}`, and retain all sorted active jobs rather than only the newest. Provide the count plus latest links/status classes. An invalid catalog fails the roster with an actionable 409 warning rather than hiding source errors.

- [ ] **Step 3: Replace the always-visible creation panel with a dialog**

Move the existing fields unchanged into `<dialog id="add-agent-dialog">`; add an **Add agent** header button, close button, Escape/backdrop handling, and focus return. On server-side create failure, call `_render_roster()` with `creation_open=True`, entered values, and issues. Keep POST plus 303 success.

- [ ] **Step 4: Implement the fully expanded card launcher**

Each card contains fixed Saved prompt/One-off segmented controls, grouped `<optgroup>` options, description text, optional invocation input, one-off textarea, memory selector for run/agent/group/declared-channel scopes, and a dedicated submit button. Include `memory_channel` when a channel is selected. Use per-card JavaScript to post `FormData` to the existing run endpoint, preserve input on errors, append a linked queued badge from returned `job_id`, increment active count, and never disable launch controls merely because jobs are active.

- [ ] **Step 5: Run route and roster tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agent_roster.py tests/test_agent_run.py -v`

Expected: PASS, including saved shared/private and one-off requests.

- [ ] **Step 6: Commit the roster UX**

```bash
git add agency/web/routes/agents.py agency/templates/agents.html tests/test_agent_roster.py tests/test_agent_run.py
git commit -m "feat(agents): restore prompt launch controls"
```

### Task 10: Current Documentation And Browser Release Gate

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `kb/configuration.md`
- Modify: `kb/directory-structure.md`
- Modify: `kb/integrations.md`
- Modify: `kb/contributing-integrations.md`
- Modify: `kb/dispatch.md`
- Modify: `kb/getting-started.md`
- Modify: `kb/setup-skill.md`
- Modify: `kb/agent-identity.md`
- Modify: `examples/code-review-team/README.md`
- Modify: `examples/content-team/README.md`
- Modify: `tests/ui/fixtures/config.yaml`
- Modify: `tests/ui/server.py`
- Modify: `tests/ui/accessibility.spec.ts`
- Modify: `tests/ui/agent_configuration.spec.ts`
- Update: `tests/ui/agent_configuration.spec.ts-snapshots/agent-roster-*.png`
- Add snapshots for: Agent Prompts tab and shared Prompt Library view across four Playwright projects.

**Interfaces:**
- Consumes: all completed schema, authoring, projection, and roster behavior.
- Produces: current user/contributor documentation for the schema-v4 prompt authority model.
- Produces: executable desktop/mobile light/dark visual and accessibility gates.

- [ ] **Step 1: Update current product docs and examples**

Replace current schema-v3 claims with schema v4, add the prompt store authority and layouts, explain scoped prompt-backed routines/manual launches, and state that native files are generated output. Keep historical files in `docs/superpowers/specs` and `docs/superpowers/plans` unchanged. Do not edit ignored runtime `config.yaml`.

- [ ] **Step 2: Seed deterministic UI prompt assets**

Update the UI fixture to schema v4 with `prompt_store`, shared prompt files, one registered private prompt, and scoped routines. Update `tests/ui/server.py` to substitute/create the new store and emit durable job v4 fixtures while retaining one explicit v3 history test outside UI.

- [ ] **Step 3: Extend Playwright interaction/accessibility coverage**

Add Prompts to the tab semantics test and accessibility page list. Exercise Add agent open/close/focus, Saved prompt/One-off switching, grouped prompt selection, and layout stability. Assert no clipping/overlap with `assertNoLayoutIssues(page)` before screenshots.

- [ ] **Step 4: Run current documentation contract tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_agency_setup_skill.py tests/test_setup_flow.py tests/test_surface_contracts.py -v`

Expected: PASS.

- [ ] **Step 5: Install UI dependencies and update intentional snapshots**

Run: `npm install`

If `.venv\Scripts\python.exe` is absent in the worktree, create a worktree-local environment and install the package:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Run: `npm run test:ui:update -- agent_configuration.spec.ts`

Expected: four updated roster snapshots plus new Prompt Library/Agent Prompts snapshots with no test failures.

- [ ] **Step 6: Run the full browser gate without snapshot updates**

Run: `npm run test:ui`

Expected: PASS in `desktop-light`, `desktop-dark`, `mobile-light`, and `mobile-dark`; no axe violations, clipping, overlaps, blank pages, or console errors.

- [ ] **Step 7: Commit docs and UI verification**

```bash
git add CLAUDE.md README.md kb examples tests/ui
git commit -m "docs(prompts): document and verify prompt workflows"
```

### Task 11: Whole-Branch Review And Final Verification

**Files:**
- Review: every file changed since `79e2d49ccafb5d1c76f91609769bfe39d44ae3cf`
- Verify: no runtime-local `config.yaml`, prompt-store data, logs, caches, `.superpowers`, test results, or Playwright reports are staged.

**Interfaces:**
- Consumes: all prior task commits.
- Produces: a reviewed feature tip ready for the repository's fast-forward integration workflow.

- [ ] **Step 1: Run mechanical diff checks**

```powershell
git status --short
git diff --check 79e2d49ccafb5d1c76f91609769bfe39d44ae3cf..HEAD
git diff --stat 79e2d49ccafb5d1c76f91609769bfe39d44ae3cf..HEAD
```

Expected: clean worktree before verification, no whitespace errors, and only intended source/test/doc/snapshot files.

- [ ] **Step 2: Run focused security and authority suites**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_prompt_assets.py tests/test_prompt_store.py tests/test_prompt_service.py tests/test_path_validation.py tests/test_cache_locking.py tests/test_job_authority.py tests/test_job_submission.py tests/test_instances.py -v`

Expected: PASS.

- [ ] **Step 3: Run the complete Python suite**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/ -q`

Expected: at least the baseline test count plus new prompt tests, with zero failures.

- [ ] **Step 4: Run the complete browser suite**

Run: `npm run test:ui`

Expected: PASS across all four projects without updating snapshots.

- [ ] **Step 5: Perform the required whole-branch review**

Invoke `superpowers:requesting-code-review` against the approved spec and the diff from the design commit. Resolve every correctness, security, concurrency, path-safety, missing-test, and UX finding. Re-run the affected focused tests after each repair, then repeat Steps 1-4.

- [ ] **Step 6: Commit only review repairs when needed**

```bash
git add -u
git add agency tests skills kb examples README.md CLAUDE.md config.yaml.example pyproject.toml
git status --short
git commit -m "fix(prompts): address branch review findings"
```

Inspect `git status --short` before committing and unstage any runtime-local or
unrelated path. If the review requires no changes, do not create an empty commit.

- [ ] **Step 7: Hand off to the repository integration workflow**

Use `superpowers:finishing-a-development-branch`. After approval, fast-forward `master` to the reviewed feature tip without merge, squash, or rebase; run the complete Python and browser suites on fast-forwarded `master`; remove `.worktrees/portable-agent-prompts`; retain `feature/portable-agent-prompts` unless explicitly asked to delete it.