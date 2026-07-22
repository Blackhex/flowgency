# Group Storage Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate each group's source workspace from its Agency-owned state root so no Agency record directories are created inside project repositories.

**Architecture:** Configuration schema version 3 gives every group a mandatory `workspace_path` and `path`. A central `ResolvedGroupPaths` model derives pipeline, lock, and log directories; all configuration validation, runtime policy, jobs, CLI, web, dispatch, and administration consume that model instead of constructing `<workspace>/shared`.

**Tech Stack:** Python 3.11+, Pydantic 2, FastAPI, Jinja2, PyYAML, pytest, portalocker

## Global Constraints

- The only accepted configuration shape is `schema_version: 3`; do not add migration, aliases, dual reads, fallback paths, or startup conversion.
- `groups.<id>.workspace_path` is the source project and execution workspace.
- `groups.<id>.path` is the exact Agency-owned group-state root.
- Group `path` directly contains `observations`, `proposals`, `decisions`, `locks`, and `logs`; never append `shared`.
- Authoritative durable jobs remain under `agency.memory_store/.jobs/<group-id>`.
- Operation locks live at `<group.path>/locks/.operations.lock`; there is no group `jobs` directory.
- Restricted runtime policies always include both `workspace_path` and group `path`, followed by configured group and agent additions.
- Project workspaces must remain free of Agency-generated record directories.
- Preserve atomic revision-checked configuration writes and unrelated extension keys.
- Use `.venv\Scripts\python -m pytest` for targeted and full validation.

## File Structure

### New focused module

- `agency/configuration/group_paths.py` — owns `ResolvedGroupPaths` and all derived group-state directories.

### Configuration authority

- `agency/configuration/models.py` — schema version 3, mandatory group roots, relative path resolution.
- `agency/configuration/paths.py` — filesystem safety, overlap validation, and storage initialization.
- `agency/configuration/store.py` — validate before initialization and atomically encode only valid schema version 3 configs.
- `agency/configuration/patches.py` — create and update both group paths.
- `agency/configuration/effective.py` — mandatory runtime root union.
- `agency/configuration/__init__.py` — export the new path interfaces.

### Runtime and jobs

- `agency/jobs/models.py` — version 3 job snapshots with explicit `workspace_root` and `group_root`.
- `agency/jobs/resolution.py` — resolve both roots and one shared effective runtime policy.
- `agency/jobs/execution.py` — execute in the workspace and persist prompts/logs in the group root.
- `agency/jobs/store.py` — place operation locks under `locks`.
- `agency/jobs/reconciliation.py` — carry the group root for decision and recovery projection.
- `agency/jobs/submission.py` — initialize all configured storage before submission.
- `agency/dispatch/run.py` — write scheduler output and markers under group `logs`.
- `agency/integrations/models.py` — expose `IntegrationRunRequest.workspace_root`.
- `agency/integrations/agency/copilot.py`, `agency/integrations/agency/script.py` — consume the renamed execution root and script placeholder.

### Web and CLI consumers

- `agency/web/state.py` — expose resolved roots and directories to route code.
- `agency/web/routes/agent_detail.py` — use group-root activity and log directories.
- `agency/web/routes/admin_groups.py` — edit and display workspace and group paths separately.
- `agency/templates/admin_org_edit.html` — separate labeled path fields.
- `agency/templates/admin_groups.html` — display both roots and remove the obsolete initialize action.
- `agency/app.py` — move pipeline and log routes to resolved group directories and remove `shared` reload assumptions.
- `agency/cli.py` — read pipeline records from group roots and report workspace/group roots accurately.

### Guidance and contracts

- `config.yaml` examples in tracked fixtures and documentation — use schema version 3.
- `CLAUDE.md`, `README.md`, `kb/configuration.md`, `kb/directory-structure.md`, `kb/data-formats.md`, `kb/getting-started.md`, `kb/setup-skill.md` — document the new authority boundaries.
- `skills/agency-setup/SKILL.md`, `skills/agency-setup/references/templates.md` — generate only the green-field schema.
- `tests/conftest.py` and affected tests — create separate workspace and group roots.

---

### Task 1: Establish the Canonical Schema Version 3

**Files:**
- Modify: `agency/configuration/models.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_config_store.py`
- Modify: `tests/test_surface_contracts.py`

**Interfaces:**
- Produces: `CONFIG_SCHEMA_VERSION: int = 3`
- Produces: `GroupConfig.workspace_path: Path`
- Produces: `GroupConfig.path: Path`
- Produces: `AgencyConfig.schema_version: Literal[3]`
- Consumes: no interfaces from later tasks

- [ ] **Step 1: Rewrite the canonical test fixture with separate roots**

Update `tests/conftest.py` so `config_paths` creates an existing workspace but leaves the group-state root available for initialization:

```python
@pytest.fixture
def config_paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    agent_library = tmp_path / "agent-library"
    workspace_path = tmp_path / "workspace"
    group_path = tmp_path / "groups" / "newsletter"
    agent_library.mkdir(parents=True)
    workspace_path.mkdir()
    return {
        "config_path": config_path,
        "config_dir": config_path.parent,
        "agent_library": agent_library,
        "workspace_path": workspace_path,
        "group_path": group_path,
        "compilation_cache": tmp_path / "compiled-agents",
        "memory_store": tmp_path / "memory",
    }
```

Make `raw_config` start with:

```python
return {
    "schema_version": 3,
    "agency": {
        "title": "Agency",
        "default_group": "newsletter",
        "ai_backend": "claude-code",
        "agent_library": str(config_paths["agent_library"]),
        "compilation_cache": str(config_paths["compilation_cache"]),
        "memory_store": str(config_paths["memory_store"]),
    },
    "memory": {"channels": {"support": {"display_name": "Support"}}},
    "groups": {
        "newsletter": {
            "name": "Newsletter",
            "workspace_path": str(config_paths["workspace_path"]),
            "path": str(config_paths["group_path"]),
            "default_integration": "claude-code",
            "agents": [
                {
                    "name": "builder",
                    "blueprint": "builder-blueprint",
                    "integration": "claude-code",
                    "routines": [
                        {
                            "id": "daily-review",
                            "skill": "daily-review",
                            "schedule": {"at": "09:00"},
                            "memory": {"scope": "routine"},
                        }
                    ],
                }
            ],
            "workspaces": [
                {
                    "name": "Terminal Grid",
                    "type": "tmux",
                    "config": {"script_path": "tmux-agents.sh"},
                }
            ],
        }
    },
}
```

