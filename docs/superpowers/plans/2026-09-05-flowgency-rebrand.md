# Flowgency Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the project's previous identity with Flowgency across code, configuration, packaging, operations, documentation, and visual assets without changing runtime behavior.

**Architecture:** Perform one clean semantic rename in independently testable ownership slices. Move the Python namespace first, then reset the configuration lineage and rename branded domain symbols, operational identities, setup discovery, web/PWA surfaces, assets, and documentation; finish with a no-exception tracked-tree naming gate and clean-package verification.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, Jinja2, PyYAML, setuptools, PowerShell, Node.js, Playwright, pytest

## Global Constraints

- Work only in the existing `feat/flowgency-rebrand` branch and `.worktrees/flowgency-rebrand` worktree until integration.
- The approved design is `docs/superpowers/specs/2026-09-05-flowgency-rebrand-design.md`.
- Flowgency is the only product identity; do not add aliases, conversion loaders, deprecation bridges, or dual entry points.
- The Python distribution, import package, and executable are all named `flowgency`.
- Flowgency accepts only `schema_version: 1` with a required `flowgency:` root.
- Environment variables use the `FLOWGENCY_` prefix; runtime-owned hidden paths use `.flowgency`.
- The canonical repository URL is `https://github.com/Blackhex/flowgency`.
- Preserve generic domain terms such as `agent`, `agents`, `agent_library`, and the standards-owned `AGENTS.md` filename.
- Do not implement tickets, boards, workflow definitions, state transitions, WIP enforcement, assignment rules, or new review behavior.
- Do not change job, prompt, memory, permission, integration, dispatch, record, or workspace semantics.
- Do not redesign current pages or change theme color values; rename CSS/theme identifiers and replace branding only.
- Do not edit the ignored local `config.yaml`, `build/`, distribution metadata directories, runtime state, logs, or reports.
- Update every tracked dated specification and plan; the final naming gate has no historical-document exclusion.
- Preserve the user's uncommitted newline-only change in the main checkout's CLI contract test.
- Use Conventional Commits with lowercase imperative subjects under 72 characters.
- Avoid the repository-prohibited prose tokens enforced by `tests/test_repository_boundaries.py` in every new tracked file.
- Approved icon assets: `docs/superpowers/specs/assets/2026-09-05-flowgency-rebrand/flowgency-icon.{html,png}`.
- Approved board assets: `docs/superpowers/specs/assets/2026-09-05-flowgency-rebrand/flowgency-board.{html,png}`.
- Python baseline: `2013 passed, 6 skipped` — confirmed from the feature worktree root using the feature `.venv` interpreter with `pytest` and `httpx` installed; establish this baseline again before Task 1 if the venv is rebuilt. The baseline figure recorded in the initial ledger used the main checkout and global interpreter and was not from this worktree.
- UI baseline: `104 passed, 2 skipped, 6 failed`; four failures are checkout-sensitive `agent-runtime` image diffs and two are pre-existing light-mode Dashboard contrast findings. Do not fold unrelated contrast changes into this branch.

## File Structure

- `flowgency/` becomes the sole Python package. Existing subpackages retain their responsibilities.
- `flowgency/integrations/flowgency/` owns built-in integration implementations.
- `flowgency/configuration/models.py` owns schema 1, `FlowgencyConfig`, and the `flowgency` root.
- `flowgency/configuration/patches.py` owns `FlowgencySettingsPatch` and `patch_flowgency_settings`.
- `flowgency/web/dependencies.py` owns `FlowgencyServices` and `build_services`.
- `flowgency/web/state.py` exposes `flowgency_settings`.
- `flowgency/static/icon.svg` is the canonical production icon source.
- `tools/render_brand_assets.mjs` deterministically renders raster and maskable derivatives.
- `tests/test_flowgency_namespace.py` pins distribution, package, and executable identity.
- `tests/test_flowgency_branding.py` pins README, PWA, and asset contracts.
- `tests/test_flowgency_naming.py` scans every tracked path, symlink target, and UTF-8 text file for previous brand terms.
- `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/` is the package-owned setup skill.
- `skills/flowgency-setup` and `.github/skills/flowgency-setup` remain symlinks to that package-owned directory.
- `flowgency.service.example` is the service template.
- `screenshots/flowgency-board.png` is the README's synthesized product screenshot.
- `screenshots/logo.svg` and `screenshots/logo-light.svg` use the approved tree geometry.

## Execution Preconditions

From the feature worktree root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest
npm ci
```

The directories are ignored. Re-run the editable install after Task 1 because
the distribution and package names change.

---

### Task 1: Python Namespace And Distribution Identity

**Files:**
- Create: `tests/test_flowgency_namespace.py`
- Rename: current top-level Python package directory to `flowgency/`
- Rename: current built-in integration directory to `flowgency/integrations/flowgency/`
- Modify: `pyproject.toml`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: all tracked Python imports and quoted module paths under `flowgency/**/*.py`, `tests/**/*.py`, and `tools/**/*.py`

**Interfaces:**
- Consumes: the existing package tree and `main()` in the CLI module.
- Produces: importable package `flowgency`; built-in integration namespace `flowgency.integrations.flowgency`; console script `flowgency = flowgency.cli:main`.

- [ ] **Step 1: Write the failing namespace contract**

Create `tests/test_flowgency_namespace.py`:

```python
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).parents[1]


def test_python_distribution_and_import_namespace_are_flowgency():
    project = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["name"] == "flowgency"
    assert project["project"]["scripts"] == {
        "flowgency": "flowgency.cli:main",
    }
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "flowgency*"
    ]
    assert (REPO_ROOT / "flowgency").is_dir()
    assert importlib.util.find_spec("flowgency") is not None
    assert importlib.util.find_spec("flowgency.integrations.flowgency") is not None

    previous_package = "".join(("a", "gency"))
    assert not (REPO_ROOT / previous_package).exists()
    assert importlib.util.find_spec(previous_package) is None


