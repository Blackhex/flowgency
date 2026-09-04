# Schema V6 Team Terminology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Agency domain term `group` with `team` across the strict schema-v6 control plane, runtime state, public interfaces, and active product documentation without preserving any old-format compatibility.

**Architecture:** The rename begins at the authoritative configuration model and propagates through typed callers. Durable jobs and hash-addressed memory selectors receive explicit current-format version changes, then CLI/public route/template contracts move to team terminology. The parser, worker, and serializers reject older inputs; no aliases, redirects, conversion, or migration path is retained.

**Tech Stack:** Python 3.13, Pydantic, FastAPI/Jinja2, argparse, PyYAML, pytest, Playwright. No new dependencies.

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-09-04-v6-team-terminology-design.md` at commit `dcb9c85`.
- Work from `C:/Projekty/christag-agency/.worktrees/v6-team-terminology` on `refactor/v6-team-terminology` until integration.
- Baseline: **1998 passed, 5 skipped, 0 failed**.
- This is a strict breaking rename. Do not add aliases, dual reads, redirects, deprecation paths, conversion, or startup fallback.
- `schema_version: 6`, root `teams`, and `agency.default_team` are the only accepted control-plane shape.
- The durable job schema is `5` only, serializing `team_key` and `team_root`; prior job records must fail strict parse.
- Memory selector scope is `team`; canonical selector criteria use `team`. No old selector hash is read.
- Rename CLI selection to `--team`, worker selection to `--team-id`, administration routes to `/admin/teams`, and team-scoped routes to `/{team}/...`. Remove prior public forms.
- Generated defaults and active examples use `<root>/teams/<team-id>`. Do not mechanically modify custom runtime path values beyond tracked fixtures/examples that the test suite creates itself.
- Remove `config migrate`, `agency/configuration/migrate.py`, and `tests/test_config_migrate.py` completely. No supported config version remains for that converter to emit.
- Preserve behavior other than naming, version, serialized key, generated-default directory, and intentional old-format rejection.
- Do not rename Python regex calls/variables that operate on capture groups; Tailwind `group`/`group-*` utilities; HTML `<optgroup>`; or historical docs/plans that describe historical formats.
- Do not modify `config.yaml`, `config.yaml.lock`, live group/team state, logs, build output, or other runtime-local data.
- Do not run a single `real_runtime` case. Run the ordinary full suite only.
- Run tests from the active worktree root with `python -m pytest`.
- Conventional Commits: lowercase imperative subject, no trailing period, 72 characters maximum.
- After implementation, use `superpowers:requesting-code-review`. After review and green verification, follow `AGENTS.md`: fast-forward `master`, rerun tests, push `master` and this feature branch, remove the worktree, retain the branch.

## Rename Matrix

| Existing domain surface | Target | Explicit exclusion |
|---|---|---|
| `groups`, `default_group`, `GroupConfig`, group key/root/path | `teams`, `default_team`, `TeamConfig`, team key/root/path | custom path text remains an operator choice |
| `group_id`, `group_key`, `group_root`, `group_path` | `team_id`, `team_key`, `team_root`, `team_path` | regex capture variables/methods remain |
| `scope: group`, criteria `group` | `scope: team`, criteria `team` | channel/run selectors remain |
| `--group`, `--group-id` | `--team`, `--team-id` | unrelated CLI values remain |
| `/admin/groups`, `/admin/orgs`, `/{group}` | `/admin/teams`, `/admin/teams`, `/{team}` | `<optgroup>` remains |
| Jinja `group`, `group_name`, `groups` contexts | `team`, `team_name`, `teams` | Tailwind CSS `group` remains |
| job `group_key`, `group_root` | `team_key`, `team_root` | job status/trigger behavior remains |

## Shared Interfaces

Task 1 creates the control-plane interfaces consumed by every later task:

```text
# agency/configuration/models.py
CONFIG_SCHEMA_VERSION = 6
MemoryScope = Literal["run", "routine", "agent", "team", "channel"]

class TeamDispatch(BaseModel): ...
class TeamRuntime(BaseModel): ...
class TeamConfig(BaseModel): ...

class AgencySettings(BaseModel):
    default_team: str = ""

class AgencyConfig(BaseModel):
    schema_version: Literal[6]
    teams: dict[str, TeamConfig]

class ParsedConfig(BaseModel):
    @property
    def teams(self) -> dict[str, TeamConfig]
```

```text
# agency/configuration/team_paths.py
@dataclass(frozen=True)
class ResolvedTeamPaths:
    workspace_root: Path
    team_root: Path
    observations: Path
    proposals: Path
    decisions: Path
    locks: Path
    logs: Path

def resolve_team_paths(team: TeamConfig) -> ResolvedTeamPaths
```

Task 2 creates runtime interfaces:

```text
# agency/jobs/models.py
SCHEMA_VERSION = 5
SUPPORTED_SCHEMA_VERSIONS = frozenset({5})

@dataclass(frozen=True)
class JobRequest:
    team_key: str

@dataclass(frozen=True)
class JobSpec:
    schema_version: int
    team_key: str
    workspace_root: str
    team_root: str

    @property
    def resolved_team_root(self) -> Path