- [ ] **Step 2: Add failing schema tests**

Add these focused tests to `tests/test_config.py`:

```python
def test_parse_config_requires_schema_version_three(raw_config, config_paths):
    from agency.configuration.models import parse_config, validate_config

    parsed = parse_config(raw_config, config_paths["config_path"])
    assert parsed.resolved.schema_version == 3

    for value in (None, 1, 2, 4):
        candidate = _clone_config(raw_config)
        if value is None:
            candidate.pop("schema_version")
        else:
            candidate["schema_version"] = value
        issues = validate_config(candidate, config_paths["config_path"])
        assert any(issue.field == "schema_version" for issue in issues)


def test_group_requires_workspace_and_state_paths(raw_config, config_paths):
    from agency.configuration.models import validate_config

    for field in ("workspace_path", "path"):
        candidate = _clone_config(raw_config)
        del candidate["groups"]["newsletter"][field]
        issues = validate_config(candidate, config_paths["config_path"])
        assert any(
            issue.field == f"groups.newsletter.{field}"
            for issue in issues
        )


def test_relative_group_paths_resolve_from_config_directory(raw_config, config_paths):
    from agency.configuration.models import parse_config

    raw_config["groups"]["newsletter"]["workspace_path"] = "workspace"
    raw_config["groups"]["newsletter"]["path"] = "groups/newsletter"
    parsed = parse_config(raw_config, config_paths["config_path"])
    group = parsed.groups["newsletter"]
    assert group.workspace_path == (config_paths["config_dir"] / "workspace").resolve()
    assert group.path == (config_paths["config_dir"] / "groups/newsletter").resolve()
```

Replace old assertions that `schema_version` is forbidden with assertions that it is mandatory and preserved in `tests/test_config_store.py` and `tests/test_surface_contracts.py`.

- [ ] **Step 3: Run the schema tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_config.py tests\test_config_store.py tests\test_surface_contracts.py -q
```

Expected: failures report that `schema_version` is an unknown root key and `GroupConfig` has no `workspace_path`.

- [ ] **Step 4: Implement schema version 3**

In `agency/configuration/models.py`, define and use:

```python
from typing import Any, Literal

CONFIG_SCHEMA_VERSION = 3
_ROOT_KEYS = {"schema_version", "agency", "memory", "groups"}


class GroupConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    workspace_path: Path
    path: Path
    default_integration: str
    runtime: GroupRuntime = Field(default_factory=GroupRuntime)
    dispatch: GroupDispatch = Field(default_factory=GroupDispatch)
    agents: dict[str, AgentInstance] = Field(default_factory=dict)
    workspaces: tuple[WorkspaceConfig, ...] = ()


class AgencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[3]
    agency: AgencySettings
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    groups: dict[str, GroupConfig]
```

In `_validate_raw_config`, emit explicit issues:

```python
if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
    issues.append(
        _build_issue(
            code="unsupported-schema-version",
            scope="config",
            field="schema_version",
            message="schema_version must be 3.",
            hint="Create a fresh schema_version: 3 configuration.",
        )
    )
```

For every group, validate both mandatory fields:

```python
for field_name in ("workspace_path", "path"):
    if not str(group.get(field_name, "")).strip():
        issues.append(
            _build_issue(
                code=f"missing-group-{field_name.replace('_', '-')}",
                scope=f"groups.{group_name}",
                field=f"groups.{group_name}.{field_name}",
                message=f"Group {field_name} is required.",
                hint=f"Set groups.{group_name}.{field_name} relative to config.yaml.",
            )
        )
```

In normalization, resolve both fields independently:

```python
normalized_group["workspace_path"] = _path_from_config(
    group["workspace_path"], config_dir
)
normalized_group["path"] = _path_from_config(group["path"], config_dir)
```

Delete old validation text that describes `group.path` as the workspace. Export `CONFIG_SCHEMA_VERSION` if tests or setup validation need it.

- [ ] **Step 5: Run the schema tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_config.py tests\test_config_store.py tests\test_surface_contracts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add agency\configuration\models.py tests\conftest.py tests\test_config.py tests\test_config_store.py tests\test_surface_contracts.py
git commit -m "feat(config): define group storage schema"
```

---

### Task 2: Centralize Group Paths, Validation, and Initialization

**Files:**
- Create: `agency/configuration/group_paths.py`
- Modify: `agency/configuration/paths.py`
- Modify: `agency/configuration/store.py`
- Modify: `agency/configuration/__init__.py`
- Modify: `agency/web/dependencies.py`
- Modify: `agency/jobs/submission.py`
- Modify: `tests/test_path_validation.py`
- Modify: `tests/test_config_store.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `GroupConfig.workspace_path`, `GroupConfig.path`
- Produces: `ResolvedGroupPaths`
- Produces: `resolve_group_paths(group: GroupConfig) -> ResolvedGroupPaths`
- Produces: `initialize_storage_directories(config: AgencyConfig) -> None`
- Produces: `validate_resolved_paths(config: AgencyConfig) -> tuple[ValidationIssue, ...]`

- [ ] **Step 1: Write failing resolved-path and initialization tests**

Add to `tests/test_path_validation.py`:

```python
def test_resolved_group_paths_have_no_shared_segment(tmp_path, raw_config):
    from agency.configuration.group_paths import resolve_group_paths
    from agency.configuration.models import parse_config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw_config["groups"]["newsletter"]["workspace_path"] = str(workspace)
    raw_config["groups"]["newsletter"]["path"] = str(tmp_path / "groups/newsletter")
    group = parse_config(raw_config, tmp_path / "config.yaml").resolved.groups["newsletter"]

    paths = resolve_group_paths(group)
    assert paths.workspace_root == workspace.resolve()
    assert paths.group_root == (tmp_path / "groups/newsletter").resolve()
    assert paths.observations == paths.group_root / "observations"
    assert paths.proposals == paths.group_root / "proposals"
    assert paths.decisions == paths.group_root / "decisions"
    assert paths.locks == paths.group_root / "locks"
    assert paths.logs == paths.group_root / "logs"
    assert "shared" not in {part for path in paths.record_directories for part in path.parts}


