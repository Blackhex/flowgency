## Subtask A - Setup and Group Settings

Status: completed

Scope delivered:
- `agency.web.routes.admin_groups` now owns HTTP GET/POST `/setup`, GET/POST `/admin/orgs/{org}/edit` and `/admin/orgs/{org}/save`, strict-canonical group create, and removed `/admin/orgs/{org}/dispatch|initialize|autodetect` mutation paths.
- Removed duplicate superseded setup/group-settings/create/dispatch handlers from `agency/app.py` so direct superseded calls no longer exist for these routes.
- Consolidated group settings save into one `ConfigStore.patch(...)` transaction via `patch_group_settings_state(...)` under a single expected revision.
- Preserved unknown extension keys across group/runtime/sandbox/tools/workspaces while updating owned fields.
- Rewrote setup/group-settings tests to use `TestClient` route behavior instead of direct handler calls.

Evidence:
- Focused validation green:
  - `tests/test_group_settings_canonical.py`: 4 passed
  - `tests/test_server.py`: 9 passed
  - `tests/test_admin_org_sandbox.py`: 11 passed
  - `tests/test_config_patches.py`: 8 passed
  - `tests/test_config_store.py`: 7 passed
  - Total: 39 passed
- Command:
  - `.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_server.py tests/test_admin_org_sandbox.py tests/test_config_patches.py tests/test_config_store.py -v`

Notes:
- Remaining known unrelated failure area from user brief still belongs to the next roster pass: superseded physical-agent create assertion in `tests/test_admin_agent_create.py`.

## Subtask B - Sole Instance Roster

Status: completed

Scope delivered:
- `agency.web.routes.agents` is now the sole owner of `/{group}/agents`, config-backed create/remove/move preview/apply, the retained old admin GET redirect, and the Task14 roster/profile placeholder surface.
- Removed superseded duplicate agent route registrations from `agency/app.py` for admin agent mutation, group agent list/profile, identity/definition/headshot/toggle-subagent, and physical directory CRUD assigned to Task 14.
- The roster create form now lists actual blueprint keys from `BlueprintLibrary`; server-side blueprint and integration validation remains in place.
- Roster mutations now carry explicit config revision or preview revision through the web layer, and move apply revalidates the exact preview revision before mutating.
- `tests/test_admin_agent_create.py` now verifies config-only instance creation and rejects invalid blueprints without filesystem scaffolding.
- Temporary `/{group}/agents/{agent}/profile` placeholder remains for Task 15 handoff, but now renders config identity, blueprint, and integration instead of fabricating display identity from the agent id.

RED/GREEN evidence:
- RED: first focused run after router cleanup exposed seven local failures in roster tests and rewritten create tests, mainly due to stale fixture revision sourcing and one route-table assertion mismatch.
- GREEN: `.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_agent_roster.py tests/test_server.py tests/test_admin_org_sandbox.py tests/test_admin_agent_create.py -v`
  - `tests/test_group_settings_canonical.py`: 4 passed
  - `tests/test_agent_roster.py`: 11 passed
  - `tests/test_server.py`: 9 passed
  - `tests/test_admin_org_sandbox.py`: 11 passed
  - `tests/test_admin_agent_create.py`: 2 passed
  - Total: 37 passed
- GREEN: `.venv\Scripts\python.exe -m pytest tests/test_instances.py tests/test_job_submission.py -v`
  - `tests/test_instances.py`: 20 passed
  - `tests/test_job_submission.py`: 26 passed
  - Total: 46 passed

Notes:
- `InstanceService` retains backward-compatible default revision behavior for direct callers, while the web roster routes require explicit page/preview revisions to block stale form submissions.
- Removed root `config.yaml.lock` artifact after validation.

## Subtask C - Full-suite Regression Closure

Status: completed

Scope delivered:
- Fixed the Task 14 roster create seam by passing `AgentInstanceCreate` and `expected_revision` to `InstanceService.create(...)` in the correct order, eliminating the `AttributeError: 'str' object has no attribute 'blueprint'` crash.
- Hardened roster blueprint availability so missing or unreadable Agent Library roots render an actionable `409` warning on `/{group}/agents` instead of raising a `500`, and added route coverage for that failure mode without mutating config.
- Restored Task 2 sandbox ownership by explicitly rejecting group-owned `runtime.sandbox.additional_roots` while preserving extension-key behavior elsewhere.
- Rewrote stale Task 14 tests that still targeted removed v1 dispatch and roster UI so they assert the approved canonical contract: group settings owns runtime defaults plus dispatch enabled/daily limit, and the roster owns config instances plus blueprint/integration/current job only.
- Kept config-only instance creation semantics intact across roster and manual routine execution: the approved `/{group}/agents/{agent}/run` route now resolves group, instance, and routine from the request-scoped strict canonical config snapshot and no longer requires a physical instance directory.