# agency/memory/selectors.py
def resolve_memory_selector(
    selector: MemorySelector,
    *,
    job_id: str,
    team_key: str,
    agent_name: str,
    routine_id: str | None,
    channels: Mapping[str, MemoryChannel],
    store_root: Path,
) -> ResolvedMemory
```

---

### Task 1: Replace The Configuration Control Plane With V6 Teams

**Files:**
- Rename: `agency/configuration/group_paths.py` to `agency/configuration/team_paths.py`
- Modify: `agency/configuration/models.py`
- Modify: `agency/configuration/__init__.py`
- Modify: `agency/configuration/effective.py`
- Modify: `agency/configuration/patches.py`
- Modify: `agency/configuration/paths.py`
- Modify: `tests/conftest.py`
- Rename: `tests/_group_helpers.py` to `tests/_team_helpers.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_config_normalization.py`
- Modify: `tests/test_config_patches.py`
- Modify: `tests/test_config_store.py`
- Modify: `tests/test_effective_policy.py`
- Modify: `tests/test_path_validation.py`

**Interfaces:**
- Consumes: current v5 configuration model and all configuration parser/patch tests.
- Produces: v6 `teams` authority, Team-named config types/patch APIs/path helpers, and strict parser rejection of v5/groups/default_group.

- [ ] **Step 1: Write failing v6 parser and public-symbol tests**

In `tests/test_config.py`, replace v5 current-default assertions with:

```python
def test_current_defaults_are_explicit(raw_config, config_paths):
    parsed = parse_config(raw_config, config_paths.config)

    assert parsed.resolved.schema_version == 6
    assert parsed.resolved.agency.default_team == "newsletter"
    team = parsed.teams["newsletter"]
    assert team.runtime.timeout == 1800
    assert team.runtime.permissions.mode == "unrestricted"
    assert team.dispatch.enabled is False
```

Add strict shape coverage:

```python
@pytest.mark.parametrize(
    ("raw_change", "required_code"),
    [
        (lambda raw: raw.__setitem__("schema_version", 5), "schema-version"),
        (lambda raw: raw.__setitem__("groups", raw.pop("teams")), "unknown-root-key"),
        (
            lambda raw: raw["agency"].__setitem__(
                "default_group", raw["agency"].pop("default_team")
            ),
            "unknown-agency-field",
        ),
    ],
)
def test_v6_rejects_prior_group_control_plane(raw_config, config_paths, raw_change, required_code):
    raw_change(raw_config)

    issues = validate_config(raw_config, config_paths.config)

    assert any(issue.code == required_code for issue in issues)
```

Create a direct public-import check in `tests/test_config.py`:

```python
def test_configuration_exports_team_not_group_apis():
    import agency.configuration as configuration

    assert hasattr(configuration, "TeamSettingsPatch")
    assert hasattr(configuration, "ResolvedTeamPaths")
    assert hasattr(configuration, "resolve_team_paths")
    assert not hasattr(configuration, "GroupSettingsPatch")
    assert not hasattr(configuration, "ResolvedGroupPaths")
    assert not hasattr(configuration, "resolve_group_paths")
```

Update `tests/conftest.py` fixture keys before this run: `schema_version: 6`,
`default_team`, and `teams`. Rename its fixture locals to team where they are
Agency-domain values.

- [ ] **Step 2: Run config tests to observe RED**

Run:

```powershell
python -m pytest tests/test_config.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_config_store.py tests/test_effective_policy.py tests/test_path_validation.py -q
```

Expected: collection/import errors for Team-named interfaces plus validation
failures because current parser accepts v5/groups and existing fixtures are not
all moved to v6 yet.

- [ ] **Step 3: Rename the configuration model and parser atomically**

In `agency/configuration/models.py` make these exact control-plane changes:

```python
MemoryScope = Literal["run", "routine", "agent", "team", "channel"]
CONFIG_SCHEMA_VERSION = 6
_ROOT_KEYS = {"schema_version", "agency", "memory", "teams"}

class AgencySettings(BaseModel):
    default_team: str = ""

class TeamDispatch(BaseModel):
    ...

class TeamRuntime(BaseModel):
    ...

class TeamConfig(BaseModel):
    ...

class AgencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[6]
    agency: AgencySettings
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    teams: dict[str, TeamConfig]
```

Rename all parser variables/field paths/errors from `group(s)` to `team(s)`.
Require the exact missing/invalid root and default-team diagnostics to say v6 and
manual rewrite. Do not add code that recognizes a `groups` root or a
`default_group` field beyond the existing unknown-key validation.

Rename `group_paths.py` and all imports/usages to `team_paths.py`,
`ResolvedTeamPaths`, and `resolve_team_paths`. Rename configuration patch classes,
functions, private helpers, and `__all__` exports from Group/group to Team/team.
Update `effective.py` and `paths.py` to consume `config.teams` and team-named
resolved paths. Do not modify permission semantics.

- [ ] **Step 4: Update configuration fixtures and helper imports**

Rename `tests/_group_helpers.py` to `tests/_team_helpers.py` using Git-aware
rename. Apply this exact interface:

```python
@dataclass(frozen=True)
class TestTeamPaths:
    key: str
    workspace_root: Path
    state_root: Path