def test_initialization_creates_group_state_but_not_workspace_shared(
    tmp_path, raw_config
):
    from agency.configuration.models import parse_config
    from agency.configuration.paths import initialize_storage_directories

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw_config["groups"]["newsletter"]["workspace_path"] = str(workspace)
    raw_config["groups"]["newsletter"]["path"] = str(tmp_path / "groups/newsletter")
    config = parse_config(raw_config, tmp_path / "config.yaml").resolved

    initialize_storage_directories(config)

    group_root = tmp_path / "groups/newsletter"
    assert {
        child.name for child in group_root.iterdir() if child.is_dir()
    } == {"observations", "proposals", "decisions", "locks", "logs"}
    assert not (workspace / "shared").exists()
```

Add overlap cases for:

```python
("workspace_path", "agency.memory_store")
("path", "agency.agent_library")
("path", "groups.other.path")
("path", "groups.other.workspace_path")
("workspace_path", "path")
```

Add a symlink/reparse test using the repository's existing platform-safe skip pattern.

- [ ] **Step 2: Run path tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_path_validation.py tests\test_config_store.py tests\test_server.py -q
```

Expected: import failure for `agency.configuration.group_paths` and old workspace validation semantics.

- [ ] **Step 3: Add the central path model**

Create `agency/configuration/group_paths.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import GroupConfig


@dataclass(frozen=True)
class ResolvedGroupPaths:
    workspace_root: Path
    group_root: Path
    observations: Path
    proposals: Path
    decisions: Path
    locks: Path
    logs: Path

    @property
    def record_directories(self) -> tuple[Path, ...]:
        return (
            self.observations,
            self.proposals,
            self.decisions,
            self.locks,
            self.logs,
        )


def resolve_group_paths(group: GroupConfig) -> ResolvedGroupPaths:
    workspace_root = Path(group.workspace_path).resolve(strict=False)
    group_root = Path(group.path).resolve(strict=False)
    return ResolvedGroupPaths(
        workspace_root=workspace_root,
        group_root=group_root,
        observations=group_root / "observations",
        proposals=group_root / "proposals",
        decisions=group_root / "decisions",
        locks=group_root / "locks",
        logs=group_root / "logs",
    )
```

Export both names from `agency/configuration/__init__.py`.

- [ ] **Step 4: Rewrite filesystem validation**

In `agency/configuration/paths.py`:

1. Validate `workspace_root` with the existing-directory helper and error code `invalid-group-workspace`.
2. Validate `group_root` with the creatable control-directory helper and error code `invalid-group-root`.
3. Build authority lists from the central resolver.
4. Reject overlap between all global stores, all group roots, and all workspaces.
5. Keep configured sandbox/additional-root checks against global control stores.
6. Treat the mandatory effective inclusion of workspace/group roots as intentional.

The central loop should have this shape:

```python
group_paths = {
    group_id: resolve_group_paths(group)
    for group_id, group in config.groups.items()
}
for group_id, paths in group_paths.items():
    scope = f"groups.{group_id}"
    issues.extend(
        _validate_existing_directory(
            paths.workspace_root,
            code="invalid-group-workspace",
            scope=scope,
            field="workspace_path",
            writable=True,
        )
    )
    issues.extend(
        _validate_creatable_directory(
            paths.group_root,
            code="invalid-group-root",
            scope=scope,
            field="path",
        )
    )
```

Compare normalized paths with `_overlap` and emit field-specific messages naming both resolved authorities.

- [ ] **Step 5: Replace initialization ordering and API**

Rename `initialize_control_directories` to `initialize_storage_directories` and implement:

```python
def initialize_storage_directories(config: AgencyConfig) -> None:
    directories = [
        Path(config.agency.compilation_cache),
        Path(config.agency.memory_store),
        job_store_root(Path(config.agency.memory_store)),
    ]
    for group in config.groups.values():
        paths = resolve_group_paths(group)
        directories.extend((paths.group_root, *paths.record_directories))
    for path in directories:
        _ensure_real_directory(path, create=True)
```

Use the existing symlink/reparse detection helpers or extract one shared helper rather than calling bare `mkdir` after validation.

In `ConfigStore._encode`, validate before initialization:

```python
parsed = parse_config(raw, self.path)
issues = validate_resolved_paths(parsed.resolved)
if issues:
    raise ValidationFailed(issues)
initialize_storage_directories(parsed.resolved)
return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode("utf-8")
```

Apply the same validate-then-initialize ordering in `build_services` and `submit_job_request`.

- [ ] **Step 6: Run path and startup tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_path_validation.py tests\test_config_store.py tests\test_server.py tests\test_job_submission.py -q
```

Expected: all selected tests pass and no test workspace contains `shared`.

- [ ] **Step 7: Commit**

```powershell
git add agency\configuration\group_paths.py agency\configuration\paths.py agency\configuration\store.py agency\configuration\__init__.py agency\web\dependencies.py agency\jobs\submission.py tests\test_path_validation.py tests\test_config_store.py tests\test_server.py
git commit -m "feat(storage): add resolved group roots"
```

---

### Task 3: Add Mandatory Roots to Effective Runtime Policy

**Files:**
- Modify: `agency/configuration/effective.py`
- Modify: `agency/jobs/resolution.py`
- Modify: `tests/test_effective_policy.py`
- Modify: `tests/test_job_submission.py`

**Interfaces:**
- Consumes: `resolve_group_paths(group) -> ResolvedGroupPaths`
- Produces: `resolve_effective_policy(..., integration: BaseIntegration | None = None) -> EffectiveRuntimePolicy`
- Produces: restricted root order `workspace_root`, `group_root`, group roots, agent additions

- [ ] **Step 1: Write failing effective-policy tests**

Add to `tests/test_effective_policy.py`:

```python
def test_restricted_policy_starts_with_workspace_and_group_roots(
    raw_config, config_paths
):
    from agency.configuration.effective import resolve_effective_policy
    from agency.configuration.models import parse_config

    extra = config_paths["config_dir"] / "research"
    extra.mkdir()
    group = raw_config["groups"]["newsletter"]
    group["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": [str(extra)]}
    }
    config = parse_config(raw_config, config_paths["config_path"]).resolved

    policy = resolve_effective_policy(config, "newsletter", "builder")

    assert policy.sandbox_roots[:2] == (
        config.groups["newsletter"].workspace_path,
        config.groups["newsletter"].path,
    )
    assert policy.sandbox_roots[2:] == (extra.resolve(),)