RED/GREEN/full-suite evidence:
- RED: `.venv\Scripts\python.exe -m pytest tests/test_agent_roster.py tests/test_admin_dispatch.py tests/test_config_canonical.py -q`
  - 6 failures initially exposed the local seam and stale test contract drift:
    - roster create passed `expected_revision` into the `request` slot for `InstanceService.create(...)`
    - stale admin dispatch tests still expected removed per-agent schedule controls
    - group sandbox ownership regression allowed `runtime.sandbox.additional_roots` at group scope
- GREEN: `.venv\Scripts\python.exe -m pytest tests/test_agent_roster.py tests/test_admin_agent_create.py tests/test_config_canonical.py -q`
  - 116 passed
- GREEN: `.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_agent_roster.py tests/test_server.py tests/test_admin_org_sandbox.py tests/test_admin_agent_create.py tests/test_admin_dispatch.py tests/test_agent_run.py tests/test_config_canonical.py -q`
  - 172 passed
- GREEN: `.venv\Scripts\python.exe -m pytest tests/test_instances.py tests/test_config_patches.py tests/test_config_store.py -q`
  - 35 passed
- GREEN: `.venv\Scripts\python.exe -m pytest tests/ -q`
  - 1007 passed, 3 skipped

Route ownership verification:
- Source inspection confirms exactly one setup GET/POST pair in `agency/web/routes/admin_groups.py`:
  - `@router.get("/setup")`
  - `@router.post("/setup")`
- Source inspection confirms exactly one Group Settings GET/POST pair in `agency/web/routes/admin_groups.py`:
  - `@router.get("/admin/orgs/{org}/edit")`
  - `@router.post("/admin/orgs/{org}/save")`
- Source inspection confirms exactly one roster GET/create/remove/move set in `agency/web/routes/agents.py`:
  - `@router.get("/{group}/agents")`
  - `@router.post("/{group}/agents/create")`
  - `@router.post("/{group}/agents/{agent}/remove")`
  - `@router.post("/{group}/agents/{agent}/move")`
  - `@router.post("/{group}/agents/{agent}/move/apply")`
- Removed superseded agent mutation routes are absent from `agency/app.py` for the Task 14-owned surfaces (`/admin/orgs/{org}/agents/create`, `/admin/orgs/{org}/agents/{agent}/save`, `/{group}/agents/{agent}/identity`, `/{group}/agents/{agent}/toggle-subagent`, etc.).
- Old admin GET redirect is retained in `agency/web/routes/agents.py` as `@router.get("/admin/orgs/{group}/agents/{agent}")`.

Changed files:
- `agency/app.py`
- `agency/configuration/__init__.py`
- `agency/configuration/models.py`
- `agency/configuration/patches.py`
- `agency/instances.py`
- `agency/templates/admin_org_edit.html`
- `agency/templates/agent_profile.html`
- `agency/templates/agents.html`
- `agency/templates/setup.html`
- `agency/templates/agent_move.html`
- `agency/web/__init__.py`
- `agency/web/dependencies.py`
- `agency/web/routes/__init__.py`
- `agency/web/routes/admin_groups.py`
- `agency/web/routes/agents.py`
- `tests/test_admin_agent_create.py`
- `tests/test_admin_dispatch.py`
- `tests/test_admin_org_sandbox.py`
- `tests/test_agent_run.py`
- `tests/test_agent_roster.py`
- `tests/test_config_patches.py`
- `tests/test_group_settings_canonical.py`
- `tests/test_server.py`

Self-review:
- The roster now fails closed for unavailable blueprint libraries with an actionable page-level warning and does not weaken blueprint validation.
- Group sandbox ownership is restored without reintroducing Task 14’s broader model regression.
- Stale tests were migrated to the approved Task 14 contract rather than satisfied by restoring removed UI.
- Full-suite validation stayed green after the test migrations, which reduces the risk of cross-task regressions.