def team_paths(tmp_path: Path, key: str) -> TestTeamPaths: ...


def create_team_environment(
    tmp_path: Path,
    key: str,
    *,
    workspace_entries: tuple[str, ...] = (),
    team_dirs: tuple[str, ...] = (),
    create_workspace: bool = True,
    create_state: bool = False,
) -> TestTeamPaths: ...


def apply_team_paths(team: dict, paths: TestTeamPaths) -> dict: ...
```

The helper's fixture-owned state root changes from `tmp_path / "groups" / key`
to `tmp_path / "teams" / key`. Update all imports within the files named in this
task and leave remaining consumers for their owning later tasks.

- [ ] **Step 5: Run the configuration regression slice**

Run:

```powershell
python -m pytest tests/test_config.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_config_store.py tests/test_effective_policy.py tests/test_path_validation.py -q
```

Expected: v6 control-plane tests pass. Any remaining failures must identify a
consumer still expecting `groups` and belong to Task 2, 3, or 4; do not add a
compatibility alias to make it pass.

- [ ] **Step 6: Commit the v6 control plane**

```powershell
git add agency/configuration tests/conftest.py tests/_team_helpers.py tests/test_config.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_config_store.py tests/test_effective_policy.py tests/test_path_validation.py
git commit -m "refactor(config): rename groups to teams in v6"
```

---

### Task 2: Rename Durable Jobs, Memory, And Runtime Team State

**Files:**
- Modify: `agency/jobs/__init__.py`
- Modify: `agency/jobs/artifacts.py`
- Modify: `agency/jobs/authority.py`
- Modify: `agency/jobs/execution.py`
- Modify: `agency/jobs/launcher.py`
- Modify: `agency/jobs/models.py`
- Modify: `agency/jobs/queue.py`
- Modify: `agency/jobs/reconciliation.py`
- Modify: `agency/jobs/resolution.py`
- Modify: `agency/jobs/store.py`
- Modify: `agency/jobs/submission.py`
- Modify: `agency/jobs/worker.py`
- Modify: `agency/memory/selectors.py`
- Modify: `agency/memory/publication.py`
- Modify: `agency/memory/recovery.py`
- Modify: `agency/permissions/eligibility.py`
- Modify: `agency/prompts/catalog.py`
- Modify: `agency/prompts/service.py`
- Modify: `agency/prompts/store.py`
- Modify: `agency/records/ingest.py`
- Modify: `agency/records/validation.py`
- Modify: `agency/instances.py`
- Modify: `agency/workspaces/*.py`
- Test: `tests/test_job_*.py`, `tests/test_memory_*.py`, `tests/test_instances.py`, `tests/test_permission_*.py`, `tests/test_workspaces.py`, `tests/test_write_boundary_contract.py`

**Interfaces:**
- Consumes: Task 1 `config.teams`, `TeamConfig`, and `resolve_team_paths`.
- Produces: current job schema 5 with team serialization, team selector hashes,
  team-named runtime APIs, and `--team-id` worker protocol.

- [ ] **Step 1: Write failing strict job and selector tests**

In `tests/test_job_models.py`, add:

```python
def test_current_job_schema_serializes_team_fields(job_spec):
    current = replace(
        job_spec,
        schema_version=5,
        team_key="newsletter",
        team_root=str(Path(job_spec.team_root).with_name("newsletter")),
    )

    payload = current.to_dict()

    assert payload["schema_version"] == 5
    assert payload["team_key"] == "newsletter"
    assert "team_root" in payload
    assert "group_key" not in payload
    assert "group_root" not in payload


@pytest.mark.parametrize("schema_version", [3, 4])
def test_job_rejects_prior_schema_versions(job_spec, schema_version):
    with pytest.raises(ValueError, match="Unsupported job schema version"):
        replace(job_spec, schema_version=schema_version).validate()
```

In `tests/test_memory_selectors.py`, add:

```python
def test_team_memory_selector_uses_team_scope_and_criteria(tmp_path):
    selector = MemorySelector(scope="team")

    resolved = resolve_memory_selector(
        selector,
        job_id="job-1",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path,
    )

    assert json.loads(resolved.canonical_json) == {
        "scope": "team",
        "team": "newsletter",
        "version": 1,
    }


def test_group_memory_scope_is_rejected():
    with pytest.raises(ValidationError):
        MemorySelector(scope="group")
```

Add a worker parser test that invokes `agency.jobs.worker` with `--team-id` and
asserts `--group-id` fails argument parsing. Rename existing job/memory fixtures
to `team_key` and `team_root` before running.

- [ ] **Step 2: Run runtime tests to observe RED**

```powershell
python -m pytest tests/test_job_models.py tests/test_job_authority.py tests/test_job_submission.py tests/test_job_queue.py tests/test_job_reconciliation.py tests/test_memory_selectors.py tests/test_memory_recovery.py tests/test_instances.py tests/test_permission_resolution.py tests/test_workspaces.py -q
```

Expected: constructor/type errors for team fields, v4 job acceptance, group
selector acceptance, and worker flag failures.

- [ ] **Step 3: Make job schema 5 strict and rename serialized fields**

In `agency/jobs/models.py`:

```python
SCHEMA_VERSION = 5
SUPPORTED_SCHEMA_VERSIONS = frozenset({5})
```

Rename `JobRequest.group_key`, `JobSpec.group_key`, `JobSpec.group_root`, and
`resolved_group_root` to team equivalents. Update `validate()`, `to_dict()`, and
`from_dict()` so version 5 only accepts the new dataclass fields. Remove the
v3/v4 prompt compatibility branches and the old `sandbox_mode` conversion in
`from_dict()`. Keep the current prompt-backed validation logic as one v5 method.
Preserve immutable digest behavior except that it naturally hashes renamed v5
payload keys.

Rename domain APIs and locals in all listed jobs modules: authority references,
queue entries, lock helpers, reconciliation maps, worker invocation, resolution,
execution, submission, artifacts diagnostics, and dispatch-facing helpers. The
worker command builder and parser must switch together to `--team-id`.

- [ ] **Step 4: Rename selector canonical JSON without old hash fallback**

In `agency/memory/selectors.py`, change `resolve_memory_selector(... team_key)`
and use this exact expected-key mapping:

```python
expected_keys = {
    "run": {"version", "scope", "job"},
    "routine": {"version", "scope", "team", "agent", "routine"},
    "agent": {"version", "scope", "team", "agent"},
    "team": {"version", "scope", "team"},
    "channel": {"version", "scope", "channel"},
}[selector.scope]
```

Emit `criteria.update(team=team_key, ...)` for routine/agent and
`criteria["team"] = team_key` for team scope. Do not inspect old hash paths,
old criteria names, or `scope == "group"`.

Rename team-domain parameters and diagnostics in memory publication/recovery,
prompt store/catalog/service, instances, permissions, records, and workspaces.
Do not rename `match.group()` or `match.groups()` in regex code.

- [ ] **Step 5: Update tests and test-state paths**

Mechanically replace domain test kwargs/attributes (`group_key`, `group_root`,
`group_id`) with team forms in the files listed above. Change fixture-owned
state paths from `groups` to `teams`; preserve arbitrary paths named `group`
only when they do not represent Agency team state. Update memory scope fixtures
from `"group"` to `"team"` and expected canonical JSON field names together.

Add direct strict-rejection tests for a v4 job payload holding `group_key` /
`group_root` and a canonical selector JSON with `group`: both must raise rather
than be translated.

- [ ] **Step 6: Run runtime regression slices**

```powershell
python -m pytest tests/test_job_models.py tests/test_job_authority.py tests/test_job_submission.py tests/test_job_queue.py tests/test_job_reconciliation.py tests/test_job_routes.py tests/test_memory_selectors.py tests/test_memory_recovery.py tests/test_memory_publication.py tests/test_memory_store.py tests/test_instances.py tests/test_permission_resolution.py tests/test_permission_capabilities.py tests/test_workspaces.py tests/test_write_boundary_contract.py -q
```

Expected: all renamed runtime behavior passes, and old job/selector formats fail
only through explicit tests.

- [ ] **Step 7: Commit runtime team state**

```powershell
git add agency/jobs agency/memory agency/permissions agency/prompts agency/records agency/instances.py agency/workspaces tests/test_job_*.py tests/test_memory_*.py tests/test_instances.py tests/test_permission_*.py tests/test_workspaces.py tests/test_write_boundary_contract.py
git commit -m "refactor(runtime): rename group state to team state"
```

---

### Task 3: Rename Dispatch, CLI, And Setup Public Contracts

**Files:**
- Modify: `agency/cli.py`
- Modify: `agency/cli_output.py`
- Modify: `agency/dispatch/*.py`
- Modify: `agency/web/setup_flow.py`
- Modify: `agency/app.py`
- Delete: `agency/configuration/migrate.py`
- Delete: `tests/test_config_migrate.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_contract.py`
- Modify: `tests/test_dispatch_*.py`
- Modify: `tests/test_setup_flow.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Consumes: Task 1 config APIs and Task 2 job/worker team APIs.
- Produces: `--team`, `--team-id`, team diagnostics/output, team setup prompt,
  strict v6 initial setup status, and dispatch loops over `config.teams`.

- [ ] **Step 1: Write failing CLI and setup v6 contract tests**

In `tests/test_cli_contract.py`, replace group selection with:

```python
def test_cli_selects_explicit_or_default_team(cli_config, cli_runner):
    assert cli_runner("agents", "--team", "newsletter", config=cli_config).returncode == 0
    assert cli_runner("agents", config=cli_config).returncode == 0


def test_cli_rejects_removed_group_flag(cli_config, cli_runner):
    result = cli_runner("agents", "--group", "newsletter", config=cli_config)

    assert result.returncode != 0
    assert "--group" in result.stderr
```

In `tests/test_setup_flow.py`, require:

```python
def test_build_setup_prompt_emits_v6_team_shape(tmp_path: Path):
    prompt = build_setup_prompt(tmp_path, tmp_path / "config.yaml", selected_integration="copilot")

    for phrase in (
        "first team project workspace",
        "team display name and stable team ID",
        "agency.default_team",
        "teams.<team-id>.path as <root>/teams/<team-id>",
        "Configure schema_version: 6.",
    ):
        assert phrase in prompt
    assert "group.default_integration" not in prompt
    assert "groups.<group-id>" not in prompt
```

- [ ] **Step 2: Run CLI/dispatch/setup tests to observe RED**

```powershell
python -m pytest tests/test_cli.py tests/test_cli_contract.py tests/test_dispatch_*.py tests/test_setup_flow.py tests/test_server.py tests/test_agency_setup_skill.py -q
```

Expected: CLI parser does not recognize `--team`, setup text still emits v5
schema/groups, and dispatch callers still consume `config.groups`.

- [ ] **Step 3: Rename CLI parser, output, and dispatch values**

Rename `_group_id`, `_group`, `_resolve_group`, `--group`, default lookup,
diagnostics, and output keys in `agency/cli.py` to team forms. Remove the
`config migrate` subcommand, handler, imports, and any migrated-output code.
In the same task, delete `agency/configuration/migrate.py` and
`tests/test_config_migrate.py`; after deletion, run `git grep -n
"migrate_v4_to_v5" -- agency tests` and require exit 1.

Change every dispatch loop/map/signature from `groups` to `teams`, consuming
`resolved.teams` and team-named job APIs. Preserve scheduling decisions, timeout,
locking order, and status formatting.

- [ ] **Step 4: Update setup prompt and setup status**

In `agency/web/setup_flow.py`, replace all domain prose with team terminology
and use only these config literals:

```text
agency.default_team
teams.<team-id>.path as <root>/teams/<team-id>
schema_version: 6
workspace_path to the approved team project execution workspace
```
Change setup readiness from `config.groups` to `config.teams`. Preserve guided
session launch, data-root safety, skill discovery, prompt content ordering, and
canonical-config completion behavior.

- [ ] **Step 5: Update test fixtures and run contract regressions**

Update CLI config writers, dispatch test configs, setup fake config payloads,
and setup-skill assertions to v6 team naming. Assert no `config migrate` parser
command remains and no v4/v5 corrective hint remains in active CLI output.

Run:

```powershell
python -m pytest tests/test_cli.py tests/test_cli_contract.py tests/test_dispatch_*.py tests/test_setup_flow.py tests/test_server.py tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py -q
```

Expected: team CLI/setup/dispatch contracts pass; old CLI option/migration forms
fail or are absent without compatibility behavior.

- [ ] **Step 6: Commit public command and setup contracts**

```powershell
git add agency/cli.py agency/cli_output.py agency/dispatch agency/web/setup_flow.py agency/app.py tests/test_cli.py tests/test_cli_contract.py tests/test_dispatch_*.py tests/test_setup_flow.py tests/test_server.py tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py
git commit -m "refactor(cli): replace group interfaces with teams"
```

---

### Task 4: Replace Web Routes, Context, And Templates

**Files:**
- Modify: `agency/app.py`
- Modify: `agency/web/state.py`
- Modify: `agency/web/routes/admin_groups.py`
- Modify: `agency/web/routes/admin_library.py`
- Modify: `agency/web/routes/admin_memory.py`
- Modify: `agency/web/routes/agents.py`
- Modify: `agency/web/routes/agent_detail.py`
- Modify: `agency/web/routes/jobs.py`
- Modify: every affected `agency/templates/*.html`
- Modify: route/dashboard/admin/agent/job tests
- Modify: `tests/ui/server.py`
- Modify: `tests/ui/fixtures/config.yaml`
- Modify: `tests/ui/*.spec.ts`

**Interfaces:**
- Consumes: Task 1-3 Team-named model, runtime, CLI, dispatch, and setup APIs.
- Produces: only team URLs/context/template labels/forms/navigation; old group/admin-org URLs absent.

- [ ] **Step 1: Write failing route absence and team navigation tests**

Add to `tests/test_surface_contracts.py`:

```python
def test_active_routes_use_team_placeholders_not_group_placeholders():
    paths = {
        route.path
        for route in app_mod.app.routes
        if hasattr(route, "path")
    }

    assert "/admin/teams" in paths
    assert "/admin/groups" not in paths
    assert not any("{group}" in path or "/admin/orgs" in path for path in paths)
    assert any("{team}" in path for path in paths)
```

Update a real route test to request `/{team}/agents`, and add an assertion that
`/{old-team}/agents` has no registered group-style alias. Use a clean v6 config.

- [ ] **Step 2: Run web tests to observe RED**

```powershell
python -m pytest tests/test_dashboard.py tests/test_agent_detail.py tests/test_agent_roster.py tests/test_agent_run.py tests/test_group_settings.py tests/test_admin_*.py tests/test_job_routes.py tests/test_memory_channel_routes.py tests/test_surface_contracts.py -q
```

Expected: route registry/template context still use group names and old URLs.

- [ ] **Step 3: Rename route decorators and handler/context variables**

Rename only Agency-domain route placeholders from `{group}` to `{team}`.
Replace `/admin/groups` and `/admin/orgs` segments with `/admin/teams`. Rename
handler arguments/local state/context variables to `team`, `team_id`,
`team_name`, `teams`, `team_path`, and `team_*` equivalents. Update redirects,
link generation, form actions, hidden inputs, query names, and navigation maps.

Rename `runtime_group` to `runtime_team`. Rename response helpers and templates
named `admin_groups.html` / `admin_org_edit.html` to team-named files, updating
all `TemplateResponse` references. Preserve route behavior/status codes/POST-303
flow and validation logic.

- [ ] **Step 4: Update templates without touching external group tokens**

Update Jinja context variable references, headings, labels, form `name`/`id`,
route hrefs, and visible copy to team terminology. Keep these exact exclusions:

```html
<optgroup label="Shared from blueprint">
class="... group ..."
group-hover:underline
```

Do not change those HTML/Tailwind constructs. Update memory selector labels and
form values from `group`/`Group memory` to `team`/`Team memory` because those are
serialized Agency domain values.

- [ ] **Step 5: Update Python and Playwright fixtures**

Change UI fixture YAML to v6 `teams` / `default_team` and its fixture-owned
runtime directory from `groups` to `teams`. Rename UI server locals that represent
Agency teams. Update Playwright route/heading/label assertions and snapshots only
when rendered behavior changes. Do not change `<optgroup>` selectors.

- [ ] **Step 6: Run web and UI tests**

```powershell
python -m pytest tests/test_dashboard.py tests/test_agent_detail.py tests/test_agent_roster.py tests/test_agent_run.py tests/test_group_settings.py tests/test_admin_*.py tests/test_job_routes.py tests/test_memory_channel_routes.py tests/test_surface_contracts.py -q
npx playwright test
```

Expected: team routes/forms/dashboard surfaces pass; browser snapshots, keyboard,
and accessibility checks reflect team terminology with no layout regression.

- [ ] **Step 7: Commit web team interfaces**

```powershell
git add agency/app.py agency/web/state.py agency/web/routes agency/templates tests/test_dashboard.py tests/test_agent_*.py tests/test_group_settings.py tests/test_admin_*.py tests/test_job_routes.py tests/test_memory_channel_routes.py tests/test_surface_contracts.py tests/ui
git commit -m "refactor(web): replace group routes with teams"
```

---

### Task 5: Align Active Documentation, Examples, And Terminology Scan

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `config.yaml.example`
- Modify: `agency.service.example`
- Modify: `kb/*.md` where they describe current behavior
- Modify: `examples/**/*.md`
- Modify: `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md`
- Modify: `agency/setup_assets/copilot/.github/skills/agency-setup/references/*.md`
- Modify: tests for active documentation/setup skill
- Create: `tests/test_team_terminology.py`

**Interfaces:**
- Consumes: v6 canonical shape and renamed public interface from Tasks 1-4.
- Produces: active docs/examples/setup assets with current team terminology plus
  a regression scanner that protects non-domain exclusions.

- [ ] **Step 1: Write failing active-documentation and scanner tests**

Create `tests/test_team_terminology.py`:

```python
from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).parents[1]
ACTIVE_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "config.yaml.example",
    REPO_ROOT / "agency.service.example",
    *(REPO_ROOT / "kb").glob("*.md"),
    *(REPO_ROOT / "examples").glob("**/*.md"),
    *(REPO_ROOT / "agency" / "setup_assets").glob("**/*.md"),
)