def test_unrestricted_policy_has_no_root_list(raw_config, config_paths):
    from agency.configuration.effective import resolve_effective_policy
    from agency.configuration.models import parse_config

    config = parse_config(raw_config, config_paths["config_path"]).resolved
    policy = resolve_effective_policy(config, "newsletter", "builder")
    assert policy.sandbox_mode == "unrestricted"
    assert policy.sandbox_roots == ()
```

Add a `tests/test_job_submission.py` assertion that the serialized job policy contains both mandatory roots when restricted.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py -q
```

Expected: restricted roots omit `workspace_path` and group `path`.

- [ ] **Step 3: Implement one effective-policy resolver**

In `agency/configuration/effective.py`, update `_resolve_sandbox`:

```python
paths = resolve_group_paths(group)
if mode == "unrestricted":
    if additional_roots:
        raise ValidationFailed((issue,))
    return mode, ()
return mode, _merge_roots(
    (paths.workspace_root, paths.group_root),
    tuple(group_sandbox.roots),
    tuple(agent_sandbox.additional_roots),
)
```

Allow callers with bound integration configuration to pass the integration:

```python
def resolve_effective_policy(
    config: AgencyConfig,
    group_id: str,
    agent_id: str,
    *,
    timeout_override: int | None = None,
    integration: BaseIntegration | None = None,
) -> EffectiveRuntimePolicy:
```

Use `integration or get_integration(agent.integration)` for final policy validation.

In `agency/jobs/resolution.py`, delete the duplicate `_resolve_runtime_policy` implementation and call:

```python
runtime_policy = resolve_effective_policy(
    snapshot.config,
    request.group_key,
    request.agent_name,
    timeout_override=request.timeout_override,
    integration=integration,
)
```

- [ ] **Step 4: Run policy and job-resolution tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py tests\test_agent_detail.py tests\test_cli.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add agency\configuration\effective.py agency\jobs\resolution.py tests\test_effective_policy.py tests\test_job_submission.py
git commit -m "feat(runtime): expose workspace and group roots"
```

---

### Task 4: Redesign Job Snapshots, Locks, and Execution Paths

**Files:**
- Modify: `agency/jobs/models.py`
- Modify: `agency/jobs/resolution.py`
- Modify: `agency/jobs/execution.py`
- Modify: `agency/jobs/store.py`
- Modify: `agency/jobs/reconciliation.py`
- Modify: `agency/app.py`
- Modify: `agency/integrations/models.py`
- Modify: `agency/integrations/agency/copilot.py`
- Modify: `agency/integrations/agency/script.py`
- Modify: `tests/test_job_models.py`
- Modify: `tests/test_job_submission.py`
- Modify: `tests/test_job_execution.py`
- Modify: `tests/test_job_reconciliation.py`
- Modify: `tests/test_job_authority.py`
- Modify: `tests/test_instances.py`
- Modify: `tests/test_integration_contract.py`
- Modify: `tests/test_integration_script.py`

**Interfaces:**
- Consumes: `ResolvedGroupPaths`
- Produces: job schema `SCHEMA_VERSION = 3`
- Produces: `JobSpec.workspace_root: str`
- Produces: `JobSpec.group_root: str`
- Produces: `group_operation_lock_path(group_root: Path) -> Path`

- [ ] **Step 1: Write failing job-contract tests**

Update the shared job-spec builders in job tests to use:

```python
JobSpec(
    schema_version=3,
    job_id="job-1",
    config_path=str(config_path),
    config_revision="revision-1",
    group_key="newsletter",
    workspace_root=str(workspace.resolve()),
    group_root=str(group_root.resolve()),
    agent_name="builder",
    trigger="manual_prompt",
    integration_name="claude-code",
    integration_config={},
    blueprint=blueprint_ref,
    routine_id="daily-review",
    skill="daily-review",
    skill_arguments=(),
    task_input="Run the daily review.",
    runtime_policy=runtime_policy_snapshot,
    memory=memory_binding,
    trigger_context=None,
    prompt_source={"type": "routine", "routine_id": "daily-review"},
    timeout_override=None,
    created_at="2026-07-22T12:00:00+00:00",
)
```

Add focused assertions:

```python
def test_job_spec_serializes_distinct_workspace_and_group_roots(job_spec):
    payload = job_spec.to_dict()
    assert payload["workspace_root"] == str(job_spec.resolved_workspace_root)
    assert payload["group_root"] == str(job_spec.resolved_group_root)
    assert "workspace_dir" not in payload
    assert "group_path" not in payload


def test_operation_lock_is_under_group_locks(tmp_path):
    from agency.jobs.store import group_operation_lock_path

    assert group_operation_lock_path(tmp_path) == (
        tmp_path / "locks" / ".operations.lock"
    )
```

In execution tests, assert:

```python
assert request.workspace_root == workspace.resolve()
assert Path(record.stdout_path).is_relative_to(group_root / "logs")
assert Path(record.stderr_path).is_relative_to(group_root / "logs")
assert not (workspace / "shared").exists()
```

- [ ] **Step 2: Run job tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_job_models.py tests\test_job_submission.py tests\test_job_execution.py tests\test_job_reconciliation.py tests\test_job_authority.py tests\test_instances.py -q
```

Expected: constructors reject the new fields and lock/log assertions fail.

- [ ] **Step 3: Implement job schema version 3**

In `agency/jobs/models.py`:

```python
SCHEMA_VERSION = 3


@dataclass(frozen=True)
class JobSpec:
    schema_version: int
    job_id: str
    config_path: str
    config_revision: str
    group_key: str
    workspace_root: str
    group_root: str
    agent_name: str
    trigger: str
    integration_name: str
    integration_config: dict[str, Any]
    blueprint: BlueprintRef
    routine_id: str | None
    skill: str | None
    skill_arguments: tuple[str, ...]
    task_input: str
    runtime_policy: RuntimePolicySnapshot
    memory: MemoryBinding
    trigger_context: dict[str, Any] | None
    prompt_source: dict[str, Any] | None
    timeout_override: int | None
    created_at: str

    @property
    def resolved_workspace_root(self) -> Path:
        return Path(self.workspace_root).resolve(strict=False)

    @property
    def resolved_group_root(self) -> Path:
        return Path(self.group_root).resolve(strict=False)
```