Remaining concerns:
- `agency/app.py` still contains older non-Task-14 routes like `/admin/orgs/{org}/initialize` and `/admin/orgs/{org}/autodetect`; they are outside the Task 14 ownership slice but remain part of the superseded surface.

## Review Fix 1

Status: completed

Scope delivered:
- Added `ConfigStore.inspect()` plus atomic `ConfigStore.replace(expected_revision, raw)` so setup no longer unlinks `config.yaml` before replacement and can compare exact on-disk bytes or expected absence under the config lock.
- Setup GET now emits an `expected_revision` token derived from the current config file state, including invalid bootstrap configs that cannot be parsed into a strict canonical snapshot.
- Setup POST now replaces bytes atomically against the posted expected revision, returns `409` on concurrent change, preserves the prior file exactly on validation or write failure, and refreshes app services only after successful replacement.
- Added single-transaction `create_group_state(...)` / `GroupCreateStatePatch` so `/admin/orgs/create` writes the full group payload in one `ConfigStore.patch(...)` call with no intermediate bare group state.

Evidence:
- RED before implementation:
  - `.venv\Scripts\python.exe -m pytest tests/test_config_store.py tests/test_config_patches.py tests/test_group_settings_canonical.py tests/test_admin_org_sandbox.py -q`
  - Result: `7 failed, 30 passed`
  - Failures proved the exact review findings:
    - `ConfigStore` had no `replace(...)`
    - setup form omitted `expected_revision`
    - setup write failure deleted `config.yaml` after `unlink()`
    - admin group create did not use one patch transaction
- GREEN focused atomicity slice:
  - `.venv\Scripts\python.exe -m pytest tests/test_config_store.py tests/test_config_patches.py tests/test_group_settings_canonical.py tests/test_admin_org_sandbox.py -q`
  - Result: `37 passed`
- GREEN Task 14 + config store/patch slice:
  - `.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_agent_roster.py tests/test_server.py tests/test_admin_org_sandbox.py tests/test_admin_agent_create.py tests/test_config_store.py tests/test_config_patches.py -q`
  - Result: `62 passed`
- Full suite:
  - `.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: `1015 passed, 3 skipped, 1 failed`
  - The single failure was `tests/test_job_detached_process.py::test_detached_worker_survives_submitter_exit` with a transient Windows `PermissionError` while re-reading a live job file.
- Isolation rerun of the only full-suite failure:
  - `.venv\Scripts\python.exe -m pytest tests/test_job_detached_process.py -k detached_worker_survives_submitter_exit -v`
  - Result: `1 passed`
- Controller full-suite rerun on the same commit:
  - `.venv\Scripts\python.exe -m pytest tests\ -q`
  - Result: `1016 passed, 3 skipped in 53.26s`

Changed files:
- `agency/configuration/store.py`
- `agency/configuration/patches.py`
- `agency/configuration/__init__.py`
- `agency/web/routes/admin_groups.py`
- `agency/templates/setup.html`
- `tests/test_config_store.py`
- `tests/test_config_patches.py`
- `tests/test_group_settings_canonical.py`
- `tests/test_admin_org_sandbox.py`
- `tests/test_server.py`

Residual concern:
- Full-suite verification hit one non-deterministic Windows file-locking failure in `tests/test_job_detached_process.py`, but the isolated rerun passed unchanged, so no deterministic regression is currently reproduced in the Task 14 fix surface.

## Subtask D - superseded Route Ownership Cleanup

Status: completed

Scope delivered:
- Removed the superseded Task 14 app-owned POST handlers for `/admin/orgs/{org}/initialize` and `/admin/orgs/{org}/autodetect` from `agency/app.py`.
- Removed the router-level custom 404 registrations for `/admin/orgs/{org}/dispatch`, `/admin/orgs/{org}/initialize`, and `/admin/orgs/{org}/autodetect` from `agency/web/routes/admin_groups.py` so those mutation paths are unregistered rather than shimmied.
- Left the approved routine execution route `/{group}/agents/{agent}/run` intact.
- Added structural tests in `tests/test_agent_roster.py` that assert the removed Task 14 POST paths are absent from `app.routes`, return ordinary `404` on POST, and do not mutate config bytes.
- Added a route-ownership assertion that the Task 14 canonical routes are unique in the live route table while preserving the runtime dispatch, admin group, and agent execution surfaces.

Evidence:
- Focused validation green:
  - `tests/test_agent_roster.py`: 14 passed
  - `tests/test_admin_dispatch.py`: 12 passed
  - `tests/test_admin_org_sandbox.py`: 11 passed
  - Total: 37 passed
- Full suite green:
  - `.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: 1009 passed, 3 skipped