def test_active_documents_use_v6_team_control_plane():
    text = "\n".join(path.read_text(encoding="utf-8") for path in ACTIVE_PATHS)

    assert "schema_version: 6" in text
    assert "default_team:" in text
    assert "teams:" in text
    assert "schema_version: 5" not in text
    assert "default_group:" not in text
    assert "\ngroups:\n" not in text
    assert "christag-agency config migrate" not in text


def test_setup_assets_use_team_domain_terms():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "agency" / "setup_assets").glob("**/*.md")
    )

    assert "team display name" in text.lower()
    assert "teams.<team-id>" in text
    assert "<root>/teams/<team-id>" in text
    assert not re.search(r"\bgroup display name\b", text, re.IGNORECASE)
```

Add focused exclusions to `tests/test_surface_contracts.py`:

```python
def test_non_domain_group_tokens_remain_unchanged():
    base = (REPO_ROOT / "agency" / "templates" / "base.html").read_text(encoding="utf-8")
    agents = (REPO_ROOT / "agency" / "templates" / "agents.html").read_text(encoding="utf-8")
    schedule = (REPO_ROOT / "agency" / "dispatch" / "schedule.py").read_text(encoding="utf-8")

    assert " group" in base
    assert "group-hover:" in base or "group-hover:" in (REPO_ROOT / "agency" / "templates" / "home.html").read_text(encoding="utf-8")
    assert "<optgroup" in agents
    assert "match.group(" in schedule