Update validation, `to_dict`, `from_dict`, and immutable digest input to use only the new names. Reject any payload containing `workspace_dir`, `group_path`, or `agent_dir`:

```python
for obsolete in ("workspace_dir", "group_path", "agent_dir"):
    if obsolete in values:
        raise ValueError(
            f"{obsolete} is not accepted in strict schema_version: 3 jobs"
        )
```

- [ ] **Step 4: Resolve and execute against distinct roots**

In `agency/jobs/resolution.py`:

```python
paths = resolve_group_paths(group)
validation_task_file = paths.logs / f"{request.job_id}.prompt"
```

Build the job with:

```python
schema_version=3,
workspace_root=str(paths.workspace_root),
group_root=str(paths.group_root),
```

In `agency/jobs/execution.py`, return context with:

```python
return SimpleNamespace(
    group_root=spec.resolved_group_root,
    workspace_root=spec.resolved_workspace_root,
    integration=integration,
    timeout=spec.runtime_policy.timeout,
    runtime_policy=runtime_policy,
    sandbox_root=None,
    launch_dir=None,
)
```

Rename the integration request contract in `agency/integrations/models.py`:

```python
@dataclass(frozen=True)
class IntegrationRunRequest:
    workspace_root: Path
    launch_dir: Path
    task_file: Path
    timeout: int
    runtime_policy: EffectiveRuntimePolicy
    skill: str | None = None
    skill_arguments: tuple[str, ...] = ()
    enforce_validation: bool = True
    memory_working_dir: Path | None = None
```

Use `context.workspace_root` for change capture fallback and
`IntegrationRunRequest.workspace_root`. Update Copilot parsing roots to
`request.workspace_root`.

In the script integration, replace the required and supported template token
`{workspace_dir}` with `{workspace_root}`:

```python
required = ("{runtime_dir}", "{workspace_root}", "{skill}")
command = command.replace("{workspace_root}", str(request.workspace_root))
```

Remove support for `{workspace_dir}` and `{agent_dir}` because the redesign has no
compatibility aliases.

Create all run files together:

```python
started = datetime.now(timezone.utc)
log_dir = context.group_root / "logs" / started.strftime("%Y-%m-%d")
log_dir.mkdir(parents=True, exist_ok=True)
stem = f"{spec.agent_name}-{spec.trigger}-{spec.job_id}"
prompt_path = log_dir / f"{stem}.prompt"
stdout_path = log_dir / f"{stem}.out"
stderr_path = log_dir / f"{stem}.err"
prompt_path.write_text(spec.task_input, encoding="utf-8")
```

Do not create the old job-authority sibling `.prompt`.

- [ ] **Step 5: Move locks and reconciliation context**

In `agency/jobs/store.py`:

```python
def group_operation_lock_path(group_root: Path) -> Path:
    return Path(group_root) / "locks" / ".operations.lock"
```

Rename local parameters from `group_path` to `group_root` where they refer to Agency state. `_group_path_identity` must compare `snapshot.config.groups[group_id].path`.

In `agency/jobs/reconciliation.py`, consume `group_root`:

```python
"group_root": group["group_root"],
```

In `agency/app.py` lifespan, pass:

```python
{
    group_id: {"group_root": str(group.path)}
    for group_id, group in snapshot.config.groups.items()
}
```

Update memory recovery context keys consistently where reconciliation projects decisions.

- [ ] **Step 6: Run all job tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_job_models.py tests\test_job_submission.py tests\test_job_execution.py tests\test_job_reconciliation.py tests\test_job_authority.py tests\test_instances.py tests\test_memory_recovery.py tests\test_memory_publication.py tests\test_integration_contract.py tests\test_integration_script.py -q
```

Expected: all selected tests pass with strict job schema version 3.

- [ ] **Step 7: Commit**

```powershell
git add agency\jobs\models.py agency\jobs\resolution.py agency\jobs\execution.py agency\jobs\store.py agency\jobs\reconciliation.py agency\integrations\models.py agency\integrations\agency\copilot.py agency\integrations\agency\script.py agency\app.py tests\test_job_models.py tests\test_job_submission.py tests\test_job_execution.py tests\test_job_reconciliation.py tests\test_job_authority.py tests\test_instances.py tests\test_memory_recovery.py tests\test_memory_publication.py tests\test_integration_contract.py tests\test_integration_script.py
git commit -m "feat(jobs): separate execution and group roots"
```

---

### Task 5: Move Dispatch, Logs, and Agent Activity to Group State

**Files:**
- Modify: `agency/dispatch/run.py`
- Modify: `agency/web/state.py`
- Modify: `agency/web/routes/agent_detail.py`
- Modify: `agency/app.py`
- Modify: `tests/test_dispatch_run.py`
- Modify: `tests/test_logs.py`
- Modify: `tests/test_agent_status.py`
- Modify: `tests/test_agent_detail.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `ResolvedGroupPaths`
- Produces: runtime group mapping keys `workspace_root`, `group_root`, `observations`, `proposals`, `decisions`, `locks`, `logs`
- Produces: all run markers and logs below `group_root/logs`

- [ ] **Step 1: Add failing operational-path tests**

In `tests/test_dispatch_run.py`, configure separate roots and assert:

```python
run_dispatch_cycle(snapshot, config_path, launcher=fake_launcher)

assert (group_root / "logs").is_dir()
assert not (workspace / "shared").exists()
```

In `tests/test_logs.py`, assert log-view traversal is rooted at `group_root / "logs"` and rejects a file under the workspace.

In `tests/test_agent_detail.py`, create logs and activity documents directly under the group root:

```python
(group_root / "logs" / "2026-07-22").mkdir(parents=True)
(group_root / "observations").mkdir(parents=True)
(group_root / "proposals").mkdir(parents=True)
```