Changed files:
- `agency/app.py`
- `agency/web/routes/admin_groups.py`
- `tests/test_agent_roster.py`

Remaining concern:
- The approved routine execution route remains in `agency/app.py` instead of a dedicated web route module, but it now uses request-scoped strict canonical config ownership instead of filesystem agent resolution.

## Subtask E - Manual Run Ownership Seam

Status: completed

Scope delivered:
- Refactored only `POST /{group}/agents/{agent}/run` in `agency/app.py` to depend on request-scoped `AgencyServices` and a strict `ConfigStore` snapshot for group, instance, and routine lookup.
- Removed this route's dependency on `get_group(...)`, `resolve_agent_dir(...)`, and singleton config authority while preserving `submit_job_request(...)` as the authoritative request resolver.
- Preserved routine argument rendering, manual memory override validation, `400`/`404` behavior, and the `202` response contract.
- Updated manual-run regression coverage so a config-only instance with no physical directory can launch a routine, and asserted the route does not create an instance directory.

Evidence:
- Focused seam regression:
  - `.venv\Scripts\python.exe -m pytest tests/test_agent_run.py -k "run_returns_202_and_schedules or run_unknown_routine_404 or run_invalid_routine_id_400 or run_accepts_valid_selector_override_for_routine or run_rejects_invalid_selector_override_for_routine" -v`
  - Result: `5 passed, 14 deselected`
- Task 14 focused suite:
  - `.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_agent_roster.py tests/test_server.py tests/test_admin_org_sandbox.py tests/test_admin_agent_create.py tests/test_admin_dispatch.py tests/test_agent_run.py tests/test_config_canonical.py -q`
  - Result: `174 passed`
- Task 12 trigger suite:
  - `.venv\Scripts\python.exe -m pytest tests/test_dispatch_run.py tests/test_agent_run.py tests/test_execute_decision.py tests/test_decision_verify.py -v`
  - Result: `60 passed`
- Full suite:
  - `.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: `1009 passed, 3 skipped`

Notes:
- Unknown group and unknown routine still return `404`, invalid routine ids and invalid selector overrides still return `400`, and no route-local filesystem scaffolding occurs for config-only instances.

## Review Fix 2

Status: completed

Scope delivered:
- Fixed the malformed setup form markup so `group_key` is a complete standalone input and `expected_revision` is a sibling hidden input inside the same form.
- Added HTML-parser-backed regression coverage proving the setup form contains distinct `group_key` and `expected_revision` inputs in the same form, and that the posted expected revision still blocks stale submits with a `409`.
- Fixed `/admin/orgs/new` and `/admin/orgs/create` so the create form renders the registered integration list, preserves the selected `default_integration` on validation errors, and persists the submitted registered integration instead of hardcoding `claude-code`.
- Added validation for blank/unknown `default_integration` values that returns a `409` with an actionable error and prevents config mutation.

Evidence:
- Focused regression run:
  - `.\.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_admin_org_sandbox.py -v`
  - Result: `22 passed`
- Adjacent Task 14/config/admin validation:
  - `.\.venv\Scripts\python.exe -m pytest tests/test_group_settings_canonical.py tests/test_admin_org_sandbox.py tests/test_server.py tests/test_config_store.py tests/test_config_patches.py -v`
  - Result: `49 passed`
- Full suite:
  - `.\.venv\Scripts\python.exe -m pytest tests/ -q`
  - Result: `1019 passed, 3 skipped in 50.24s`

Changed files:
- `agency/app.py`
- `agency/templates/admin_org_edit.html`
- `agency/templates/setup.html`
- `agency/web/routes/admin_groups.py`
- `tests/test_admin_org_sandbox.py`
- `tests/test_group_settings_canonical.py`

Notes:
- The create form smoke test intentionally uses `/admin/orgs/new`, which is the actual GET route for the `admin_org_edit.html` create form.
- No new parser dependency was introduced; the tests use the stdlib `html.parser` to avoid brittle substring assertions.
