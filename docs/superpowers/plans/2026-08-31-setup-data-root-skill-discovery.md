# Setup Data Root And Skill Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Launch first-run setup from the selected Agency data root and make the bundled `agency-setup` skill discoverable to Copilot in editable and wheel installations.

**Architecture:** Move the canonical setup skill beneath `agency/setup_assets` and expose its Copilot discovery root through package data. The setup form prepares one safe writable data root, passes it through a renamed immutable request, and Copilot launches with that root as cwd and `-C` while attaching the package-owned discovery root through `--add-dir`. The guided prompt and skill then ask for the first group project workspace, while manual skill invocation retains a root-first fallback.

**Tech Stack:** Python 3.13, setuptools package data, FastAPI/Jinja2, GitHub Copilot CLI, pytest, standard-library `zipfile`, Playwright browser tooling. No new third-party runtime dependency.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-08-31-setup-data-root-skill-discovery-design.md` at commit `797b238`.
- Work only in `C:/Projekty/christag-agency/.worktrees/setup-data-root-skill-discovery` on `fix/setup-data-root-skill-discovery` until the integration task.
- Use `.venv\Scripts\python.exe` in the feature worktree. Baseline: **1966 passed, 5 skipped, 0 failed**.
- Do not run an individual `real_runtime` test. Those tests can launch external AI CLIs and consume credits; the normal full suite handles its configured skips.
- Keep `schema_version: 5`. Do not add `agency.data_root`; the data root is launch input and the existing derived paths remain config authority.
- The browser selects the Agency data root. The guided conversation selects the first group's project workspace.
- Exact UI term: `Agency data root`. Exact Windows placeholder: `C:\Agency`. Do not retain a `project_dir` compatibility alias in the setup boundary.
- Before conversational approval, create only the selected data root and missing ordinary parents. Do not create `agent-library`, `compiled-agents`, `memory`, `prompts`, `groups`, blueprints, or config.
- The selected root remains after a failed launch and is revalidated idempotently on relaunch. Never remove it automatically.
- Package the canonical skill; do not copy it into the data root, inline it in the launch prompt, or install it globally.
- Preserve both repository discovery paths as relative Git symlinks with mode `120000`. Never commit an absolute local target.
- Do not add runtime directory-shape loaders, native-file authority, startup conversion, or a second canonical skill copy.
- Preserve setup polling, complete config validation, revision checking, and the one atomic config replacement.
- Do not modify or stage `config.yaml`, `config.yaml.lock`, group state, logs, `.venv`, build output, or browser smoke artifacts.
- Use Conventional Commits with lowercase imperative subjects no longer than 72 characters.
- After implementation, invoke `superpowers:requesting-code-review`; after review and green tests, follow `AGENTS.md` automatically: rebase if needed, fast-forward `master`, retest, push both branches, remove the worktree, and retain the feature branch.

## File Structure

### New Package-Owned Files

- `agency/setup_assets/__init__.py` — stable API for locating bundled setup resources.
- `agency/setup_assets/copilot/.github/skills/agency-setup/` — canonical `SKILL.md` and `references/*.md`, moved from `skills/agency-setup/`.
- `tests/test_setup_assets.py` — repository-link and built-wheel package coverage.

### Repository Discovery Links

- `skills/agency-setup` — relative symlink to the package-owned canonical skill.
- `.github/skills/agency-setup` — relative symlink to the same canonical skill.

### Existing Production Files

- `pyproject.toml` — include the complete canonical skill tree as package data.
- `agency/configuration/paths.py` — shared real-directory detection and writable-directory preparation.
- `agency/configuration/__init__.py` — export the setup-root preparation API.
- `agency/integrations/models.py` — rename `InteractiveSetupRequest.project_dir` to `data_root`.
- `agency/integrations/agency/copilot.py` — validate/attach the bundled skill and launch from the data root.
- `agency/web/setup_flow.py` — guided data-root context and project-workspace-first prompt.
- `agency/web/routes/admin_groups.py` — prepare the selected root and handle launch/fallback failures.
- `agency/templates/setup.html` — Agency data-root labels, fields, picker copy, and waiting summary.
- `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md` — guided/manual question-order branches.
- `README.md`, `kb/getting-started.md`, `kb/setup-skill.md` — active first-run documentation.

### Existing Tests

- `tests/test_path_validation.py` — writable-root preparation behavior.
- `tests/test_setup_flow.py` — exact guided prompt context and ordering.
- `tests/test_server.py`, `tests/test_group_settings.py` — form, root preparation, request, waiting, and fallback behavior.
- `tests/test_interactive_setup.py` — immutable request rename and Copilot command/discovery checks.
- `tests/test_agency_setup_skill.py`, `tests/test_surface_contracts.py` — guided/manual skill and active documentation contracts.

## Shared Interfaces

Task 1 produces:

```python
# agency/setup_assets/__init__.py
def copilot_discovery_root() -> Path:
    """Return the stable package-owned root passed to Copilot --add-dir."""
```

Task 2 produces:

```python
# agency/configuration/paths.py
class DirectoryPreparationError(ValueError):
    """A requested writable directory could not be prepared safely."""

def is_symlink_or_reparse(path: Path) -> bool:
    """Return whether the path entry itself is a symlink or reparse point."""

def prepare_writable_directory(path: Path, *, label: str) -> Path:
    """Create if needed, revalidate, and return a strict real writable path."""
```

Task 3 produces:

```python
# agency/integrations/models.py
@dataclass(frozen=True)
class InteractiveSetupRequest:
    data_root: Path
    config_path: Path
    prompt: str

# agency/web/setup_flow.py
def build_setup_prompt(
    data_root: Path,
    config_path: Path,
    *,
    selected_integration: str,
) -> str: ...

def launchable_integrations(
    integrations: Mapping[str, BaseIntegration],
    data_root: Path,
) -> tuple[BaseIntegration, ...]: ...
```

Task 4 consumes all three interfaces and adds one private Copilot boundary:

```python
# agency/integrations/agency/copilot.py
def _interactive_setup_discovery_root(self, data_root: Path) -> Path:
    """Validate the bundled skill and reject a shadowing local skill."""
```

---

### Task 1: Package The Canonical Setup Skill

**Files:**
- Create: `agency/setup_assets/__init__.py`
- Move: `skills/agency-setup/SKILL.md` to `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md`
- Move: `skills/agency-setup/references/*.md` to `agency/setup_assets/copilot/.github/skills/agency-setup/references/*.md`
- Replace with symlink: `skills/agency-setup`
- Replace symlink: `.github/skills/agency-setup`
- Modify: `pyproject.toml`
- Modify: `tests/test_agency_setup_skill.py`
- Create: `tests/test_setup_assets.py`

**Interfaces:**
- Consumes: current canonical skill files and setuptools package discovery.
- Produces: `copilot_discovery_root() -> Path`; one package-owned canonical skill; two repository symlinks resolving to it; wheel entries for every canonical regular file.

- [ ] **Step 1: Write the failing resource-location and wheel tests**

Change the constants at the top of `tests/test_agency_setup_skill.py` so all existing contract tests identify the future canonical path:

```python
from agency.setup_assets import copilot_discovery_root


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = (
    copilot_discovery_root() / ".github" / "skills" / "agency-setup"
)
REPOSITORY_SKILL_DIR = REPO_ROOT / "skills" / "agency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "agency-setup"
```

Replace the existing discovery test with:

```python
def test_repository_skill_paths_resolve_to_package_owned_source():
    canonical = CANONICAL_SKILL_DIR.resolve(strict=True)

    assert REPOSITORY_SKILL_DIR.resolve(strict=True) == canonical
    assert DISCOVERY_SKILL_DIR.resolve(strict=True) == canonical
```

Create `tests/test_setup_assets.py`:

```python
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from agency.setup_assets import copilot_discovery_root


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = (
    REPO_ROOT
    / "agency"
    / "setup_assets"
    / "copilot"
    / ".github"
    / "skills"
    / "agency-setup"
)


def test_copilot_discovery_root_is_package_owned():
    assert copilot_discovery_root() == (
        REPO_ROOT / "agency" / "setup_assets" / "copilot"
    ).resolve()


def test_wheel_contains_every_canonical_setup_skill_file(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("christag_agency-*.whl"))
    assert len(wheels) == 1

    expected = {
        path.relative_to(REPO_ROOT).as_posix(): path.read_bytes()
        for path in CANONICAL_SKILL_DIR.rglob("*")
        if path.is_file()
    }
    assert expected

    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert set(expected) <= names
        for name, content in expected.items():
            assert archive.read(name) == content
```

- [ ] **Step 2: Run the tests to verify the package API is absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_setup_assets.py tests/test_agency_setup_skill.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'agency.setup_assets'`.

- [ ] **Step 3: Move the source and create the package locator**

From the worktree root, run:

```powershell
New-Item -ItemType Directory -Force agency\setup_assets\copilot\.github\skills | Out-Null
git mv skills/agency-setup agency/setup_assets/copilot/.github/skills/agency-setup
Remove-Item .github\skills\agency-setup
New-Item -ItemType SymbolicLink -Path skills\agency-setup -Target ..\agency\setup_assets\copilot\.github\skills\agency-setup | Out-Null
New-Item -ItemType SymbolicLink -Path .github\skills\agency-setup -Target ..\..\agency\setup_assets\copilot\.github\skills\agency-setup | Out-Null
git add skills/agency-setup .github/skills/agency-setup agency/setup_assets/copilot
git ls-files -s -- skills/agency-setup .github/skills/agency-setup
Resolve-Path skills\agency-setup, .github\skills\agency-setup
```

The exact stored targets are:

```text
skills/agency-setup
  -> ../agency/setup_assets/copilot/.github/skills/agency-setup

.github/skills/agency-setup
  -> ../../agency/setup_assets/copilot/.github/skills/agency-setup
```

Use Windows symbolic links, not junctions or copied directories. Verify before
commit that both entries have Git mode `120000` and that both
`Resolve-Path` results equal the canonical package directory.

Create `agency/setup_assets/__init__.py`:

```python
from __future__ import annotations

from pathlib import Path


def copilot_discovery_root() -> Path:
    """Return the stable package-owned root passed to Copilot --add-dir."""
    return (Path(__file__).resolve().parent / "copilot").resolve()
```

- [ ] **Step 4: Declare the hidden skill tree as package data**

Extend `pyproject.toml` without changing the existing `agency` entry:

```toml
[tool.setuptools.package-data]
agency = ["templates/*.html", "static/*", "themes/*.yaml"]
"agency.setup_assets" = [
    "copilot/.github/skills/agency-setup/*.md",
    "copilot/.github/skills/agency-setup/references/*.md",
]
```

The explicit `.github` patterns are required because hidden path components
must not depend on wildcard discovery.

- [ ] **Step 5: Run package, skill, and surface tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_setup_assets.py tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py tests/test_surface_contracts.py -q
git ls-files -s -- skills/agency-setup .github/skills/agency-setup
```

Expected: all tests pass; both repository paths are listed with mode `120000`;
the wheel comparison includes `SKILL.md` and all three reference documents.

- [ ] **Step 6: Commit the packaged canonical source**

Run:

```powershell
git add pyproject.toml agency/setup_assets skills/agency-setup .github/skills/agency-setup tests/test_setup_assets.py tests/test_agency_setup_skill.py
git commit -m "feat(setup): package the canonical setup skill"
```

Expected: one commit containing the move, package declaration, locator, links,
and package regression tests; no duplicate regular-file skill tree remains.

---

### Task 2: Prepare Safe Writable Data Roots

**Files:**
- Modify: `agency/configuration/paths.py`
- Modify: `agency/configuration/__init__.py`
- Modify: `tests/test_path_validation.py`

**Interfaces:**
- Consumes: existing `_nearest_existing_parent()` and symlink/reparse stat logic.
- Produces: `DirectoryPreparationError`, `is_symlink_or_reparse()`, and `prepare_writable_directory(path, *, label) -> Path` for Tasks 3 and 4.

- [ ] **Step 1: Write the failing preparation tests**

Update the imports in `tests/test_path_validation.py`:

```python
from agency.configuration.paths import (
    DirectoryPreparationError,
    job_store_root,
    prepare_writable_directory,
    validate_resolved_paths,
)
```

Add these tests beside the existing path-safety cases:

```python
def test_prepare_writable_directory_creates_only_requested_root(tmp_path):
    root = tmp_path / "new" / "Agency"

    resolved = prepare_writable_directory(root, label="Agency data root")

    assert resolved == root.resolve(strict=True)
    assert resolved.is_dir()
    assert list(resolved.iterdir()) == []


def test_prepare_writable_directory_revalidates_existing_root(tmp_path):
    root = tmp_path / "Agency"
    root.mkdir()
    marker = root / "user-content.txt"
    marker.write_text("keep\n", encoding="utf-8")

    first = prepare_writable_directory(root, label="Agency data root")
    second = prepare_writable_directory(root, label="Agency data root")

    assert first == second == root.resolve(strict=True)
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_prepare_writable_directory_requires_absolute_path():
    with pytest.raises(
        DirectoryPreparationError,
        match="Agency data root must be an absolute path",
    ):
        prepare_writable_directory(Path("relative/Agency"), label="Agency data root")


def test_prepare_writable_directory_rejects_file(tmp_path):
    root = tmp_path / "Agency"
    root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        DirectoryPreparationError,
        match="Agency data root must be a directory",
    ):
        prepare_writable_directory(root, label="Agency data root")


def test_prepare_writable_directory_rejects_link_or_reparse(
    tmp_path, monkeypatch
):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "Agency"
    _make_hostile_directory_entry(root, target, monkeypatch)

    with pytest.raises(
        DirectoryPreparationError,
        match="symlink or reparse point",
    ):
        prepare_writable_directory(root, label="Agency data root")


def test_prepare_writable_directory_rejects_unwritable_parent(
    tmp_path, monkeypatch
):
    parent = tmp_path / "locked"
    parent.mkdir()
    root = parent / "Agency"
    original_access = os.access
    monkeypatch.setattr(
        "agency.configuration.paths.os.access",
        lambda path, mode: False
        if Path(path) == parent and mode & os.W_OK
        else original_access(path, mode),
    )

    with pytest.raises(
        DirectoryPreparationError,
        match="No writable real parent can create Agency data root",
    ):
        prepare_writable_directory(root, label="Agency data root")

    assert not root.exists()


def test_prepare_writable_directory_rejects_inaccessible_existing_root(
    tmp_path, monkeypatch
):
    root = tmp_path / "Agency"
    root.mkdir()
    original_access = os.access
    monkeypatch.setattr(
        "agency.configuration.paths.os.access",
        lambda path, mode: False
        if Path(path) == root and mode == os.R_OK | os.W_OK
        else original_access(path, mode),
    )

    with pytest.raises(
        DirectoryPreparationError,
        match="Agency data root is not readable and writable",
    ):
        prepare_writable_directory(root, label="Agency data root")
```

- [ ] **Step 2: Run the focused tests to verify the public API is absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_path_validation.py -q
```

Expected: collection fails because `DirectoryPreparationError` and
`prepare_writable_directory` are not exported yet.

- [ ] **Step 3: Promote real-entry detection and implement preparation**

Rename `_is_symlink_or_reparse` to `is_symlink_or_reparse` and update every
call in `agency/configuration/paths.py`. Add:

```python
class DirectoryPreparationError(ValueError):
    """A requested writable directory could not be prepared safely."""


def prepare_writable_directory(path: Path, *, label: str) -> Path:
    """Create if needed, revalidate, and return a strict real writable path."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise DirectoryPreparationError(f"{label} must be an absolute path.")

    try:
        if is_symlink_or_reparse(candidate):
            raise DirectoryPreparationError(
                f"{label} must be a real directory, not a symlink or reparse point: "
                f"{candidate}"
            )
        if candidate.exists() and not candidate.is_dir():
            raise DirectoryPreparationError(
                f"{label} must be a directory: {candidate}"
            )
        if not candidate.exists():
            parent = _nearest_existing_parent(candidate)
            if (
                parent is None
                or is_symlink_or_reparse(parent)
                or not parent.is_dir()
                or not os.access(parent, os.W_OK)
            ):
                raise DirectoryPreparationError(
                    f"No writable real parent can create {label}: {candidate}"
                )
            candidate.mkdir(parents=True, exist_ok=True)

        if is_symlink_or_reparse(candidate):
            raise DirectoryPreparationError(
                f"{label} must be a real directory, not a symlink or reparse point: "
                f"{candidate}"
            )
        resolved = candidate.resolve(strict=True)
    except DirectoryPreparationError:
        raise
    except OSError as exc:
        raise DirectoryPreparationError(
            f"Could not create or inspect {label}: {candidate}"
        ) from exc

    if not resolved.is_dir():
        raise DirectoryPreparationError(f"{label} must be a directory: {resolved}")
    if not os.access(resolved, os.R_OK | os.W_OK):
        raise DirectoryPreparationError(
            f"{label} is not readable and writable: {resolved}"
        )
    return resolved
```

Export all three public names from `agency/configuration/__init__.py`.

- [ ] **Step 4: Run path and config regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_path_validation.py tests/test_config.py tests/test_config_store.py -q
```

Expected: all preparation and existing config path tests pass. The first test
proves that no derived data-root child is created.

- [ ] **Step 5: Commit the path primitive**

Run:

```powershell
git add agency/configuration/paths.py agency/configuration/__init__.py tests/test_path_validation.py
git commit -m "feat(paths): prepare writable setup roots"
```

---

### Task 3: Make The Setup Boundary Data-Root Native

**Files:**
- Modify: `agency/integrations/models.py`
- Modify: `agency/integrations/agency/copilot.py`
- Modify: `agency/web/setup_flow.py`
- Modify: `agency/web/routes/admin_groups.py`
- Modify: `agency/templates/setup.html`
- Modify: `tests/test_setup_flow.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_group_settings.py`
- Modify: `tests/test_interactive_setup.py`

**Interfaces:**
- Consumes: `prepare_writable_directory(path, label="Agency data root")` from Task 2 and unchanged integration launch methods.
- Produces: strict `InteractiveSetupRequest.data_root`; a guided prompt with explicit mode/root/config/integration lines; data-root form and waiting state; deterministic handling when both launch and fallback construction fail.

- [ ] **Step 1: Write the failing guided-prompt and form-contract tests**

Replace the two old root-first prompt tests in `tests/test_setup_flow.py` with:

```python
def test_build_setup_prompt_supplies_guided_data_root_context(tmp_path: Path):
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    config_path = tmp_path / "config.yaml"

    prompt = build_setup_prompt(
        data_root,
        config_path,
        selected_integration="copilot",
    )

    for line in (
        "Setup mode: guided-first-run.",
        f"Agency data root: {data_root.resolve()}.",
        f"Authoritative config: {config_path.resolve()}.",
        "Selected integration: copilot.",
    ):
        assert line in prompt
    assert "The Agency data root was selected in the browser; do not ask for it again." in prompt
    assert (
        "Ask for the first group project workspace as the first user-facing question."
        in prompt
    )
    assert "Project workspace:" not in prompt


def test_build_setup_prompt_keeps_derived_path_approval(tmp_path: Path):
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    prompt = build_setup_prompt(
        data_root,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    for phrase in (
        "agency.agent_library as <root>/agent-library",
        "agency.compilation_cache as <root>/compiled-agents",
        "agency.memory_store as <root>/memory",
        "agency.prompt_store as <root>/prompts",
        "groups.<group-id>.path as <root>/groups/<group-id>",
        "Customize the derived storage paths?",
        "review all five derived paths together",
        "consolidated path summary",
        "before creating any derived directory or blueprint",
    ):
        assert phrase in prompt
```

In `tests/test_server.py`, rename the setup form test and require exact data-root
semantics:

```python
def test_setup_get_renders_only_data_root_and_integration_fields(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "agency.web.routes.admin_groups.launchable_integrations",
        lambda integrations, data_root: (
            _LaunchIntegration("copilot", "GitHub Copilot"),
        ),
    )
    response = TestClient(app_mod.app).get("/setup")

    assert response.status_code == 200
    assert "Agency data root" in response.text
    assert "Project folder" not in response.text
    assert 'name="data_root"' in response.text
    assert 'name="project_dir"' not in response.text
    assert 'placeholder="C:\\Agency"' in response.text
    assert 'id="browse-data-root"' in response.text
    assert "Choose Agency data root" in response.text
    assert "Agency data root selected." in response.text
```

Make the corresponding launcher-field/form assertions in
`tests/test_group_settings.py` exact:

```python
assert "Agency data root" in response.text
assert "Project folder" not in response.text
assert 'name="data_root"' in response.text
assert 'name="project_dir"' not in response.text

# In test_setup_form_posts_only_launcher_inputs:
assert "data_root" in inputs
assert "project_dir" not in inputs
assert "group_key" not in inputs
assert "expected_revision" not in inputs
```

- [ ] **Step 2: Write failing root-creation and fallback-state route tests**

Extend `_LaunchIntegration` in `tests/test_server.py` with
`fallback_error: Exception | None = None`, and raise it from
`interactive_setup_fallback_command()` before returning the command. Add:

```python
def test_setup_launch_creates_and_uses_missing_data_root(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "new" / "Agency"
    integration = _LaunchIntegration()
    monkeypatch.setattr(
        "agency.web.routes.admin_groups.launchable_integrations",
        lambda integrations, root: (integration,),
    )
    client = TestClient(app_mod.app)

    response = client.post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot"},
    )

    assert response.status_code == 200
    assert data_root.is_dir()
    assert list(data_root.iterdir()) == []
    assert not config_path.exists()
    assert integration.requests[0].data_root == data_root.resolve(strict=True)
    assert "Waiting for setup to complete" in response.text
    assert "Agency data root" in response.text


def test_setup_launch_rejects_relative_data_root(tmp_path, monkeypatch):
    _configure_missing_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "agency.web.routes.admin_groups.launchable_integrations",
        lambda integrations, root: (_LaunchIntegration(),),
    )
    response = TestClient(app_mod.app).post(
        "/setup/launch",
        data={"data_root": "relative/Agency", "integration": "copilot"},
    )

    assert response.status_code == 200
    assert "Agency data root must be an absolute path." in response.text


def test_setup_launch_returns_to_form_when_launch_and_fallback_fail(
    tmp_path, monkeypatch
):
    _configure_missing_config(tmp_path, monkeypatch)
    data_root = tmp_path / "Agency"
    integration = _LaunchIntegration(
        error=IntegrationError("Bundled setup skill is unavailable."),
        fallback_error=IntegrationError("No valid fallback command."),
    )
    monkeypatch.setattr(
        "agency.web.routes.admin_groups.launchable_integrations",
        lambda integrations, root: (integration,),
    )

    response = TestClient(app_mod.app).post(
        "/setup/launch",
        data={"data_root": str(data_root), "integration": "copilot"},
    )

    assert response.status_code == 200
    assert data_root.is_dir()
    assert "Bundled setup skill is unavailable." in response.text
    assert "Waiting for setup to complete" not in response.text
```

In the existing unavailable-integration, config-preservation, successful launch,
and launch-failure tests, use this request and immutable-model pattern:

```python
data_root = tmp_path / "Agency"
data_root.mkdir()
response = client.post(
    "/setup/launch",
    data={"data_root": str(data_root.resolve()), "integration": "copilot"},
)

assert integration.requests[0].data_root == data_root.resolve()
assert not hasattr(integration.requests[0], "project_dir")
```

Keep each test's existing integration name, fallback command, config-byte, and
waiting-state assertions around this changed field contract.

- [ ] **Step 3: Run the setup tests to verify old semantics fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_setup_flow.py tests/test_server.py tests/test_group_settings.py -q
```

Expected: failures name the old prompt, `project_dir` field, existing-directory
requirement, and false waiting behavior.

- [ ] **Step 4: Rename the immutable request and setup-flow parameters**

Change `InteractiveSetupRequest` in `agency/integrations/models.py`:

```python
@dataclass(frozen=True)
class InteractiveSetupRequest:
    data_root: Path
    config_path: Path
    prompt: str
```

In setup-specific sections of `agency/integrations/agency/copilot.py`, replace
`request.project_dir` with `request.data_root` and name the local value
`data_root`. Do not rename `project_dir` locals in generic terminal-spawn tests
or unrelated project execution code.

Rename `launchable_integrations(..., project_dir)` to
`launchable_integrations(..., data_root)` and use `resolved_data_root` only for
integration detection.

- [ ] **Step 5: Replace the setup prompt with the guided handoff**

Replace `build_setup_prompt` with a data-root signature. Its opening and retained
workflow contract must be:

```python
def build_setup_prompt(
    data_root: Path,
    config_path: Path,
    *,
    selected_integration: str,
) -> str:
    return (
        "Use the agency-setup skill to configure Agency.\n"
        "Setup mode: guided-first-run.\n"
        f"Agency data root: {data_root.resolve(strict=True)}.\n"
        f"Authoritative config: {config_path.resolve()}.\n"
        f"Selected integration: {selected_integration}.\n"
        "The Agency data root was selected in the browser; do not ask for it again. "
        "Ask for the first group project workspace as the first user-facing question. "
        "After the user selects it, inspect that project read-only before discussing "
        "the team. The project workspace remains source and execution context, the "
        "Agency data root remains Agency-owned storage, and the authoritative config "
        "remains at the supplied path. "
        "Use the selected integration for group.default_integration and the initial "
        "agent instances unless the user explicitly approves a different registered "
        "integration. By default derive agency.agent_library as <root>/agent-library, "
        "agency.compilation_cache as <root>/compiled-agents, agency.memory_store as "
        "<root>/memory, agency.prompt_store as <root>/prompts, and "
        "groups.<group-id>.path as <root>/groups/<group-id>. Configure "
        "schema_version: 5. Set each group workspace_path to its approved project "
        "execution workspace and path to a disjoint Agency-owned group root. Never "
        "create or reference a project-local shared directory. After the group ID is "
        "approved, ask `Customize the derived storage paths?` once. Only if accepted, "
        "review all five derived paths together; otherwise do not ask about individual "
        "storage paths. Show one consolidated path summary and obtain approval before "
        "creating any derived directory or blueprint. Discuss and obtain approval for "
        "the group name, storage paths, agent team, integrations, routines, runtime "
        "policy, workspaces, and memory. Perform validation on the final config and "
        "make one atomic write for one complete configuration. Do not write a partial "
        "configuration."
    )
```

- [ ] **Step 6: Prepare the root and make fallback failure explicit**

In `agency/web/routes/admin_groups.py`:

1. Rename `_setup_project_seed` to `_setup_data_root_seed` and all
   `project_dir_value` context/form locals to `data_root_value`.
2. Build and validate `launchable_by_name` before creating filesystem state.
3. Call:

```python
try:
    resolved_data_root = prepare_writable_directory(
        Path(data_root_value),
        label="Agency data root",
    )
except DirectoryPreparationError as exc:
    return _setup_response(
        request,
        services,
        status=status,
        data_root_value=data_root_value,
        integrations=integrations,
        selected_integration=selected_integration,
        selected_integration_name=selected_integration_name,
        error=str(exc),
    )
```

4. Construct `InteractiveSetupRequest(data_root=resolved_data_root, ...)`.
5. Preserve a valid integration-owned fallback after spawn failure. If fallback
   construction also raises, return the non-waiting setup form with the original
   launch error:

```python
except Exception as launch_error:
    launch_notice = str(launch_error).strip() or "Interactive setup could not be launched."
    try:
        fallback_command = integration.interactive_setup_fallback_command(
            setup_request
        )
    except Exception:
        return _setup_response(
            request,
            services,
            status=status,
            data_root_value=str(resolved_data_root),
            integrations=integrations,
            selected_integration=requested_integration,
            selected_integration_name=launchable_by_name[
                requested_integration
            ].display_name,
            error=launch_notice,
        )
```

Do not delete `resolved_data_root` on either path.

- [ ] **Step 7: Rename the setup template contract and copy**

In `agency/templates/setup.html`, make these exact replacements across initial,
waiting, and relaunch states:

```text
Pick the Agency data root and launch-capable integration, then continue setup in an interactive session.
Agency data root
C:\Agency
Choose Agency data root
Agency data root selected.
```

Use `data_root_value`, `name="data_root"`, `id="data_root"`,
`id="browse-data-root"`, and JavaScript local `dataRootInput`. Change the waiting
summary heading from `Project` to `Agency data root`. Preserve existing layout,
directory listing behavior, status polling, and button styling.

- [ ] **Step 8: Update request construction tests and run the focused slice**

In `tests/test_interactive_setup.py`, change only setup requests and assertions
from `project_dir` to `data_root`; leave generic `spawn_interactive_terminal`
fixture names unchanged. At this task boundary Copilot command expectations still
have `-C <data-root>` followed directly by `-i`; Task 4 adds `--add-dir`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_path_validation.py tests/test_setup_flow.py tests/test_server.py tests/test_group_settings.py tests/test_interactive_setup.py -q
```

Expected: all tests pass; a missing absolute root is created empty; relative
input and double launch/fallback failure stay on the form; no setup-specific
`project_dir` reference remains.

- [ ] **Step 9: Commit the data-root setup boundary**

Run:

```powershell
git add agency/integrations/models.py agency/integrations/agency/copilot.py agency/web/setup_flow.py agency/web/routes/admin_groups.py agency/templates/setup.html tests/test_setup_flow.py tests/test_server.py tests/test_group_settings.py tests/test_interactive_setup.py
git commit -m "feat(setup): launch from the Agency data root"
```

---

### Task 4: Attach And Validate The Packaged Copilot Skill

**Files:**
- Modify: `agency/integrations/agency/copilot.py`
- Modify: `tests/test_interactive_setup.py`

**Interfaces:**
- Consumes: `copilot_discovery_root()`, `is_symlink_or_reparse()`, and `InteractiveSetupRequest.data_root`.
- Produces: `_interactive_setup_discovery_root(data_root) -> Path`; Copilot setup argv containing `--add-dir`; actionable package and local-shadow errors before terminal spawn.

- [ ] **Step 1: Write failing command and package-validation tests**

Import the package locator in `tests/test_interactive_setup.py`:

```python
from agency.setup_assets import copilot_discovery_root
```

Update the direct, fallback, and PowerShell-wrapper command expectations so the
setup-specific argv order is exactly:

```python
(
    "-C",
    str(data_root.resolve()),
    "--add-dir",
    str(copilot_discovery_root()),
    "-i",
    "Use the agency-setup skill.",
    "--name",
    "Agency setup",
)
```

Add `import re` and these tests:

```python
def test_copilot_rejects_missing_packaged_setup_skill(monkeypatch, tmp_path):
    import agency.integrations.agency.copilot as copilot_mod

    missing = tmp_path / "missing-discovery-root"
    monkeypatch.setattr(copilot_mod, "copilot_discovery_root", lambda: missing)
    integration = CopilotIntegration()
    request = InteractiveSetupRequest(
        data_root=tmp_path,
        config_path=tmp_path / "config.yaml",
        prompt="Use the agency-setup skill.",
    )

    with pytest.raises(IntegrationError, match="reinstall christag-agency"):
        integration.interactive_setup_fallback_command(request)


def test_copilot_rejects_shadowing_data_root_skill(monkeypatch, tmp_path):
    data_root = tmp_path / "Agency"
    local_skill = data_root / ".github" / "skills" / "agency-setup"
    local_skill.mkdir(parents=True)
    (local_skill / "SKILL.md").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(
        CopilotIntegration,
        "_interactive_setup_command_prefix",
        lambda self: ("copilot",),
    )
    integration = CopilotIntegration()
    request = InteractiveSetupRequest(
        data_root=data_root,
        config_path=tmp_path / "config.yaml",
        prompt="Use the agency-setup skill.",
    )

    with pytest.raises(IntegrationError, match=re.escape(str(local_skill))):
        integration.interactive_setup_fallback_command(request)


def test_copilot_accepts_repository_link_to_canonical_skill(monkeypatch):
    repository_root = Path(__file__).parents[1]
    monkeypatch.setattr(
        CopilotIntegration,
        "_interactive_setup_command_prefix",
        lambda self: ("copilot",),
    )
    request = InteractiveSetupRequest(
        data_root=repository_root,
        config_path=repository_root / "config.yaml",
        prompt="Use the agency-setup skill.",
    )

    command = CopilotIntegration()._interactive_setup_command(request)

    assert command[1:5] == (
        "-C",
        str(repository_root.resolve()),
        "--add-dir",
        str(copilot_discovery_root()),
    )


def test_copilot_rejects_unreadable_packaged_setup_skill(monkeypatch, tmp_path):
    import agency.integrations.agency.copilot as copilot_mod

    skill_file = (
        copilot_discovery_root()
        / ".github"
        / "skills"
        / "agency-setup"
        / "SKILL.md"
    )
    original_access = copilot_mod.os.access
    monkeypatch.setattr(
        copilot_mod.os,
        "access",
        lambda path, mode: False
        if Path(path) == skill_file and mode == copilot_mod.os.R_OK
        else original_access(path, mode),
    )
    request = InteractiveSetupRequest(
        data_root=tmp_path,
        config_path=tmp_path / "config.yaml",
        prompt="Use the agency-setup skill.",
    )

    with pytest.raises(IntegrationError, match="reinstall christag-agency"):
        CopilotIntegration().interactive_setup_fallback_command(request)
```

- [ ] **Step 2: Run the focused tests to verify `--add-dir` and guards are absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_interactive_setup.py -q
```

Expected: command assertions fail because `--add-dir` is absent, and package/
shadowing tests fail because command construction does not validate either path.

- [ ] **Step 3: Implement package and shadow validation**

Import `copilot_discovery_root` and `is_symlink_or_reparse`. Add this method to
`CopilotIntegration` beside `_interactive_setup_command`:

```python
def _interactive_setup_discovery_root(self, data_root: Path) -> Path:
    root = copilot_discovery_root()
    skill_root = root / ".github" / "skills" / "agency-setup"
    skill_file = skill_root / "SKILL.md"
    try:
        resolved_root = root.resolve(strict=True)
        resolved_skill_root = skill_root.resolve(strict=True)
    except OSError as exc:
        raise IntegrationError(
            "Bundled agency-setup skill is missing or unreadable; "
            "reinstall christag-agency."
        ) from exc
    if (
        is_symlink_or_reparse(skill_file)
        or not skill_file.is_file()
        or not os.access(skill_file, os.R_OK)
    ):
        raise IntegrationError(
            "Bundled agency-setup skill is missing or unreadable; "
            "reinstall christag-agency."
        )

    local_skill = data_root / ".github" / "skills" / "agency-setup"
    if local_skill.exists() or is_symlink_or_reparse(local_skill):
        try:
            local_resolved = local_skill.resolve(strict=True)
        except OSError as exc:
            raise IntegrationError(
                "Agency data root contains a conflicting agency-setup skill at "
                f"{local_skill}. Remove or rename it before launching setup."
            ) from exc
        if local_resolved != resolved_skill_root:
            raise IntegrationError(
                "Agency data root contains a conflicting agency-setup skill at "
                f"{local_skill}. Remove or rename it before launching setup."
            )
    return resolved_root
```

This allows the repository's canonical symlink and rejects a regular local
copy, junction, unrelated symlink, or broken link.

- [ ] **Step 4: Attach the discovery root to every setup command**

Build `_interactive_setup_command()` in this order:

```python
def _interactive_setup_command(
    self,
    request: InteractiveSetupRequest,
) -> Sequence[str]:
    data_root = request.data_root.resolve(strict=True)
    discovery_root = self._interactive_setup_discovery_root(data_root)
    return (
        *self._interactive_setup_command_prefix(),
        "-C",
        str(data_root),
        "--add-dir",
        str(discovery_root),
        "-i",
        request.prompt,
        "--name",
        "Agency setup",
    )
```

`launch_interactive_setup()` must pass `data_root` as the process cwd.
`interactive_setup_fallback_command()` continues formatting this same sequence,
so direct launch and fallback cannot drift.

- [ ] **Step 5: Run Copilot and route failure regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_interactive_setup.py tests/test_server.py tests/test_setup_assets.py -q
```

Expected: direct executable, PowerShell wrapper, fallback formatting, missing
package, canonical link, shadow conflict, and non-waiting double-failure tests
all pass.

- [ ] **Step 6: Commit Copilot skill attachment**

Run:

```powershell
git add agency/integrations/agency/copilot.py tests/test_interactive_setup.py
git commit -m "fix(copilot): attach the packaged setup skill"
```

---

### Task 5: Align Guided And Manual Setup Guidance

**Files:**
- Modify: `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md`
- Modify through symlink: `skills/agency-setup/SKILL.md`
- Modify: `README.md`
- Modify: `kb/getting-started.md`
- Modify: `kb/setup-skill.md`
- Modify: `tests/test_agency_setup_skill.py`
- Modify: `tests/test_surface_contracts.py`

**Interfaces:**
- Consumes: the four exact guided context lines from `build_setup_prompt()` and package-owned canonical skill from Task 1.
- Produces: deterministic guided/manual question ordering and active documentation that matches the browser and launch contract.

- [ ] **Step 1: Write failing guided/manual skill contract tests**

Replace `test_setup_asks_for_data_root_before_team_questions()` in
`tests/test_agency_setup_skill.py` with:

```python
def test_guided_setup_asks_for_workspace_before_inspection_and_team_questions():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    guided = normalized.index("Setup mode: guided-first-run.")
    workspace = normalized.index(
        "ask for the first group project workspace as the first user-facing question"
    )
    inspection = normalized.index("inspect that workspace read-only")
    team = normalized.index("how many agents to create")

    assert guided < workspace < inspection < team
    assert "do not ask for the data root again" in normalized


def test_manual_setup_collects_root_then_workspace_without_hidden_mode_state():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "without that complete guided context" in normalized
    assert "ask for the Agency data root first" in normalized
    assert "then ask for the first group project workspace" in normalized
    assert "No environment variable or hidden process state selects a mode." in skill
```

Update the creation-timing assertion to require:

```python
assert "No derived directory or blueprint may be created before" in skill
```

In `tests/test_surface_contracts.py`, replace the old project-handoff test with:

```python
def test_readme_and_getting_started_describe_the_data_root_handoff():
    expected = (
        "Start Agency, choose the Agency data root and supported AI integration, "
        "complete the agency-setup conversation, and return to the dashboard automatically."
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    getting_started = (REPO_ROOT / "kb" / "getting-started.md").read_text(
        encoding="utf-8"
    )

    assert expected in readme
    assert expected in getting_started
    for text in (readme, getting_started):
        assert "project workspace as its first question" in text
        assert "choose the project folder" not in text.lower()
```

Replace the Windows junction assertion with one requiring that guided Copilot
setup is automatic and the old `New-Item -ItemType Junction` recipe is absent.

- [ ] **Step 2: Run the contract tests to verify the old question order remains**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agency_setup_skill.py tests/test_surface_contracts.py -q
```

Expected: failures identify root-first guided ordering, old project-folder copy,
and the obsolete manual Copilot junction instructions.

- [ ] **Step 3: Rewrite the canonical skill's launch-context section**

In the package-owned `SKILL.md`, replace the opening paragraph and Section 1
with this contract while preserving the existing derivation table:

```markdown
The `agency-setup` skill owns the one authoritative canonical Agency config.
Guided first-run setup supplies an approved Agency data root, authoritative
config path, and supported AI integration; manual invocation collects missing
context explicitly. The skill selects and inspects the first group project
workspace, derives canonical storage paths, and then owns group naming,
blueprint source, explicit instances, routines, runtime policy, workspaces,
memory, validation, and the one atomic config write.

## 1. Resolve Launch Context And Project Workspace

Consume the launch context before asking questions. Guided mode requires both
the exact `Setup mode: guided-first-run.` marker and an `Agency data root:`
line. In guided mode, use that root as already selected and do not ask for the
data root again. Ask for the first group project workspace as the first
user-facing question.

Without that complete guided context, ask for the Agency data root first.
Explain that it is a separate home for Agency-owned data: reusable agent
blueprints, disposable compiled projections, semantic memory and durable jobs,
and per-group records. Accept an existing directory or a new absolute path,
expand user-home syntax, and require a writable real nearest parent for a
missing root. Give `C:\Agency` and `~/Agency` as examples. Then ask for the first
group project workspace. No environment variable or hidden process state
selects a mode.

After the project workspace is selected, inspect that workspace read-only.
Read project instructions, README, dependency manifests, source layout, tests,
deployment files, and recent git history. Detect the host OS and available
agent CLI. Do not ask about the group, agents, roles, routines, workspaces,
memory channels, or individual storage paths before this inspection completes.
```

After the retained derivation block, add these exact rules:

```markdown
The guided launcher may already have created the selected data root so it can
serve as the session working directory. Derive every child path in memory only.
No derived directory or blueprint may be created before the user approves the
consolidated path summary.

If validation fails in guided mode, return to the project-workspace choice or
the grouped path review; do not ask for the supplied data root again. In manual
mode, validation may return to the root choice or grouped path review.
```

Replace the later pre-approval sentence with `No derived directory or blueprint
may be created before the user approves this summary.`

- [ ] **Step 4: Align setup guide and quick-start documentation**

Use this exact lead sentence in both `README.md` and `kb/getting-started.md`:

```text
Start Agency, choose the Agency data root and supported AI integration, complete the agency-setup conversation, and return to the dashboard automatically.
```

State immediately afterward that the launcher safely creates a missing root,
attaches the bundled skill, and the guided conversation asks for the project
workspace as its first question.

In `kb/setup-skill.md`:

1. Describe the same guided/manual branch as the canonical skill.
2. Change the Run section to say the first-run page launches from the selected
   data root with the exact config path and integration.
3. Replace `### GitHub Copilot on Windows` and its junction commands with:

```markdown
### GitHub Copilot

The first-run launcher exposes the package-owned `agency-setup` skill to
Copilot automatically. A normal editable or wheel installation does not need a
project-local junction or user-global skill installation.
```

Keep the Claude manual installation section because this change only adds an
integration-owned discovery attachment for Copilot.

- [ ] **Step 5: Run all skill, documentation, and package tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py tests/test_surface_contracts.py tests/test_setup_assets.py tests/test_setup_flow.py -q
```

Expected: all tests pass; active docs contain no stale project-folder handoff;
both repository links still expose the changed package-owned skill; the rebuilt
wheel contains the updated bytes.

- [ ] **Step 6: Commit the workflow and documentation alignment**

Run:

```powershell
git add agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md README.md kb/getting-started.md kb/setup-skill.md tests/test_agency_setup_skill.py tests/test_surface_contracts.py
git commit -m "fix(setup): align guided setup questions"
```

---

### Task 6: Verify And Review The Complete Feature

**Files:**
- Verify: every file changed in Tasks 1-5
- Do not create tracked browser artifacts

**Interfaces:**
- Consumes: all implementation commits and the approved design commit.
- Produces: a clean, reviewed feature tip with focused, full-suite, package, and responsive-browser evidence.

- [ ] **Step 1: Scan for stale setup-boundary terminology and malformed diffs**

Run:

```powershell
git diff --check master...HEAD
git grep -n -E "project_dir|Project folder|Choose project folder|choose the project folder" -- agency/templates/setup.html agency/web/setup_flow.py agency/web/routes/admin_groups.py agency/integrations/models.py agency/integrations/agency/copilot.py README.md kb/getting-started.md kb/setup-skill.md
```

Expected: `git diff --check` emits nothing and `git grep` finds no match. Do not
remove legitimate `project workspace` wording from the skill, group config, or
runtime documentation. Negative compatibility assertions in tests may still
contain the literal `project_dir`.

- [ ] **Step 2: Run the complete focused regression slice**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_setup_assets.py tests/test_path_validation.py tests/test_setup_flow.py tests/test_server.py tests/test_group_settings.py tests/test_interactive_setup.py tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py tests/test_surface_contracts.py -q
```

Expected: every focused test passes with no unexpected skip or collection error.

- [ ] **Step 3: Build and inspect a clean wheel explicitly**

Run:

```powershell
$wheelDir = Join-Path $env:TEMP "agency-setup-wheel-$PID"; Remove-Item -Recurse -Force $wheelDir -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path $wheelDir | Out-Null; .\.venv\Scripts\python.exe -m pip wheel --disable-pip-version-check --no-deps --wheel-dir $wheelDir .; .\.venv\Scripts\python.exe -c "from pathlib import Path; from zipfile import ZipFile; wheel=next(Path(r'$wheelDir').glob('christag_agency-*.whl')); names=ZipFile(wheel).namelist(); required=[n for n in names if n.startswith('agency/setup_assets/copilot/.github/skills/agency-setup/')]; assert any(n.endswith('/SKILL.md') for n in required); assert len([n for n in required if '/references/' in n]) == 3; print(*required, sep='\n')"
Remove-Item -Recurse -Force $wheelDir
```

Expected: the command prints `SKILL.md` and exactly the three current reference
documents beneath the package discovery root. Remove `$wheelDir` afterward.

- [ ] **Step 4: Run the complete Python suite**

Run with no concurrent source edits:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: all tests pass; the baseline five environment-dependent skips remain
acceptable. Record the exact pass/skip counts.

- [ ] **Step 5: Smoke-test the setup page at desktop and mobile widths**

Invoke the `playwright` skill before browser automation. Start an Agency server
from the feature worktree on an unused port with `AGENCY_CONFIG` pointing to a
new temporary `config.yaml`; do not use or edit the repository's runtime config.
Run the server through `run_in_terminal` in async mode:

```powershell
$smokeRoot = Join-Path $env:TEMP "agency-setup-browser-smoke"; Remove-Item -Recurse -Force $smokeRoot -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path $smokeRoot | Out-Null; $env:AGENCY_CONFIG = Join-Path $smokeRoot "config.yaml"; .\.venv\Scripts\python.exe -m agency.cli serve --host 127.0.0.1 --port 8766
```

Open `/setup` and check at `1440x1000` and `390x844`:

```text
Agency data root
C:\Agency
Choose Agency data root
```

At both widths verify the input, Browse button, integration selector, and
continue action do not overlap or overflow. Open and close the directory picker;
verify its title and selection feedback. Do not submit the form or launch a real
AI session during this visual smoke. Capture temporary screenshots for
inspection, then delete them and stop the temporary server.
After killing its async terminal, run:

```powershell
Remove-Item -Recurse -Force (Join-Path $env:TEMP "agency-setup-browser-smoke")
```

- [ ] **Step 6: Reproduce skill discovery with one real Copilot session**

Use a fresh disposable absolute data root and authoritative config outside the
repository. Run this through `run_in_terminal` in async mode so the output can
be inspected and the process can be stopped immediately after the first
user-facing question:

```powershell
$liveRoot = Join-Path $env:TEMP "agency-setup-live-smoke"; $dataRoot = Join-Path $liveRoot "Agency"; $configPath = Join-Path $liveRoot "config.yaml"; Remove-Item -Recurse -Force $liveRoot -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null; .\.venv\Scripts\python.exe -c "import os; from pathlib import Path; from agency.integrations.agency.copilot import CopilotIntegration; from agency.integrations.models import InteractiveSetupRequest; from agency.web.setup_flow import build_setup_prompt; root=Path(r'$dataRoot').resolve(strict=True); config=Path(r'$configPath').resolve(); prompt=build_setup_prompt(root, config, selected_integration='copilot'); request=InteractiveSetupRequest(data_root=root, config_path=config, prompt=prompt); command=tuple(CopilotIntegration()._interactive_setup_command(request)); os.execv(command[0], command)"
```

Stop the async terminal without answering the workspace question. Then run:

```powershell
$liveRoot = Join-Path $env:TEMP "agency-setup-live-smoke"; $dataRoot = Join-Path $liveRoot "Agency"; $forbidden = "agent-library", "compiled-agents", "memory", "prompts", "groups", "config.yaml"; $present = $forbidden | Where-Object { Test-Path (Join-Path $dataRoot $_) }; if ($present) { throw "Live setup created forbidden pre-approval paths: $($present -join ', ')" }; Remove-Item -Recurse -Force $liveRoot
```

Expected evidence:

```text
- no "Skill not found: agency-setup" message
- agency-setup instructions are active
- the first question asks for the first group project workspace
- the selected root contains no agent-library, compiled-agents, memory,
  prompts, groups, or config output
```

This one live check may consume a Copilot request. If Copilot is not available
or the skill still fails to resolve, stop before review/integration and report
the acceptance blocker rather than substituting command inspection.

- [ ] **Step 7: Perform the required whole-branch review**

Invoke `superpowers:requesting-code-review` against:

```text
Base: 797b238 (approved design commit)
Head: current feature tip
Spec: docs/superpowers/specs/2026-08-31-setup-data-root-skill-discovery-design.md
```

Require the reviewer to check package completeness, symlink portability,
real/reparse path safety, root-creation side effects, shadowing behavior,
fallback state, prompt/skill ordering, stale copy, and missing tests.

- [ ] **Step 8: Resolve findings and re-run evidence**

For each correctness finding, add the smallest failing regression test, run it
to observe the failure, apply the focused fix, rerun the focused test, and commit
the repair separately. Then repeat Steps 1-7. If review finds no issue, create no
review-only commit.

- [ ] **Step 9: Confirm a clean feature tip**

Run:

```powershell
git status --short --branch
git log --oneline --decorate -8
```

Expected: no working-tree changes; the standalone design and plan commits
precede focused implementation commits on
`fix/setup-data-root-skill-discovery`.

---

### Task 7: Fast-Forward, Verify, Push, And Clean Up

**Files:**
- Integrate: reviewed `fix/setup-data-root-skill-discovery` into `master`
- Preserve: feature branch `fix/setup-data-root-skill-discovery`
- Remove after verification: `.worktrees/setup-data-root-skill-discovery`

**Interfaces:**
- Consumes: clean reviewed feature tip and recorded green verification from Task 6.
- Produces: fast-forwarded and pushed `master`, pushed retained feature branch, restored unrelated main-checkout changes, and no completed worktree.

- [ ] **Step 1: Stop feature-worktree processes and inspect both branches**

Stop the temporary browser-smoke server and ensure no terminal cwd remains inside
the feature worktree. From `C:/Projekty/christag-agency`, run:

```powershell
git status --short --branch
git fetch origin
git rev-parse master
git rev-parse fix/setup-data-root-skill-discovery
git merge-base --is-ancestor master fix/setup-data-root-skill-discovery
```

If `master` has tracked or untracked user changes, record them and stash with
untracked files before integration; never discard or stage them. If `master` is
not an ancestor of the feature tip, rebase the feature branch onto current
`master` inside the worktree, rerun Task 6 Steps 1-4 there, and repeat this
precondition check.

Use these exact commands when either condition applies:

```powershell
# Main checkout, only when user changes are present:
git stash push --include-untracked -m "pre-setup-data-root-integration"

# Main checkout, only when origin/master is strictly ahead:
git merge --ff-only origin/master

# Feature worktree, only when master is not its ancestor:
git rebase master
```

- [ ] **Step 2: Fast-forward master only**

From the main checkout, run:

```powershell
git merge --ff-only fix/setup-data-root-skill-discovery
```

Expected: `master` points directly at the reviewed feature tip with no merge or
squash commit.

- [ ] **Step 3: Re-run the complete suite on fast-forwarded master**

Run from `C:/Projekty/christag-agency`:

```powershell
python -m pytest tests/ -q
```

Expected: every test passes. If the main interpreter lacks a test-only package,
use the already installed project environment rather than changing runtime
dependencies solely for this verification.

- [ ] **Step 4: Push both required branches**

Run only after the master suite is green:

```powershell
git push origin master fix/setup-data-root-skill-discovery
```

Expected: both remote refs point to the reviewed feature tip.

- [ ] **Step 5: Restore main-checkout changes and remove the worktree**

If Step 1 created a stash, restore it with `git stash pop` now and report any
conflict without discarding user content. Then run:

```powershell
git worktree remove .worktrees/setup-data-root-skill-discovery
git worktree prune
git branch --list fix/setup-data-root-skill-discovery
git worktree list
git status --short --branch
```

Expected: the completed worktree is absent, the feature branch remains, stale
worktree metadata is gone, and `master` retains any restored pre-existing user
changes.

- [ ] **Step 6: Report final evidence**

Report the focused and both full-suite results, wheel contents, desktop/mobile
smoke outcome, whole-branch review result, fast-forwarded commit hash, pushed
refs, removed worktree path, retained feature branch, and any restored user
changes. Do not claim the `Skill not found` reproduction is fixed unless the
command includes the verified packaged discovery root and all automated checks
are green; distinguish command-contract evidence from any real interactive
Copilot launch that was intentionally not run.