- [ ] **Step 2: Run operational tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_dispatch_run.py tests\test_logs.py tests\test_agent_status.py tests\test_agent_detail.py tests\test_dashboard.py -q
```

Expected: dispatch and activity code still searches under `<workspace>/shared`.

- [ ] **Step 3: Expose resolved paths in web state**

In `agency/web/state.py`:

```python
paths = resolve_group_paths(group)
return {
    "key": group_id,
    "name": group.name,
    "workspace_root": paths.workspace_root,
    "group_root": paths.group_root,
    "observations": paths.observations,
    "proposals": paths.proposals,
    "decisions": paths.decisions,
    "locks": paths.locks,
    "logs": paths.logs,
    "job_paths": job_store.paths(group_id),
    "agents": list(group.agents.keys()),
    "agents_full": [
        instance.model_dump(mode="json")
        for instance in group.agents.values()
    ],
    "dispatch": group.dispatch.model_dump(mode="json"),
    "runtime": group.runtime.model_dump(mode="json"),
    "workspaces": [
        workspace.model_dump(mode="json")
        for workspace in group.workspaces
    ],
}
```

Remove the ambiguous `"path"` and `"shared"` entries.

- [ ] **Step 4: Move dispatcher and status files**

In `agency/dispatch/run.py`:

```python
paths = resolve_group_paths(group)
log_dir = paths.logs / datetime.now().strftime("%Y-%m-%d")
log_dir.mkdir(parents=True, exist_ok=True)
```

Use `paths.logs` for `.last-*` and `.running-*` markers. Use `paths.workspace_root` for integration execution and source change capture.

In `agency/web/routes/agent_detail.py`, change helper signatures:

```python
def _recent_log_rows(
    group_id: str,
    paths: ResolvedGroupPaths,
    agent_id: str,
) -> list[dict[str, str]]:


def _activity_items(
    group_id: str,
    paths: ResolvedGroupPaths,
    agent_id: str,
    job_store: JobStore | None,
) -> dict[str, Any]:
```

Read from `paths.logs`, `paths.observations`, and `paths.proposals`.

- [ ] **Step 5: Replace operational `shared` use in `agency/app.py`**

For each log/status helper, use runtime-group keys:

```python
logs_dir = g["logs"]
```

Validate requested log paths against `g["logs"].resolve()`. Replace initialization checks with existence of all derived group directories:

```python
initialized = all(
    Path(g[key]).is_dir()
    for key in ("observations", "proposals", "decisions", "locks", "logs")
)
```

Do not alter pipeline route behavior in this task beyond the runtime mapping needed for tests.

- [ ] **Step 6: Run operational tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_dispatch_run.py tests\test_logs.py tests\test_agent_status.py tests\test_agent_detail.py tests\test_dashboard.py -q
```

Expected: all selected tests pass and the workspace remains unchanged.

- [ ] **Step 7: Commit**

```powershell
git add agency\dispatch\run.py agency\web\state.py agency\web\routes\agent_detail.py agency\app.py tests\test_dispatch_run.py tests\test_logs.py tests\test_agent_status.py tests\test_agent_detail.py tests\test_dashboard.py
git commit -m "feat(runtime): move operational records to group roots"
```

---

### Task 6: Move Pipeline Web and CLI Consumers to Group State

**Files:**
- Modify: `agency/app.py`
- Modify: `agency/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_contract.py`
- Modify: `tests/test_decision_prompts.py`
- Modify: `tests/test_execute_decision.py`
- Modify: `tests/test_decision_verify.py`
- Modify: `tests/test_proposal_questions.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: runtime mapping directories from Task 5
- Consumes: `resolve_group_paths(group)`
- Produces: all pipeline reads/writes under `group_root/{observations,proposals,decisions}`

- [ ] **Step 1: Add failing CLI and route tests**

Update pipeline fixtures to place documents directly under the group root. Add:

```python
def test_cli_reads_group_records_without_workspace_shared(
    cli_runner, config_path, workspace, group_root
):
    observation = group_root / "observations" / "signal.md"
    observation.parent.mkdir(parents=True, exist_ok=True)
    observation.write_text("---\nagent: builder\nstatus: open\n---\n# Signal\n")

    result = cli_runner("observations", "--config", str(config_path), "--json")

    assert result.exit_code == 0
    assert "signal" in result.stdout
    assert not (workspace / "shared").exists()
```

Add route assertions that deciding, verifying, and creating follow-up observations touch only `group_root`.

- [ ] **Step 2: Run pipeline tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_cli.py tests\test_cli_contract.py tests\test_decision_prompts.py tests\test_execute_decision.py tests\test_decision_verify.py tests\test_proposal_questions.py tests\test_dashboard.py -q
```

Expected: list/detail/decision code still searches through `shared`.

- [ ] **Step 3: Replace CLI path construction**

In `agency/cli.py`, make `_resolve_group` return explicit roots:

```python
paths = resolve_group_paths(group)
return {
    "key": group_id,
    "name": group.name,
    "workspace_root": paths.workspace_root,
    "group_root": paths.group_root,
    "observations": paths.observations,
    "proposals": paths.proposals,
    "decisions": paths.decisions,
    "logs": paths.logs,
    "agents": list(group.agents),
    "_agents_normalized": [
        {
            "name": instance.name,
            "integration": instance.integration,
            "integration_config": dict(instance.integration_config),
            "capabilities": {"write": instance.capabilities.write},
        }
        for instance in group.agents.values()
    ],
    "_snapshot": snapshot,
    "_group_config": group,
}
```

Change `_markdown_items` to consume the already resolved directory:

```python
def _markdown_items(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        metadata, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        items.append(
            {
                **metadata,
                "_slug": path.stem,
                "_title": _extract_title(body, path.stem),
                "_path": path,
            }
        )
    return items
```

Call it with `paths.observations`, `paths.proposals`, or `paths.decisions`. Decision writes use:

```python
proposal_path = runtime_group["proposals"] / f"{args.slug}.md"
decision_path = runtime_group["decisions"] / f"{args.slug}.md"
```

- [ ] **Step 4: Replace pipeline route paths**

In `agency/app.py`, change the current `list_markdown_items` helper to accept a
concrete directory while preserving its TTL behavior:

```python
def list_markdown_items(
    item_dir: Path,
    apply_ttl: bool = False,
) -> list[dict]:
    if not item_dir.exists():
        return []
    items = []
    for path in sorted(item_dir.glob("*.md"), reverse=True):
        raw = path.read_text()
        meta, body = parse_frontmatter(raw)
        meta.update(
            {
                "_filename": path.name,
                "_body": body,
                "_slug": path.stem,
                "_title": extract_display_title(body, path.stem),
            }
        )
        if apply_ttl:
            enforce_ttl(path, meta)
        items.append(meta)
    return items


def list_observations(g: dict) -> list[dict]:
    return list_markdown_items(g["observations"], apply_ttl=True)


def list_proposals(g: dict) -> list[dict]:
    return list_markdown_items(g["proposals"], apply_ttl=True)


def list_decisions(g: dict) -> list[dict]:
    return list_markdown_items(g["decisions"])
```