```

- [ ] **Step 2: Run docs/scanner tests to observe RED**

```powershell
python -m pytest tests/test_team_terminology.py tests/test_agency_setup_skill.py tests/test_setup_flow.py tests/test_surface_contracts.py -q
```

Expected: active docs/examples/setup assets still contain current group/v5/migrate
terminology while the external regex/CSS/HTML exclusions continue to pass.

- [ ] **Step 3: Update all active current-format documentation**

Replace domain language in active docs/examples with team terminology. Update
config blocks to v6 `teams` / `default_team`; generated paths to `/teams/`.
Remove `config migrate` instructions and mentions. Update setup skill/references
and launch text to team IDs/names/root/workspace while keeping exact YAML field
literals aligned with Task 1.

Do not edit `docs/superpowers/specs/**` or `docs/superpowers/plans/**`; these are
historical design records. Do not alter regex APIs, Tailwind utilities, or
`<optgroup>`.

- [ ] **Step 4: Run active docs and setup tests**

```powershell
python -m pytest tests/test_team_terminology.py tests/test_agency_setup_skill.py tests/test_setup_flow.py tests/test_setup_skill_e2e.py tests/test_surface_contracts.py tests/test_repository_boundaries.py -q
```

Expected: current docs, examples, setup assets, and protected exclusions pass.

- [ ] **Step 5: Commit active terminology documentation**

```powershell
git add README.md AGENTS.md config.yaml.example agency.service.example kb examples agency/setup_assets tests/test_team_terminology.py tests/test_agency_setup_skill.py tests/test_setup_flow.py tests/test_setup_skill_e2e.py tests/test_surface_contracts.py
git commit -m "docs: adopt team terminology for schema v6"
```

---

### Task 6: Complete Repository Migration And Verify The Strict Break

**Files:**
- Modify: all remaining domain-team production/test files identified by:
  `git grep -n -E '\b(group|groups|Group[A-Z]|default_group|group_[a-z]+)\b' -- agency tests`
- Verify: all active docs/examples/setup assets
- Verify: all public CLI, routes, worker, job, and memory contracts

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: no remaining Agency-domain group naming, all tests/fixtures migrated,
  strict v6/team state, and explicit exclusions intact.

- [ ] **Step 1: Create an allowed-exclusion terminology report**

Run this command and save its output to a temporary ignored file:

```powershell
$remaining = git grep -n -E '\b(group|groups|Group[A-Z]|default_group|group_[a-z]+)\b' -- agency tests; $remaining | Set-Content "$env:TEMP\agency-v6-team-remaining.txt"; $remaining
```

Classify every line as one of:

```text
1. Agency domain rename still required
2. regex Match API / regex capture value
3. Tailwind group utility
4. HTML optgroup
5. historical v4 raw input literal in a preserved historical document
6. test variable that deliberately verifies old-format rejection
```

The final implementation may retain only categories 2-6. Do not suppress category
1 with aliases or broad scanner exclusions.

- [ ] **Step 2: Write failing end-to-end strict-break tests**

Add a test to `tests/test_cli_contract.py` that creates a raw v5 config containing
`schema_version: 5`, `default_group`, and `groups`, then asserts `christag-agency
validate --config <path>` exits with validation failure, says `schema_version must
be 6`, and does not mention a migration command.

Add a route test that requests `/admin/groups` and a representative old scoped
path and asserts 404. Add a worker subprocess test where `--group-id` exits
nonzero while `--team-id` accepts a valid current job invocation.

Add a job-record test that writes raw YAML under `.jobs/<team>/<job>.yaml` with
`schema_version: 4` and group fields and asserts `JobRecord.from_dict()` raises.
Add selector JSON tests that reject both scope/key variants of group state.

- [ ] **Step 3: Mechanically migrate remaining domain callers and tests**

Rename all category-1 symbols/files using language-aware rename where available,
then resolve each compile/test failure locally. Rename test files whose names are
part of the domain, including `test_group_settings.py` to `test_team_settings.py`
and `tests/_group_helpers.py` consumers to `_team_helpers.py`. Update all inline
YAML to v6, all fixture paths that represent created team state to `teams`, job
constructor kwargs, MemorySelector values, route URLs, CLI options, and assertions.

Keep error/rejection tests explicitly containing old text only as their input or
negative expected value. Keep the strict-rejection tests from Step 2; do not delete
or soften them when broad replacements encounter them.

- [ ] **Step 4: Run the broad mechanical test matrix**

```powershell
python -m pytest tests/test_config.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_config_store.py tests/test_path_validation.py tests/test_effective_policy.py tests/test_permission_*.py tests/test_executor_eligibility.py tests/test_job_*.py tests/test_memory_*.py tests/test_prompt_*.py tests/test_instances.py tests/test_agent_*.py tests/test_admin_*.py tests/test_dashboard.py tests/test_cli.py tests/test_cli_contract.py tests/test_dispatch_*.py tests/test_server.py tests/test_setup_*.py tests/test_surface_contracts.py tests/test_team_terminology.py tests/test_workspaces.py tests/test_write_boundary_contract.py -q
```

Expected: all current-format tests pass; every old-format case fails only where it
is intentionally tested.

- [ ] **Step 5: Verify no migration or old public interface remains**

```powershell
python -m agency.cli --help
python -m agency.cli config --help
python -c "import agency.configuration as c; assert not hasattr(c, 'GroupConfig'); assert not hasattr(c, 'create_group'); assert hasattr(c, 'TeamConfig'); assert hasattr(c, 'create_team'); print('configuration export boundary passed')"
python -c "from agency.jobs.models import JobSpec; assert 'team_key' in JobSpec.__dataclass_fields__; assert 'group_key' not in JobSpec.__dataclass_fields__; print('job field boundary passed')"
```

Expected: no config-migration subcommand or `--group` argument appears; removed
configuration exports are absent; Team and job fields are current only.

- [ ] **Step 6: Run repository scans with exclusions verified**

```powershell
git grep -n -E 'config migrate|migrate_v4_to_v5|default_group|schema_version: 5|^groups:' -- ':!docs/superpowers/**'
git grep -n -E '"group_key"|"group_root"|--group-id|--group' -- agency tests
```

Expected: both commands exit 1 except for explicitly named old-format rejection
tests. Inspect each remaining match manually. Then run the protected-exclusion
test from Task 5 to prove regex/CSS/HTML tokens still exist.

- [ ] **Step 7: Commit remaining migration consumers**

```powershell
git add agency tests
# Stage deleted and renamed test files discovered in Step 3 as well.
git add -u
git commit -m "refactor: complete strict v6 team terminology"
```

---

### Task 7: Final Validation, Review, And Integration

**Files:**
- Verify: every changed production, template, documentation, example, fixture,
  Python test, and Playwright test file
- Preserve: `refactor/v6-team-terminology` after integration
- Remove after verification: `.worktrees/v6-team-terminology`

**Interfaces:**
- Consumes: all implementation commits.
- Produces: reviewed, published v6 team terminology on master with fresh full
  verification and no completed worktree.

- [ ] **Step 1: Run focused v6 and public-interface checks**

```powershell
python -m pytest tests/test_config.py tests/test_config_patches.py tests/test_cli.py tests/test_cli_contract.py tests/test_job_models.py tests/test_job_authority.py tests/test_memory_selectors.py tests/test_dispatch_run.py tests/test_dashboard.py tests/test_team_settings.py tests/test_setup_flow.py tests/test_agency_setup_skill.py tests/test_team_terminology.py tests/test_surface_contracts.py -q
python -m agency.cli validate --config config.yaml.example
```

Expected: tests pass and validation accepts the v6 example. If the local ignored
`config.yaml` is old, do not validate or modify it; this command uses only the
tracked example.

- [ ] **Step 2: Run full Python and browser suites**

```powershell
python -m pytest tests/ -q
npx playwright test
```

Expected: all Python tests pass with only existing environment-dependent skips;
all configured browser projects pass or snapshots are intentionally updated and
visually reviewed because terminology changed.

- [ ] **Step 3: Perform a clean v6 CLI/dashboard smoke test**

Create a temporary config outside the repository using the canonical v6 fixture
shape. Start `christag-agency serve --config <temp-config> --host 127.0.0.1 --port
8768`, open `/admin/teams` and `/<team>/agents`, and verify team labels/navigation.
Request `/admin/groups` and `/<team>/agents` under the old placeholder form; both
must return 404. Run `christag-agency agents --team <team> --config <temp-config>`
and verify `--group` fails. Stop the server and remove temporary config/storage.

- [ ] **Step 4: Whole-branch review**

Invoke `superpowers:requesting-code-review` against the approved design and the
diff from `dcb9c85` to the current head. Require review of strict old-format
rejection, job/memory serialization, route/CLI/worker contracts, migration removal,
non-domain exclusions, custom-path preservation, test fixture breadth, and
behavioral preservation.

For Critical/Important findings: add a focused failing regression, fix only that
finding, rerun the covering test, commit separately, and repeat Steps 1-4. Record
Minor findings but do not mix unrelated polish into this breaking migration.

- [ ] **Step 5: Fast-forward, verify, push, and clean up**

From `C:/Projekty/christag-agency`, confirm `master` is clean, fetch origin, and
verify `master` plus `origin/master` are ancestors of
`refactor/v6-team-terminology`. Stash unrelated main-checkout changes with
`git stash push --include-untracked` if present; restore them after integration.
If master advanced, fast-forward it; if the feature is no longer descended from
master, rebase the feature, repeat Steps 1-3, and re-review.

Then run:

```powershell
git merge --ff-only refactor/v6-team-terminology
python -m pytest tests/ -q
git push origin master refactor/v6-team-terminology
python -m pip install -e .
git worktree remove .worktrees/v6-team-terminology
git worktree prune
git branch --list refactor/v6-team-terminology
git worktree list
git status --short --branch
```

Expected: master points directly at the reviewed tip; complete suite is green;
both refs are published; editable install references main checkout; worktree is
gone; feature branch remains; restored user changes, if any, remain untouched.