def test_ui_test_package_uses_flowgency_name():
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads(
        (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8")
    )
    assert package["name"] == "flowgency-ui-gate"
    assert package_lock["name"] == "flowgency-ui-gate"
    assert package_lock["packages"][""]["name"] == "flowgency-ui-gate"
```

- [ ] **Step 2: Run the namespace contract to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_namespace.py -q
```

Expected: FAIL because `flowgency/` and the Flowgency package metadata do not
exist yet.

- [ ] **Step 3: Move the two namespace directories with Git**

Use fragments so no tracked planning artifact contains the previous product
noun literally:

```powershell
$previousPackage = -join ('a', 'gency')
git mv $previousPackage flowgency
git mv "flowgency/integrations/$previousPackage" "flowgency/integrations/flowgency"
```

Do not copy the directories. Git must record both as moves so history remains
readable.

- [ ] **Step 4: Replace only Python import and module-path identities**

Apply these exact semantic transformations to tracked Python files:

```python
previous_package = "".join(("a", "gency"))
import_replacements = {
    f"from {previous_package}": "from flowgency",
    f"import {previous_package}": "import flowgency",
    f'"{previous_package}.app:app"': '"flowgency.app:app"',
    f'"{previous_package}.jobs.worker"': '"flowgency.jobs.worker"',
    f'"{previous_package}.dispatch.run"': '"flowgency.dispatch.run"',
}
```

Use the editor's semantic rename for import references where available. Apply
the quoted module-path replacements only to the exact strings shown. Do not
rename config-root strings, environment variables, prose, or runtime hidden
paths in this task.

Also update quoted source-root paths used by tests and reload filters from the
previous package directory to `flowgency`, including package asset discovery,
the repository boundary scan root, and watched paths in `tests/test_server.py`.
These are namespace paths, not product prose.

- [ ] **Step 5: Update Python packaging metadata**

Make the relevant `pyproject.toml` blocks exactly:

```toml
[project]
name = "flowgency"

[project.scripts]
flowgency = "flowgency.cli:main"

[tool.setuptools.packages.find]
include = ["flowgency*"]

[tool.setuptools.package-data]
flowgency = ["templates/*.html", "static/*", "themes/*.yaml"]
"flowgency.setup_assets" = [
    "copilot/.github/skills/flowgency-setup/*.md",
    "copilot/.github/skills/flowgency-setup/references/*.md",
]
```

Keep the version and dependency constraints unchanged.

- [ ] **Step 6: Update the UI package name and lock metadata**

Set `package.json`'s `name` to `flowgency-ui-gate`, then run:

```powershell
npm install --package-lock-only
```

Confirm no dependency version changed:

```powershell
git diff -- package.json package-lock.json
```

- [ ] **Step 7: Reinstall and validate namespace imports**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m compileall -q flowgency tests tools
.\.venv\Scripts\python.exe -c "import flowgency; import flowgency.cli; import flowgency.integrations.flowgency.copilot"
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_namespace.py tests/test_cli_contract.py -q
```

Expected: compilation succeeds and both test files pass.

- [ ] **Step 8: Verify no Python import still targets the previous namespace**

Run this source-aware check:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; p=''.join(('a','gency')); hits=[str(f) for root in ('flowgency','tests','tools') for f in Path(root).rglob('*.py') if (f'from {p}' in f.read_text(encoding='utf-8') or f'import {p}' in f.read_text(encoding='utf-8'))]; assert not hits, hits"
```

Expected: no output and exit code 0.

- [ ] **Step 9: Commit the namespace slice**

```powershell
git add -A
git commit -m "refactor: rename python namespace to flowgency"
```

---

### Task 2: Flowgency Schema 1 And Branded Domain Types

**Files:**
- Modify: `flowgency/configuration/models.py`
- Modify: `flowgency/configuration/patches.py`
- Modify: `flowgency/configuration/__init__.py`
- Modify: `flowgency/configuration/effective.py`
- Modify: `flowgency/configuration/paths.py`
- Modify: `flowgency/configuration/store.py`
- Modify: `flowgency/config.py`
- Modify: `flowgency/app.py`
- Modify: `flowgency/web/dependencies.py`
- Modify: `flowgency/web/state.py`
- Modify: `flowgency/web/__init__.py`
- Modify: `flowgency/web/routes/*.py`
- Modify: `config.yaml.example`
- Modify: `tests/conftest.py`
- Modify: every schema-bearing fixture under `tests/**/*.py`, `tests/ui/fixtures/config.yaml`, and `tests/ui/server.py`
- Test: `tests/test_config.py`
- Test: `tests/test_config_store.py`
- Test: `tests/test_permission_models.py`
- Test: `tests/test_team_terminology.py`

**Interfaces:**
- Consumes: the `flowgency` namespace from Task 1.
- Produces: `CONFIG_SCHEMA_VERSION = 1`; `FlowgencyDispatch`, `FlowgencyJobs`, `FlowgencySettings`, `FlowgencyConfig`, `FlowgencySettingsPatch`, `FlowgencyServices`; `ParsedConfig.flowgency`; `patch_flowgency_settings`; `flowgency_settings`; `get_flowgency_config`.

- [ ] **Step 1: Rewrite the canonical fixture and add schema-lineage tests**

Update `tests/conftest.py` so `raw_config` starts with:

```python
return {
    "schema_version": 1,
    "flowgency": {
        "title": "Flowgency",
        "default_team": "newsletter",
        "ai_backend": "claude-code",
        "agent_library": str(config_paths["agent_library"]),
        "compilation_cache": str(config_paths["compilation_cache"]),
        "memory_store": str(config_paths["memory_store"]),
        "prompt_store": str(config_paths["prompt_store"]),
    },
    # existing memory and teams mappings remain unchanged
}
```

Replace the opening tests in `tests/test_config.py` with contracts equivalent
to:

```python
def test_parse_config_accepts_flowgency_schema_one(raw_config, config_paths):
    from flowgency.configuration.models import parse_config

    parsed = parse_config(raw_config, config_paths["config_path"])

    assert parsed.raw == raw_config
    assert parsed.resolved.flowgency.title == "Flowgency"
    assert parsed.flowgency.title == "Flowgency"
    assert parsed.resolved.schema_version == 1


def test_previous_root_is_rejected_without_conversion(raw_config, config_paths):
    from flowgency.configuration.models import validate_config

    previous_root = "".join(("a", "gency"))
    candidate = _clone_config(raw_config)
    candidate[previous_root] = candidate.pop("flowgency")

    issues = validate_config(candidate, config_paths["config_path"])

    assert any(
        issue.code == "invalid-config" and issue.field == previous_root
        for issue in issues
    )
    assert not any("convert" in issue.corrective_hint.lower() for issue in issues)


def test_only_schema_one_is_accepted(raw_config, config_paths):
    from flowgency.configuration.models import parse_config, validate_config

    assert (
        parse_config(raw_config, config_paths["config_path"]).resolved.schema_version
        == 1
    )
    for value in (None, 0, 2, 3, 4, 5, 6):
        candidate = _clone_config(raw_config)
        if value is None:
            candidate.pop("schema_version")
        else:
            candidate["schema_version"] = value
        assert any(
            issue.field == "schema_version"
            for issue in validate_config(candidate, config_paths["config_path"])
        )
```

- [ ] **Step 2: Run the schema tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: FAIL because the parser still requires the previous root and version.

- [ ] **Step 3: Rename the branded model and service symbols semantically**

Use language-server rename, one symbol at a time, and accept every source and
test reference:

```text
"A" + "gencyDispatch"      -> FlowgencyDispatch
"A" + "gencyJobs"          -> FlowgencyJobs
"A" + "gencySettings"      -> FlowgencySettings
"A" + "gencyConfig"        -> FlowgencyConfig
"A" + "gencySettingsPatch" -> FlowgencySettingsPatch
"A" + "gencyServices"      -> FlowgencyServices
patch_ + "a" + "gency_settings" -> patch_flowgency_settings
"a" + "gency_settings"     -> flowgency_settings
get_ + "a" + "gency_config" -> get_flowgency_config
```

Do not add aliases under the previous names.

- [ ] **Step 4: Implement the Flowgency root and schema model**

Make the controlling definitions in
`flowgency/configuration/models.py` equivalent to:

```python
CONFIG_SCHEMA_VERSION = 1
_ROOT_KEYS = {"schema_version", "flowgency", "memory", "teams"}


class FlowgencyDispatch(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    interval: int = 15


class FlowgencyJobs(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    pool: int = Field(default=4, ge=1)


class FlowgencySettings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    title: str = "Flowgency"
    default_team: str = ""
    ai_backend: str = "claude-code"
    dispatch: FlowgencyDispatch = Field(default_factory=FlowgencyDispatch)
    jobs: FlowgencyJobs = Field(default_factory=FlowgencyJobs)
    agent_library: Path | None = None
    compilation_cache: Path | None = None
    memory_store: Path | None = None
    prompt_store: Path | None = None


class FlowgencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    flowgency: FlowgencySettings
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    teams: dict[str, TeamConfig]


class ParsedConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    raw: dict[str, Any]
    resolved: FlowgencyConfig

    @property
    def flowgency(self) -> FlowgencySettings:
        return self.resolved.flowgency
```

Rename local variables, validation scopes, fields, hints, preparation keys,
post-parse accessors, and Pydantic construction to `flowgency`. The unsupported
version message must say `schema_version must be 1.` and instruct callers to
rewrite to schema 1 with `teams`.

- [ ] **Step 5: Update config patching and consumers**

Make `patch_flowgency_settings` write only to the renamed root:

```python
def patch_flowgency_settings(
    store: ConfigStore,
    expected_revision: str,
    patch: FlowgencySettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        flowgency = raw.setdefault("flowgency", {})
        flowgency["title"] = patch.title
        flowgency["default_team"] = patch.default_team
        flowgency["ai_backend"] = patch.ai_backend
        flowgency["theme"] = patch.theme
        flowgency["agent_library"] = patch.agent_library
        flowgency["compilation_cache"] = patch.compilation_cache
        flowgency["memory_store"] = patch.memory_store
        flowgency["prompt_store"] = patch.prompt_store
        flowgency.setdefault("dispatch", {})["interval"] = patch.dispatch_interval

    return store.patch(expected_revision, apply)
```

Apply the same root rename in tip dismissal, default-team maintenance, path
resolution, service construction, Python helper consumers, and all `.config`
type annotations. Every access formerly made through the previous root
property must use `.flowgency`. Leave template context key strings for Task 5.

- [ ] **Step 6: Update every executable test fixture to schema 1**

Generate the fixture manifest before editing:

```powershell
git grep -Il 'schema_version' -- 'tests/**'
git grep -Il '"flowgency"' -- 'tests/**'
```

For every executable config fixture, apply exactly:

```yaml
schema_version: 1
flowgency:
```

or the equivalent Python dictionary keys. Keep deliberate rejection fixtures
on their tested unsupported number, but build them from a valid schema-1 base.
Update `config.yaml.example` and `tests/ui/fixtures/config.yaml` in the same
step. Do not edit the ignored root `config.yaml`.

- [ ] **Step 7: Run focused configuration and service tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_config_store.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_permission_models.py tests/test_permission_resolution.py tests/test_team_terminology.py tests/test_setup_flow.py -q
```

Expected: all pass with Flowgency schema 1.

- [ ] **Step 8: Run the broad config-consumer slice**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli_contract.py tests/test_dashboard.py tests/test_instances.py tests/test_job_submission.py tests/test_prompt_catalog.py tests/test_server.py -q
```

Expected: all pass without a compatibility root.

- [ ] **Step 9: Commit the schema slice**

```powershell
git add -A
git commit -m "refactor(config): start flowgency schema one"
```

---

### Task 3: Runtime Paths, Environment, Scheduler, And Service Identity

**Files:**
- Modify: `flowgency/clock.py`
- Modify: `flowgency/cli.py`
- Modify: `flowgency/app.py`
- Modify: `flowgency/web/dependencies.py`
- Modify: `flowgency/records/outbox.py`
- Modify: `flowgency/permissions/zones.py`
- Modify: `flowgency/integrations/__init__.py`
- Modify: `flowgency/dispatch/install.py`
- Modify: `flowgency/dispatch/run.py`
- Rename: current service example to `flowgency.service.example`
- Modify: `.vscode/tasks.json`
- Modify: tests covering clock, CLI, launch zones, outbox, integrations, dispatch, recovery, and systemd probes

**Interfaces:**
- Consumes: Flowgency package and schema types from Tasks 1-2.
- Produces: `FLOWGENCY_CONFIG`, `FLOWGENCY_FIXED_NOW`, `FLOWGENCY_TEST_SYSTEMD`, `FLOWGENCY_REAL_RUNTIME_PROBES`, and Flowgency-prefixed test probe variables; `.flowgency/outbox`, `.flowgency/memory`, `.flowgency-meta.yaml`; `FlowgencyDispatch`; `com.flowgency.dispatch`; `flowgency-dispatch.{service,timer}`.

- [ ] **Step 1: Add failing runtime-identity assertions**

Update existing tests with these contracts:

```python
def test_launch_zones_use_flowgency_directory():
    from flowgency.permissions.zones import ZONE_MEMORY, ZONE_OUTBOX

    assert ZONE_OUTBOX == ".flowgency/outbox"
    assert ZONE_MEMORY == ".flowgency/memory"


def test_previous_config_environment_variable_is_ignored(tmp_path, monkeypatch):
    from flowgency.web.dependencies import build_services

    previous_name = "".join(("A", "GENCY_CONFIG"))
    monkeypatch.setenv(previous_name, str(tmp_path / "ignored.yaml"))
    monkeypatch.delenv("FLOWGENCY_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    services = build_services()

    assert services.config_path == (tmp_path / "config.yaml").resolve()
```

Update scheduler expectations to assert `FlowgencyDispatch`,
`com.flowgency.dispatch`, `flowgency-dispatch.service`,
`flowgency-dispatch.timer`, and `-m flowgency.dispatch.run`.

- [ ] **Step 2: Run the runtime identity tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_clock.py tests/test_launch_zones.py tests/test_dispatch_install.py -q
```

Expected: FAIL on previous environment, hidden-path, module, and scheduler
identities.

- [ ] **Step 3: Rename all environment variables**

Use this complete target set and rename source constants plus all tests and
documentation that define or consume them:

```text
FLOWGENCY_CONFIG
FLOWGENCY_FIXED_NOW
FLOWGENCY_NEW_JOBS
FLOWGENCY_REAL_RUNTIME_PROBES
FLOWGENCY_TEST_SYSTEMD
FLOWGENCY_TOKEN
FLOWGENCY_DIRNAME
```

Do not read either prefix as a fallback. `FLOWGENCY_CONFIG` falls back directly
to `Path.cwd() / "config.yaml"`; `FLOWGENCY_FIXED_NOW` falls back directly to
the real clock.

- [ ] **Step 4: Rename runtime-owned paths and sidecar identity**

Make the controlling constants exactly:

```python
ZONE_INSTRUCTIONS = "instructions"
ZONE_OUTBOX = ".flowgency/outbox"
ZONE_MEMORY = ".flowgency/memory"
_FLOWGENCY_DIRNAME = ".flowgency"
SIDECAR_FILENAME = ".flowgency-meta.yaml"
```

Update launch instructions, test probes, record workers, job snapshots, and
path assertions to the same values. Keep all permissions and filesystem safety
checks unchanged.

- [ ] **Step 5: Rename scheduler identities without changing scheduling logic**

Make the platform constants and generated filenames exactly:

```python
DISPATCH_CONF_DIR = Path.home() / ".config" / "flowgency"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
LAUNCHD_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_PLIST = "com.flowgency.dispatch"
WINDOWS_TASK_NAME = "FlowgencyDispatch"
```

Systemd generation uses `flowgency-dispatch.service` and
`flowgency-dispatch.timer`. Generated commands use
`-m flowgency.dispatch.run`. Conflict errors say `Flowgency dispatcher`.

- [ ] **Step 6: Rename service and workspace task identities**

Move the existing service template using an assembled source name:

```powershell
$previous = -join ('a', 'gency.service.example')
git mv $previous flowgency.service.example
```

Set its description to `Flowgency Dashboard`, its executable module to
`flowgency.app`, and example install paths to `/path/to/flowgency`. Change both
Dashboard tasks in `.vscode/tasks.json` to run the `flowgency` executable.

- [ ] **Step 7: Run focused runtime and scheduler tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_clock.py tests/test_cli.py tests/test_launch_zones.py tests/test_records_outbox.py tests/test_records_worker.py tests/test_memory_round_trip.py tests/test_integration_sidecar.py tests/test_dispatch_install.py tests/test_dispatch_run.py tests/test_dispatch_launch_recovery.py tests/test_job_systemd_integration.py -q
```

Expected: all pass with no fallback to previous identities.

- [ ] **Step 8: Commit the runtime identity slice**

```powershell
git add -A
git commit -m "refactor(runtime): rename flowgency identities"
```

---

### Task 4: Flowgency Setup Skill And Discovery Links

**Files:**
- Rename: package-owned setup directory to `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/`
- Rename: repository link to `skills/flowgency-setup`
- Rename: discovery link to `.github/skills/flowgency-setup`
- Rename: setup contract test to `tests/test_flowgency_setup_skill.py`
- Modify: `flowgency/setup_assets/__init__.py`
- Modify: `tests/test_setup_assets.py`
- Modify: `tests/test_interactive_setup.py`
- Modify: `tests/test_setup_flow.py`
- Modify: `tests/test_setup_skill_e2e.py`
- Modify: `kb/setup-skill.md`

**Interfaces:**
- Consumes: `flowgency` package data declaration from Task 1 and schema 1 from Task 2.
- Produces: canonical skill path ending in `flowgency-setup`; frontmatter `name: flowgency-setup`; two repository symlinks resolving to the package-owned source; Flowgency CLI/config guidance.

- [ ] **Step 1: Rename the setup test and write failing target-path assertions**

Use an assembled source filename:

```powershell
$previousTest = 'tests/test_' + (-join ('a', 'gency_setup_skill.py'))
git mv $previousTest tests/test_flowgency_setup_skill.py
```

Update its path constants and first test to:

```python
CANONICAL_SKILL_DIR = (
    copilot_discovery_root() / ".github" / "skills" / "flowgency-setup"
)
REPOSITORY_SKILL_DIR = REPO_ROOT / "skills" / "flowgency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "flowgency-setup"


def test_repository_skill_paths_resolve_to_package_owned_source():
    canonical = CANONICAL_SKILL_DIR.resolve(strict=True)
    assert REPOSITORY_SKILL_DIR.resolve(strict=True) == canonical
    assert DISCOVERY_SKILL_DIR.resolve(strict=True) == canonical
```

Add assertions that `SKILL.md` contains `name: flowgency-setup`,
`schema_version: 1`, `flowgency:`, `FLOWGENCY_CONFIG`, and `flowgency validate`.

- [ ] **Step 2: Run setup discovery tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_setup_skill.py tests/test_setup_assets.py -q
```

Expected: FAIL because target directories and content do not exist.

- [ ] **Step 3: Move the canonical skill directory**

```powershell
$previousSkill = -join ('a', 'gency-setup')
git mv "flowgency/setup_assets/copilot/.github/skills/$previousSkill" "flowgency/setup_assets/copilot/.github/skills/flowgency-setup"
```

- [ ] **Step 4: Recreate both symlinks with target names**

Remove only the two tracked links, then create their target replacements:

```powershell
$previousSkill = -join ('a', 'gency-setup')
git rm ".github/skills/$previousSkill" "skills/$previousSkill"
New-Item -ItemType SymbolicLink -Path '.github/skills/flowgency-setup' -Target '..\..\flowgency\setup_assets\copilot\.github\skills\flowgency-setup'
New-Item -ItemType SymbolicLink -Path 'skills/flowgency-setup' -Target '..\flowgency\setup_assets\copilot\.github\skills\flowgency-setup'
git add '.github/skills/flowgency-setup' 'skills/flowgency-setup'
```

Verify both tracked modes are `120000` with `git ls-files -s`.

- [ ] **Step 5: Rewrite setup guidance to Flowgency schema 1**

The canonical skill and every reference file must consistently use:

```yaml
schema_version: 1
flowgency:
  title: Flowgency
```

Use `FLOWGENCY_CONFIG`, data roots such as `C:/Flowgency`, the executable
`flowgency`, and the invocation `flowgency-setup`. Preserve the existing guided
question order, validation, one atomic config write, scheduler offer, team
synthesis, permissions, prompt, memory, and workspace behavior.

- [ ] **Step 6: Update setup packaging and integration tests**

Update `tests/test_setup_assets.py` so the canonical directory ends in
`flowgency-setup` and the wheel glob is `flowgency-*.whl`. Update interactive
setup fallback labels and error text to Flowgency. Keep
`copilot_discovery_root() -> Path` unchanged because it returns the parent
discovery root, not a branded skill path.

- [ ] **Step 7: Run the complete setup slice**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_setup_skill.py tests/test_setup_assets.py tests/test_interactive_setup.py tests/test_setup_flow.py tests/test_setup_skill_e2e.py -q
```

Expected: all pass and both symlinks resolve to the packaged source.

- [ ] **Step 8: Commit the setup slice**

```powershell
git add -A
git commit -m "refactor(setup): rename flowgency setup skill"
```

---

### Task 5: Web, Template, PWA, And Theme Branding

**Files:**
- Modify: `flowgency/app.py`
- Modify: `flowgency/web/state.py`
- Modify: `flowgency/web/routes/*.py`
- Modify: `flowgency/templates/*.html`
- Modify: `flowgency/static/manifest.json`
- Modify: `flowgency/static/sw.js`
- Modify: `flowgency/themes/*.yaml`
- Test: `tests/test_server.py`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_surface_contracts.py`
- Test: `tests/test_strict_app_authority.py`

**Interfaces:**
- Consumes: `FlowgencyServices`, `FlowgencySettingsPatch`, and schema 1 from Task 2.
- Produces: template context key `flowgency_title`; Tailwind token namespace `flowgency`; PWA name `Flowgency`; cache key `flowgency-app-shell`; static icon rendering in navigation.

- [ ] **Step 1: Add failing web-brand contracts**

Add assertions equivalent to these in `tests/test_server.py`:

```python
def test_static_pwa_metadata_uses_flowgency():
    import json

    repo_root = Path(__file__).parents[1]
    manifest = json.loads(
        (repo_root / "flowgency" / "static" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    service_worker = (
        repo_root / "flowgency" / "static" / "sw.js"
    ).read_text(encoding="utf-8")
    base_template = (
        repo_root / "flowgency" / "templates" / "base.html"
    ).read_text(encoding="utf-8")

    assert manifest["name"] == "Flowgency"
    assert manifest["short_name"] == "Flowgency"
    assert manifest["description"] == (
        "Ticket-driven orchestration for teams of AI agents"
    )
    assert "flowgency-app-shell" in service_worker
    assert "flowgency_title" in base_template
    assert "bg-flowgency-" in base_template
```

Add a rendered-page assertion that the default document title and mobile brand
are `Flowgency`.

- [ ] **Step 2: Run web-brand tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_dashboard.py -q
```

Expected: FAIL on manifest, cache, template context, and displayed title.

- [ ] **Step 3: Rename template contexts and branded web helpers**

Every route context and template uses:

```python
{
    "flowgency_title": snapshot.config.flowgency.title,
    # existing team, navigation, form, and page data remains unchanged
}
```

Replace the previous namespace plus `_title` identifiers with
`flowgency_title`, including setup pages, error paths, hidden form values,
mobile headers, and page title blocks. Defaults must be `Flowgency`. Do not
change URL routes.

- [ ] **Step 4: Rename CSS token namespaces without changing values**

In Tailwind configuration and templates, rename only the token namespace:

```javascript
colors: {
  flowgency: {
    50: '#f0f4ff',
    100: '#dbe4ff',
    200: '#bac8ff',
    500: '#5c7cfa',
    600: '#4263eb',
    700: '#3b5bdb',
    800: '#364fc7',
    900: '#1e2a5e',
    950: '#141b3d',
  }
}
```

Rename every `bg-*`, `text-*`, `border-*`, `ring-*`, `focus:*`, and dark-mode
class that uses the previous token namespace. Preserve all hexadecimal values,
spacing, layout, and states.

- [ ] **Step 5: Replace inline node artwork with the static icon**

Use one image in desktop and mobile brand locations:

```html
<img
  src="/static/icon.svg"
  alt=""
  class="h-8 w-8 shrink-0"
  width="32"
  height="32"
>
```

The product name remains adjacent visible text, so the decorative image uses an
empty alt attribute. Do not add a second nested card or alter navigation size.

- [ ] **Step 6: Update PWA and theme identifiers**

Set `manifest.json` to:

```json
{
  "name": "Flowgency",
  "short_name": "Flowgency",
  "description": "Ticket-driven orchestration for teams of AI agents"
}
```

Preserve the remaining manifest fields and icon declarations. Set the service
worker cache name to `flowgency-app-shell`. Rename theme `author` values and
theme scale comments/identifiers to Flowgency without changing palette values.

- [ ] **Step 7: Run focused rendered-surface tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_dashboard.py tests/test_surface_contracts.py tests/test_strict_app_authority.py -q
```

Expected: all pass with unchanged routes and behavior.

- [ ] **Step 8: Commit web branding**

```powershell
git add -A
git commit -m "refactor(web): apply flowgency branding"
```

---

### Task 6: Production Brand Assets And Visual Snapshots

**Files:**
- Create: `tools/render_brand_assets.mjs`
- Create: `tests/test_flowgency_branding.py`
- Replace: `flowgency/static/icon.svg`
- Replace: `flowgency/static/icon-maskable.svg`
- Replace: `flowgency/static/favicon-16.png`
- Replace: `flowgency/static/favicon-32.png`
- Replace: `flowgency/static/apple-touch-icon.png`
- Replace: `flowgency/static/icon-192.png`
- Replace: `flowgency/static/icon-192-maskable.png`
- Replace: `flowgency/static/icon-512.png`
- Replace: `flowgency/static/icon-512-maskable.png`
- Replace: `screenshots/logo.svg`
- Replace: `screenshots/logo-light.svg`
- Create: `screenshots/flowgency-board.png`
- Regenerate: `tests/ui/agent_configuration.spec.ts-snapshots/*.png`
- Regenerate: `tests/ui/dashboard.spec.ts-snapshots/*.png`

**Interfaces:**
- Consumes: approved icon and board sources from the design spec; static icon references from Task 5.
- Produces: canonical `flowgency/static/icon.svg`; reproducible raster derivatives; README board screenshot; updated visual baselines.

- [ ] **Step 1: Write failing brand-asset tests**

Create `tests/test_flowgency_branding.py` with binary-safe dimension checks:

```python
from __future__ import annotations

import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).parents[1]
STATIC_ROOT = REPO_ROOT / "flowgency" / "static"


def _png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


def test_production_icon_assets_have_required_dimensions():
    expected = {
        "favicon-16.png": (16, 16),
        "favicon-32.png": (32, 32),
        "apple-touch-icon.png": (180, 180),
        "icon-192.png": (192, 192),
        "icon-192-maskable.png": (192, 192),
        "icon-512.png": (512, 512),
        "icon-512-maskable.png": (512, 512),
    }
    assert {name: _png_dimensions(STATIC_ROOT / name) for name in expected} == expected


def test_production_svg_and_manifest_reference_flowgency_tree():
    svg_path = STATIC_ROOT / "icon.svg"
    root = ET.parse(svg_path).getroot()
    source = svg_path.read_text(encoding="utf-8")
    manifest = json.loads((STATIC_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert root.attrib["viewBox"] == "0 0 512 512"
    for color in ("#31549f", "#d5563f", "#dda42f", "#4f745d"):
        assert color in source.lower()
    assert "flowgency-tree" in source
    assert {entry["src"] for entry in manifest["icons"]} >= {
        "/static/icon.svg",
        "/static/icon-192.png",
        "/static/icon-192-maskable.png",
        "/static/icon-512.png",
        "/static/icon-512-maskable.png",
    }


def test_readme_board_screenshot_matches_approved_dimensions():
    assert _png_dimensions(REPO_ROOT / "screenshots" / "flowgency-board.png") == (
        1440,
        850,
    )
```

- [ ] **Step 2: Run brand-asset tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_branding.py -q
```

Expected: FAIL because approved production assets do not exist yet.

- [ ] **Step 3: Extract the approved mark into the canonical SVG**

Use the exact artwork inside the `.closer-svg` element in
`docs/superpowers/specs/assets/2026-09-05-flowgency-rebrand/flowgency-icon.html`.
Normalize it to `viewBox="0 0 512 512"`, preserve aspect ratio, retain the
approved bone background and all accepted stem, centerline, leaf, hanger, and
ticket geometry, and add `id="flowgency-tree"` to the artwork group.

Do not redraw or simplify the 192 px and 512 px variants. For 16 px and 32 px,
the renderer may hide internal ticket marks and centerlines through a named
`small-icon-detail` group while preserving the silhouette.

- [ ] **Step 4: Add the deterministic asset renderer**

Create `tools/render_brand_assets.mjs` with this structure:

```javascript
import { chromium } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = process.cwd();
const staticRoot = path.join(root, 'flowgency', 'static');
const source = path.join(staticRoot, 'icon.svg');
const outputs = [
  ['favicon-16.png', 16, 1],
  ['favicon-32.png', 32, 1],
  ['apple-touch-icon.png', 180, 1],
  ['icon-192.png', 192, 1],
  ['icon-192-maskable.png', 192, 0.8],
  ['icon-512.png', 512, 1],
  ['icon-512-maskable.png', 512, 0.8],
];

// Read and validate the canonical SVG before launching the browser.
const sourceText = await readFile(source, 'utf-8');
if (!sourceText.includes('id="flowgency-tree"')) {
  throw new Error('canonical icon is missing the Flowgency tree group');
}
// Embed as a data URL so Chromium loads it without file-origin restrictions.
const svgDataUrl = 'data:image/svg+xml;base64,' + Buffer.from(sourceText).toString('base64');

const browser = await chromium.launch();
try {
  for (const [name, size, scale] of outputs) {
    const page = await browser.newPage({
      viewport: { width: size, height: size },
      deviceScaleFactor: 1,
    });
    const inset = Math.round((size * (1 - scale)) / 2);
    const imageSize = size - inset * 2;
    await page.setContent(`
      <style>
        * { box-sizing: border-box; }
        html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
        body { display: grid; place-items: center; background: #f3efe5; }
        img { width: ${imageSize}px; height: ${imageSize}px; }
      </style>
      <img id="icon" src="${svgDataUrl}" alt="">
    `);
    await page.locator('#icon').waitFor({ state: 'visible' });
    // Verify the image decoded successfully before capturing.
    const loaded = await page.locator('#icon').evaluate(img => img.complete && img.naturalWidth > 0);
    if (!loaded) throw new Error(name + ': image failed to decode');
    await page.screenshot({ path: path.join(staticRoot, name) });
    await page.close();
  }
  // Derive icon-maskable.svg by wrapping the canonical artwork in a background
  // rectangle and scaling it into the central 80 % safe zone, matching the
  // inset applied to the maskable PNG variants above.
  const maskableInset = Math.round(512 * 0.1);
  const artSize = 512 - maskableInset * 2;
  const maskableSvg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n` +
    `  <rect width="512" height="512" fill="#f3efe5"/>\n` +
    `  <image href="${svgDataUrl}" x="${maskableInset}" y="${maskableInset}" ` +
    `width="${artSize}" height="${artSize}"/>\n` +
    `</svg>\n`;
  await writeFile(path.join(staticRoot, 'icon-maskable.svg'), maskableSvg, 'utf-8');
  // Logo copies use the full-bleed canonical mark without the maskable inset.
  await writeFile(path.join(root, 'screenshots', 'logo.svg'), sourceText, 'utf-8');
  await writeFile(path.join(root, 'screenshots', 'logo-light.svg'), sourceText, 'utf-8');
} finally {
  await browser.close();
}
```

The maskable PNG variants scale the tree into the central 80 % safe zone via the
`inset` / `imageSize` calculation above. The `icon-maskable.svg` derives the same
safe-zone inset from the canonical artwork rather than copying the full-bleed icon.

- [ ] **Step 5: Render icon derivatives and promote the approved board image**

Run:

```powershell
node tools/render_brand_assets.mjs
Copy-Item 'docs\superpowers\specs\assets\2026-09-05-flowgency-rebrand\flowgency-board.png' 'screenshots\flowgency-board.png' -Force
```

- [ ] **Step 6: Run asset tests and inspect native-size output**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_branding.py tests/test_server.py -q
```

Open and inspect all seven PNG outputs. At 16 px and 32 px, confirm the tree is
nonblank and recognizable. At 180 px, 192 px, and 512 px, confirm the pointed
stem, offset leaf pairs, and close green ticket fruit match the approved
reference. For maskable files, confirm the tree remains inside the central safe
region.

- [ ] **Step 7: Regenerate only branding-affected UI snapshots**

First make the existing `agent-runtime` screenshot checkout-independent: mask
the stable full-width containing rows for `workspaceRule` and `editorialRule`
instead of masking text-width locators, and mask the full textarea control.
Run that one test in all four projects twice before updating any expected
image; both runs must produce the same pixels.

Run:

```powershell
npx playwright test tests/ui/agent_configuration.spec.ts tests/ui/dashboard.spec.ts --update-snapshots
npx playwright test tests/ui/agent_configuration.spec.ts tests/ui/dashboard.spec.ts
```

Expected: both commands pass. Inspect desktop/mobile and light/dark snapshots
for the new mark and title, unchanged layout, no overlap, and no blank images.

- [ ] **Step 8: Commit production assets and snapshots**

```powershell
git add -A
git commit -m "feat(brand): add flowgency visual identity"
```

---

### Task 7: README And Current Operator Documentation

**Files:**
- Rewrite: `README.md`
- Modify: `AGENTS.md`
- Modify: `config.yaml.example`
- Modify: `flowgency.service.example`
- Modify: `kb/*.md`
- Modify: `examples/**/*.md`
- Modify: `examples/**/*.yaml`
- Modify: `.vscode/tasks.json`
- Test: `tests/test_flowgency_branding.py`
- Test: `tests/test_team_terminology.py`

**Interfaces:**
- Consumes: executable/config identities from Tasks 1-4 and production assets from Task 6.
- Produces: ticket-driven README, canonical repository links, schema-1 operator guidance, and a synthesized board image reference.

- [ ] **Step 1: Add failing README and current-doc contracts**

Extend `tests/test_flowgency_branding.py`:

```python
def test_readme_presents_ticket_driven_flowgency():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Flowgency\n")
    assert "Ticket-driven orchestration for teams of AI agents." in readme
    assert "screenshots/logo.svg" in readme
    assert "screenshots/flowgency-board.png" in readme
    assert "https://github.com/Blackhex/flowgency" in readme
    assert "flowgency serve" in readme
    assert "schema_version: 1" in readme
    assert "flowgency:" in readme


def test_current_operator_documents_use_flowgency_schema_one():
    paths = (
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "config.yaml.example",
        REPO_ROOT / "flowgency.service.example",
        *(REPO_ROOT / "kb").glob("*.md"),
        *(REPO_ROOT / "examples").glob("**/*.md"),
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "schema_version: 1" in text
    assert "flowgency:" in text
    assert "FLOWGENCY_CONFIG" in text
```

- [ ] **Step 2: Run documentation contracts to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_branding.py tests/test_team_terminology.py -q
```

Expected: FAIL on README narrative and current operator documents.

- [ ] **Step 3: Rewrite the README in the approved order**

The opening must be exactly structured as:

```markdown
# Flowgency

![Flowgency tree mark](screenshots/logo.svg)

**Ticket-driven orchestration for teams of AI agents.**

See agents take work, move tickets through predefined workflows, and keep every
transition inspectable from one local-first control plane.

![Flowgency delivery workflow with synthetic tickets and agents](screenshots/flowgency-board.png)
```

Continue with sections for the ticket workflow, supporting capabilities, quick
start, schema-1 configuration authority, integrations, local-first operation,
documentation, development, contributing, and AGPL-3.0. Describe the board as
the product experience without inventing workflow configuration fields or
claiming that this rebrand implements the board.

- [ ] **Step 4: Update quick start and repository guidance**

Use only:

```text
git clone https://github.com/Blackhex/flowgency.git
cd flowgency
python -m pip install -e .
flowgency serve
```

Configuration examples use schema 1, the `flowgency` root, `C:/Flowgency`
storage roots, and `FLOWGENCY_CONFIG`. Scheduler examples use
`flowgency dispatch`.

- [ ] **Step 5: Update AGENTS.md, KB, examples, and service guidance**

Preserve every authority and safety boundary while renaming the product,
package paths, config root, environment variables, setup skill, scheduler,
service file, and runtime hidden directory. Keep the statement that reporting
is unconditional, path permissions are longest-match, config writes are
locked/revision-checked/atomic, and decision execution requires an explicitly
eligible instance.

Example YAML must remain parser-valid under schema 1. Update
`tests/test_team_terminology.py` to read target paths and validate embedded YAML
through `flowgency.configuration.models.validate_config`.

- [ ] **Step 6: Run current documentation and example tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_branding.py tests/test_team_terminology.py tests/test_flowgency_setup_skill.py tests/test_setup_skill_e2e.py -q
```

Expected: all pass.

- [ ] **Step 7: Check README assets and links mechanically**

Run:

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; r=Path('README.md').read_text(encoding='utf-8'); required=('screenshots/flowgency-board.png','screenshots/logo.svg','kb/getting-started.md','kb/configuration.md'); missing=[p for p in required if p in r and not Path(p).exists()]; assert not missing, missing"
```

Expected: no missing local references.

- [ ] **Step 8: Commit README and current documentation**

```powershell
git add -A
git commit -m "docs: introduce flowgency product narrative"
```

---

### Task 8: Exhaustive Tracked-tree Naming Gate

**Files:**
- Create: `tests/test_flowgency_naming.py`
- Rename: dated data-root plan to `docs/superpowers/plans/2026-07-23-flowgency-data-root-setup.md`
- Rename: dated data-root spec to `docs/superpowers/specs/2026-07-23-flowgency-data-root-setup-design.md`
- Modify: all remaining tracked text files reported by the new gate, including dated `docs/superpowers/plans/*.md` and `docs/superpowers/specs/*.md`
- Modify: symlink targets and any remaining test, tool, theme, template, JSON, YAML, service, or example text reported by the gate

**Interfaces:**
- Consumes: all prior rename slices.
- Produces: a no-exception invariant over every tracked path, symlink target, and UTF-8 text file.

- [ ] **Step 1: Add the exhaustive naming test**

Create `tests/test_flowgency_naming.py`:

```python
from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).parents[1]
PREVIOUS_TERMS = (
    "".join(("a", "gency")),
    "".join(("chris", "tag")),
)


def _tracked_paths() -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return tuple(
        path for path in completed.stdout.decode("utf-8").split("\0") if path
    )


def test_tracked_paths_omit_previous_brand_terms():
    matches = [
        path
        for path in _tracked_paths()
        if any(term in path.casefold() for term in PREVIOUS_TERMS)
    ]
    assert not matches, "\n".join(matches)


def test_tracked_text_and_symlink_targets_omit_previous_brand_terms():
    matches: list[str] = []
    for relative in _tracked_paths():
        path = REPO_ROOT / relative
        if path.is_symlink():
            text = os.readlink(path)
        elif path.is_file():
            payload = path.read_bytes()
            if b"\0" in payload:
                continue
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            continue
        lowered = text.casefold()
        for term in PREVIOUS_TERMS:
            if term in lowered:
                matches.append(f"{relative}: {term}")
    assert not matches, "\n".join(matches)
```

No path or content exclusion is permitted. Generic `agent` vocabulary and
`AGENTS.md` do not contain either assembled term and therefore need no
allowlist.

- [ ] **Step 2: Run the naming gate to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_naming.py -q
```

Expected: FAIL and print every remaining tracked path/content occurrence.

- [ ] **Step 3: Rename the remaining branded paths**

Use Git moves for every path in the failure list. The known dated paths are:

```powershell
$previous = -join ('a', 'gency')
git mv "docs/superpowers/plans/2026-07-23-$previous-data-root-setup.md" "docs/superpowers/plans/2026-07-23-flowgency-data-root-setup.md"
git mv "docs/superpowers/specs/2026-07-23-$previous-data-root-setup-design.md" "docs/superpowers/specs/2026-07-23-flowgency-data-root-setup-design.md"
```

Re-run the path test after each move group.

- [ ] **Step 4: Perform the controlled content sweep**

Use editor replacements over the tracked files reported by the gate. Construct
the source terms from fragments and map only brand identities:

```python
previous_product = "".join(("A", "gency"))
previous_namespace = previous_product.casefold()
previous_creator = "".join(("Chris", "tag"))
previous_distribution = f"{previous_creator.casefold()}-{previous_namespace}"
previous_module = previous_distribution.replace("-", "_")
previous_repository = f"{previous_creator.casefold()}/{previous_namespace}"
replacements = {
    previous_repository: "Blackhex/flowgency",
    previous_distribution: "flowgency",
    previous_module: "flowgency",
    previous_product: "Flowgency",
    previous_product.upper(): "FLOWGENCY",
    previous_namespace: "flowgency",
    previous_creator: "Blackhex",
    previous_creator.casefold(): "blackhex",
}
```

Apply this mapping in the shown order, from compound identities to individual
terms. This prevents an executable, distribution, or repository URL from being
rewritten into a mixed owner/product name.

Review every diff hunk. Do not replace `agent`, `agents`, `agent_library`, or
`AGENTS.md`. In dated plans/specs, preserve technical meaning and old numeric
schema examples where chronology requires them, but use Flowgency identifiers
and neutral phrases such as `superseded schema`.

- [ ] **Step 5: Re-run naming and repository boundary gates**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_naming.py tests/test_repository_boundaries.py -q
```

Expected: all pass with no exclusions added.

- [ ] **Step 6: Inspect the final manifest and diff**

Run:

```powershell
git ls-files | Select-String -Pattern 'flowgency' | Measure-Object
git status --short
git diff --check
```

Then review `git diff --word-diff` for accidental changes to generic agent
terminology, URLs, code examples, and schema numbers.

- [ ] **Step 7: Run the Python suite after the exhaustive sweep**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: all Python tests pass. The count must be at least the baseline plus
the new namespace, branding, and naming tests; the six capability skips may
remain.

- [ ] **Step 8: Commit the exhaustive sweep**

```powershell
git add -A
git commit -m "refactor: complete flowgency terminology sweep"
```

---

### Task 9: Clean-package Verification, Review, And Integration

**Files:**
- Modify only files required by concrete review findings attributable to this rebrand
- Do not commit `dist/`, `.venv/`, `node_modules/`, `test-results/`, `playwright-report/`, or generated metadata directories

**Interfaces:**
- Consumes: all implementation tasks.
- Produces: a reviewed Flowgency wheel, smoke-tested executable/imports, complete verification evidence, and a fast-forwarded published `master` when every integration gate is green.

- [ ] **Step 1: Build and inspect a clean wheel**

Run:

```powershell
Remove-Item dist -Recurse -Force -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pip wheel --no-deps --wheel-dir dist .
.\.venv\Scripts\python.exe -c "from pathlib import Path; from zipfile import ZipFile; wheel=next(Path('dist').glob('flowgency-*.whl')); names=ZipFile(wheel).namelist(); previous=''.join(('a','gency')); assert any(n.startswith('flowgency/') for n in names); assert not any(n.startswith(previous + '/') for n in names); assert any('flowgency-setup/SKILL.md' in n for n in names)"
```

Expected: one `flowgency-*.whl`, only the target package namespace, and the
packaged setup skill.

- [ ] **Step 2: Smoke-test in a clean temporary environment**

Run:

```powershell
$smoke = Join-Path $env:TEMP 'flowgency-wheel-smoke'
Remove-Item $smoke -Recurse -Force -ErrorAction SilentlyContinue
python -m venv $smoke
& "$smoke\Scripts\python.exe" -m pip install (Get-ChildItem dist\flowgency-*.whl | Select-Object -First 1).FullName
Push-Location $env:TEMP
try {
  & "$smoke\Scripts\python.exe" -c "import flowgency; import flowgency.cli"
  & "$smoke\Scripts\flowgency.exe" --help
  $previousExe = (-join ('chris','tag','-','a','gency.exe'))
  if (Test-Path "$smoke\Scripts\$previousExe") { throw 'unexpected previous executable' }
} finally {
  Pop-Location
}
```

Expected: import and help succeed outside the repository; no previous
executable exists in the clean environment.

- [ ] **Step 3: Run final Python verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_flowgency_namespace.py tests/test_flowgency_branding.py tests/test_flowgency_naming.py tests/test_repository_boundaries.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 4: Run final UI verification and compare with baseline**

Run:

```powershell
npm run test:ui
```

Expected for rebrand-owned coverage: every snapshot, navigation, interaction,
and dark-mode accessibility test passes. The pre-existing light Dashboard
contrast findings may remain exactly as recorded in Global Constraints. Any
new failure, changed diagnostic, or additional skip is a regression.

If those two baseline findings remain, do not change unrelated colors in this
branch and do not claim a fully green UI suite. Record the unchanged blocker
and stop before repository integration, because repository policy requires a
green complete suite.

- [ ] **Step 5: Perform whole-branch review**

Use the requesting-code-review skill against the merge base through branch tip.
Review specifically for:

```text
- accidental compatibility aliases
- missed config-root or schema-version consumers
- stale runtime paths or environment variables
- altered permission, job, memory, prompt, or dispatch semantics
- missing setup-skill package files or broken symlinks
- icon drift from the approved source
- board functionality accidentally introduced by README positioning
- previous brand terms hidden in paths, symlink targets, or dated documents
```

Fix only confirmed findings, rerun their focused checks, and commit each
approved fix separately.

- [ ] **Step 6: Verify repository cleanliness**

Run:

```powershell
git diff --check master...HEAD
git status --short --branch
git log --oneline master..HEAD
```

Expected: no tracked working-tree changes, no ignored/generated files staged,
and separate documentation, plan, implementation, and review-fix commits.

- [ ] **Step 7: Integrate automatically when all gates are green**

Follow repository policy without presenting merge choices:

```powershell
git fetch origin
```

If `master` moved, rebase the feature branch onto it and rerun the complete
Python and UI suites. In the main checkout, stash any uncommitted changes,
including the user's CLI contract newline fix, with a descriptive stash name.
Then fast-forward only:

```powershell
git switch master
git merge --ff-only feat/flowgency-rebrand
```

Restore the user's stash, rerun the complete suites on fast-forwarded `master`,
push both `master` and `feat/flowgency-rebrand` to `origin`, remove
`.worktrees/flowgency-rebrand`, and run `git worktree prune`. Keep the feature
branch unless the user explicitly requests deletion.

- [ ] **Step 8: Restore the surviving editable install**

After worktree cleanup, from the main checkout run:

```powershell
python -m pip install -e .
python -m pip show flowgency
```

Expected: `Editable project location` points to the surviving main checkout,
and `flowgency --help` succeeds from outside the repository.