Replace every pipeline construction:

```python
g["shared"] / "observations"  -> g["observations"]
g["shared"] / "proposals"     -> g["proposals"]
g["shared"] / "decisions"     -> g["decisions"]
g["shared"] / "logs"          -> g["logs"]
```

Update follow-up observation creation:

```python
observations_dir = g["observations"]
observations_dir.mkdir(parents=True, exist_ok=True)
```

Keep existing frontmatter, status transitions, POST/303 behavior, and traversal checks unchanged apart from the authority root.

- [ ] **Step 5: Run pipeline tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_cli.py tests\test_cli_contract.py tests\test_decision_prompts.py tests\test_execute_decision.py tests\test_decision_verify.py tests\test_proposal_questions.py tests\test_dashboard.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Search application code for old path construction**

Run:

```powershell
rg -n 'group\.path\s*/\s*"shared"|\["shared"\]|/\s*"shared"' agency
```

Expected: no matches.

- [ ] **Step 7: Commit**

```powershell
git add agency\app.py agency\cli.py tests\test_cli.py tests\test_cli_contract.py tests\test_decision_prompts.py tests\test_execute_decision.py tests\test_decision_verify.py tests\test_proposal_questions.py tests\test_dashboard.py
git commit -m "feat(pipeline): read records from group roots"
```

---

### Task 7: Update Group Administration and Setup Contracts

**Files:**
- Modify: `agency/configuration/patches.py`
- Modify: `agency/web/routes/admin_groups.py`
- Modify: `agency/templates/admin_org_edit.html`
- Modify: `agency/templates/admin_groups.html`
- Modify: `agency/web/setup_flow.py`
- Modify: `tests/test_config_patches.py`
- Modify: `tests/test_group_settings.py`
- Modify: `tests/test_setup_flow.py`
- Modify: `tests/test_interactive_setup.py`
- Modify: `tests/test_admin_org_sandbox.py`

**Interfaces:**
- Consumes: schema version 3 and `ResolvedGroupPaths`
- Produces: patches with `workspace_path: str` and `path: str`
- Produces: forms labeled “Workspace path” and “Group path”

- [ ] **Step 1: Write failing patch and form tests**

Update patch construction tests:

```python
GroupSettingsPatch(
    name="Editorial",
    workspace_path=str(workspace),
    path=str(group_root),
    default_integration="copilot",
)
```

Assert raw output contains both fields and preserves unrelated keys.

In `tests/test_group_settings.py`, assert:

```python
assert 'name="workspace_path"' in response.text
assert 'name="path"' in response.text
assert "Workspace path" in response.text
assert "Group path" in response.text
assert "shared/" not in response.text
assert "/initialize" not in response.text
```

POST both values in create/save tests and assert:

```python
group = store.load().config.groups["newsletter"]
assert group.workspace_path == workspace.resolve()
assert group.path == group_root.resolve()
```

- [ ] **Step 2: Run admin/setup tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_config_patches.py tests\test_group_settings.py tests\test_setup_flow.py tests\test_interactive_setup.py tests\test_admin_org_sandbox.py -q
```

Expected: patch dataclasses and forms expose only the old `path`.

- [ ] **Step 3: Update patch contracts**

In `agency/configuration/patches.py`, define:

```python
@dataclass(frozen=True)
class GroupSettingsPatch:
    name: str
    workspace_path: str
    path: str
    default_integration: str
```

Apply the same two fields to `GroupSettingsStatePatch` and `GroupCreateStatePatch`. Every group create/update writes:

```python
group["workspace_path"] = patch.workspace_path
group["path"] = patch.path
```

Keep extension-key preservation and atomic store behavior unchanged.

- [ ] **Step 4: Update routes and locking**

In `agency/web/routes/admin_groups.py`, parse:

```python
workspace_path = str(form.get("workspace_path", "")).strip()
path = str(form.get("path", "")).strip()
```

For edits, acquire operation locks for the existing and proposed group roots:

```python
with revision_bound_group_operation(
    services.config_store,
    group_ids=(org,),
    proposed_paths=(_canonical_group_path(services.config_path, path),),
    expected_revision=revision,
) as locked:
```

`workspace_path` is validated by the candidate config but is not an operation-lock location.

For create, require key, name, workspace path, and group path. Populate template context with:

```python
"workspace_path": str(group.workspace_path),
"group_path": str(group.path),
```

Group list rows expose both and determine initialization from the five standard child directories. Remove the obsolete `/initialize` form because valid configuration writes initialize storage automatically.

- [ ] **Step 5: Update templates and setup prompt**

In `agency/templates/admin_org_edit.html`, replace the single path field with:

```html
<div>
  <label for="workspace_path">Workspace path</label>
  <input type="text" name="workspace_path" id="workspace_path"
         value="{{ workspace_path }}" required>
  <p>Project source and agent execution location.</p>
</div>
<div>
  <label for="path">Group path</label>
  <input type="text" name="path" id="path"
         value="{{ group_path }}" required>
  <p>Agency-owned observations, proposals, decisions, locks, and logs.</p>
</div>
```

Preserve the existing Tailwind classes from the old field.

In `agency/templates/admin_groups.html`, display both labeled paths and remove “Not initialized (shared/ missing)” plus the nonexistent initialize action.

In `agency/web/setup_flow.py`, make the setup prompt explicitly require schema version 3 and both group paths:

```python
"Configure schema_version: 3. For every group, set workspace_path to the "
"project execution workspace and path to a disjoint Agency-owned group root. "
"Never create or reference a project-local shared directory. "
```

- [ ] **Step 6: Run admin/setup tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_config_patches.py tests\test_group_settings.py tests\test_setup_flow.py tests\test_interactive_setup.py tests\test_admin_org_sandbox.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```powershell
git add agency\configuration\patches.py agency\web\routes\admin_groups.py agency\templates\admin_org_edit.html agency\templates\admin_groups.html agency\web\setup_flow.py tests\test_config_patches.py tests\test_group_settings.py tests\test_setup_flow.py tests\test_interactive_setup.py tests\test_admin_org_sandbox.py
git commit -m "feat(admin): separate workspace and group paths"
```

---

### Task 8: Align Guidance, Fixtures, Reload Policy, and Repository Contracts

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `kb/configuration.md`
- Modify: `kb/directory-structure.md`
- Modify: `kb/data-formats.md`
- Modify: `kb/getting-started.md`
- Modify: `kb/setup-skill.md`
- Modify: `skills/agency-setup/SKILL.md`
- Modify: `skills/agency-setup/references/templates.md`
- Modify: `examples/code-review-team/README.md`
- Modify: `examples/content-team/README.md`
- Modify: `tests/ui/fixtures/config.yaml`
- Modify: `tests/ui/server.py`
- Modify: `agency/app.py`
- Modify: `tests/test_agency_setup_skill.py`
- Modify: `tests/test_repository_boundaries.py`
- Modify: `tests/test_server.py`
- Modify: affected tests containing inline schema version 2 job/config builders

**Interfaces:**
- Consumes: all prior task interfaces
- Produces: one consistent schema version 3 repository contract
- Produces: no active-code dependency on the `shared` directory segment

- [ ] **Step 1: Add repository-boundary and reload tests**

In `tests/test_repository_boundaries.py`, add:

```python
def test_application_does_not_construct_project_local_shared_paths(repo_root):
    import re

    patterns = (
        re.compile(r'group\.path\s*/\s*["\']shared["\']'),
        re.compile(r'\[["\']shared["\']\]'),
        re.compile(r'/\s*["\']shared["\']'),
    )
    matches = []
    for path in (repo_root / "agency").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                matches.append(f"{path.relative_to(repo_root)}:{line_number}:{line}")
    assert not matches, "\n".join(matches)
```

In `tests/test_server.py`, remove `deep/shared/jobs/job.yaml` from the generic excluded-directory list. Add an external group-root event and assert it is outside the watched project root rather than specially excluded:

```python
external_group_log = tmp_path / "agency-data/groups/newsletter/logs/run.out"
external_group_log.parent.mkdir(parents=True)
external_group_log.write_text("probe", encoding="utf-8")
assert not supervisor.watch_filter(external_group_log.resolve())
```

- [ ] **Step 2: Run contract tests to verify they fail**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_agency_setup_skill.py tests\test_repository_boundaries.py tests\test_server.py -q
```

Expected: old setup examples and active code/docs still describe project-local `shared`.

- [ ] **Step 3: Update canonical guidance and templates**

Every current example must use:

```yaml
schema_version: 3
groups:
  example:
    workspace_path: C:/Projects/example
    path: C:/Agency/groups/example
```

Document the storage tree:

```text
agent-library/
compiled-agents/
memory/
`-- .jobs/
groups/
`-- <group-id>/
    |-- observations/
    |-- proposals/
    |-- decisions/
    |-- locks/
    `-- logs/
```

Apply these exact semantic statements consistently:

- `workspace_path` is the execution workspace and source repository.
- `path` is the Agency-owned group root.
- The group root is automatically available to restricted agents.
- Durable jobs live in `memory_store/.jobs`.
- Operation locks live in `<group.path>/locks`.
- Agency never loads or creates `<workspace_path>/shared`.

Update `skills/agency-setup/SKILL.md` and its templates so generated configs contain both mandatory paths and never mention conversion.

- [ ] **Step 4: Update UI fixtures and all strict schema builders**

Change `tests/ui/fixtures/config.yaml` and `tests/ui/server.py` to schema version 3 with separate temporary workspace/group roots.

Replace remaining configuration fixtures that omit `schema_version` and remaining job builders using `schema_version=2`, `workspace_dir`, or `group_path`. Use job schema version 3 fields:

```python
schema_version=3,
workspace_root=str(workspace.resolve()),
group_root=str(group_root.resolve()),
```

This is a strict cutover; do not retain helper aliases for old tests.

- [ ] **Step 5: Remove the reload `shared` special case**

In `agency/app.py`, remove `"shared"` from the ignored directory-component set. External group roots are naturally outside the reload root; project-local `shared` has no Agency meaning in schema version 3.

Keep exclusions for `.git`, virtual environments, Python/test/tool caches, package metadata, and build artifacts.

- [ ] **Step 6: Run focused guidance and contract tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\test_agency_setup_skill.py tests\test_repository_boundaries.py tests\test_server.py tests\test_cli_contract.py tests\test_setup_flow.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Run a repository-wide stale-reference search**

Run:

```powershell
rg -n 'schema_version:\s*2|group\.path.*/.*shared|\["shared"\]|shared/(observations|proposals|decisions|jobs|logs)|workspace_dir|group_path=' agency tests CLAUDE.md README.md kb skills examples
```

Expected: no active schema/path matches. Historical approved plans/specifications may retain old descriptions and are outside this command's path set except the newly approved design and plan.

- [ ] **Step 8: Run the full Python test suite**

Run:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

Expected: all tests pass.

- [ ] **Step 9: Inspect the repository boundary**

Run:

```powershell
git status --short
Get-ChildItem -Force .\shared -ErrorAction SilentlyContinue
```

Expected:

- no implementation step created or modified a repository-local `shared` directory;
- only intended source, tests, fixtures, and documentation are changed;
- pre-existing untracked `shared` data, if present, is untouched.

- [ ] **Step 10: Commit**

```powershell
git add CLAUDE.md README.md kb skills examples tests\ui agency\app.py tests
git commit -m "docs(storage): adopt external group roots"
```

---

## Final Verification

- [ ] Run the full suite once more from a clean process:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

Expected: all tests pass.

- [ ] Validate the tracked example configuration:

```powershell
.venv\Scripts\python -c "from pathlib import Path; from agency.configuration import ConfigStore; ConfigStore(Path('tests/ui/fixtures/config.yaml')).load(); print('valid')"
```

Expected:

```text
valid
```

- [ ] Confirm no active application code constructs `shared` paths:

```powershell
rg -n 'group\.path\s*/\s*"shared"|\["shared"\]|/\s*"shared"' agency
```

Expected: no matches.

- [ ] Confirm the final diff does not include user runtime data:

```powershell
git status --short
git diff --stat
```

Expected: `shared/` and `config.yaml.lock` remain untracked and are not staged or committed.
