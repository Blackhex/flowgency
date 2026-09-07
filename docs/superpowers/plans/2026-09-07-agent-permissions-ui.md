# Agent Permissions UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one structured agent Permissions tab, backed by sibling team/agent permission configuration, without changing the existing policy semantics or enforcement.

**Architecture:** Relocate canonical configuration first, then introduce a read-only tool catalog, a pure draft adapter, and a server-side effective-policy presenter. Use the existing ConfigStore lock/revision transaction for persistence and a small dedicated route module for the editor. Jinja renders the saved view; browser JavaScript edits structured drafts and renders only the newest server preview.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, Jinja2, PyYAML, existing filesystem locks/atomic writes, plain JavaScript, pytest, Playwright, and existing themes. No new Python dependencies; vendor the pinned Lucide browser bundle used by the approved mockup for the new icon controls.

## Global Constraints

- Follow `docs/superpowers/specs/2026-09-06-agent-permissions-ui-design.md`; its latest team-and-agent relocation amendment is authoritative.
- "Both team and agent configuration use `permissions` alongside `runtime`; `permissions` retains `mode` and `rules`, and each rule retains optional `path` and `tools`."
- "There is no All/None/Selected selector and no separate All tools toggle. All available choices are tool checkboxes."
- "No separate inherited-rules section. All inherited information belongs in the effective summary, with source links."
- "**Path** and **Tools** use matching normal-weight field-label typography."
- "No-op fidelity takes precedence over normalization."
- "Do not rewrite the user's live configuration or add startup conversion as part of this feature."
- "Agent Permissions saves remain isolated to the selected agent; they do not edit team defaults."
- "A YAML editor or advanced schema-oriented mode" remains out of scope for the agent UI. Existing Team Settings controls are not redesigned.
- Copy is **Permissions**, **Mode**, **Rules**, **Effective access**, **Add rule**, **Path rule**, **No-path rule**, **Workspace access**, **No-path tools**, **Path**, **Tools**, **Discard changes**, **Save permissions**. No visible Tools label inside a no-path rule; no Target selector inside either rule type.
- Keep configuration defaults: team timeout `1800`, team mode `unrestricted`, no team rules; omitted agent values inherit. Preserve exact-path workspace write eligibility, same-path unions, and longest-matching-path resolution.
- `schema_version: 1` remains the accepted version for this planned change; explicitly reject the relocated old nested keys instead of accepting dual authority. No implicit field migration or live config edit is authorized.
- Run all implementation commands from `C:/Projekty/Flowgency/.worktrees/agent-permissions-ui` on `feat/agent-permissions-ui`, never from the main checkout. Preserve unrelated edits and runtime files.
- Baseline at `6559632`: `python -m pytest tests/ -q` yielded **2092 passed, 6 skipped**. Re-establish it at execution start if the branch or environment changed. Baseline results do not verify the implementation.
- Finish each task's red/green test cycle and review before dependent tasks. Use Conventional Commits, maximum 72-character subjects. This plan is committed separately from all implementation.

## Normative Assets

- Source: `docs/superpowers/specs/assets/2026-09-06-agent-permissions-ui/permissions-short-labels-v7.html`.
- Desktop: `docs/superpowers/specs/assets/2026-09-06-agent-permissions-ui/permissions-short-labels-v7.png`.
- Narrow: `docs/superpowers/specs/assets/2026-09-06-agent-permissions-ui/permissions-short-labels-v7-mobile.png`.
- Compare the application region, excluding the brainstorming banner. Use existing app navigation and themes. Earlier v1-v6 assets are historical and must not override v7. Mockup sample tools and disabled controls are not production behavior.

## File Ownership

| Deliverable | Files and responsibility |
| --- | --- |
| Canonical permission relocation | `flowgency/configuration/models.py`, `effective.py`, `paths.py`, `patches.py`; keep validators/resolution/store contracts, move model fields and raw writers |
| Existing consumers | `flowgency/web/routes/admin_teams.py`, `agent_detail.py`, `flowgency/integrations/__init__.py`; read the new model locations and use correct diagnostic paths |
| Tool metadata | New `flowgency/integrations/tool_catalog.py`; `BaseIntegration` exposes read-only catalog metadata, separate from runtime capabilities |
| Structured drafts | New `flowgency/permissions/forms.py`; validated transport types, server-derived row identities, no-op-safe serialization |
| Effective presentation | New `flowgency/permissions/presentation.py`; call existing resolver and annotate its result without implementing a second policy engine |
| Atomic agent save | New `flowgency/permissions/editor.py`; shared preview/save preparation and ConfigStore patch callback |
| HTTP and page ownership | New `flowgency/web/routes/agent_permissions.py`; existing agent detail tabs/context and router registration get narrow additions |
| Frontend | New `flowgency/templates/agent_detail_permissions.html`, `agent_permissions_summary.html`, `flowgency/static/agent-permissions.js`, `agent-permissions.css`; existing Profile/Runtime templates lose permission controls |
| Tests | New `tests/test_permission_relocation.py`, `test_tool_catalog.py`, `test_permission_forms.py`, `test_permission_presentation.py`, `test_permission_editor.py`, `test_agent_permissions.py`, `tests/ui/agent_permissions.spec.ts`; update existing fixtures/expectations rather than introducing runtime compatibility shims |

## Task Order

1. Relocate team and agent configuration, all active producers/consumers, and documentation.
2. Introduce truthful read-only tool metadata with a completeness gate.
3. Build the structured draft adapter and lossless configuration mapping.
4. Add effective presentation and revision-safe preview/save service.
5. Add Permissions routes/template and remove competing agent permission surfaces.
6. Complete browser interactions, keyboard behavior, and responsive/theme styling.
7. Run acceptance tests, visual comparison, whole-branch review, and authorized integration.

Tasks 1 and 2 are independently reviewable, but use the listed sequence to avoid overlapping edits in integration and fixture files. Do not spawn implementation agents during plan writing.

---

### Task 1: Relocate Canonical Team and Agent Permissions

**Files:**
- Modify: `flowgency/configuration/models.py` (`AgentRuntime`, `TeamRuntime`, `AgentInstance`, `TeamConfig`, `_validate_team_runtime`, `_validate_agent_runtime`, `_prepare_for_model`, `_resolve_permission_paths`).
- Modify: `flowgency/configuration/effective.py`, `flowgency/configuration/paths.py`, `flowgency/configuration/patches.py`.
- Modify: `flowgency/web/routes/admin_teams.py`, `flowgency/web/routes/agent_detail.py`, `flowgency/integrations/__init__.py`.
- Modify: `tests/conftest.py`, `tests/test_agent_detail.py`, `tests/test_config_normalization.py`, `tests/test_config_patches.py`, `tests/test_effective_policy.py`, `tests/test_executor_eligibility.py`, `tests/ui/server.py`, and active fixture configuration documents that contain nested permission blocks.
- Modify: `config.yaml.example`, `AGENTS.md`, `kb/configuration.md`, `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`, active setup templates/reference examples and their existing fixture assertions. Never modify `config.yaml`, historical specs, historical plans, screenshots, or runtime-state directories as part of the structural update. Editing setup/agent instruction documents must follow the applicable customization skill; only the configuration examples and descriptions needed for this relocation change.
- Create/Test: `tests/test_permission_relocation.py`.

**Interfaces:**
- Consumes: `parse_config(raw: dict[str, Any], config_path: Path)`, `resolve_effective_policy(config, team_id, agent_id, *, timeout_override=None, integration=None)`, and existing ConfigStore patch functions.
- Produces: `TeamConfig.permissions: RuntimePermissions` and `AgentInstance.permissions: RuntimePermissions`; both runtime models retain `timeout` only among defined fields. Retain the existing `RuntimePermissions` Python name to avoid an unrelated public rename.
- Produces: old nested-key error `code="relocated-permissions"`, `field=f"{scope}.runtime.permissions"`, with guidance to move to `f"{scope}.permissions"`.
- Preserves: resolver signatures, rule semantics, accepted schema version, timeout defaults, and existing Team Settings HTTP form fields.

- [x] **Step 1: Pin the structural regression before changing fixtures.** Create the test file with this test; `raw_config` is the existing conftest fixture. Add the four parameterized cases for team-only old key, agent-only old key, team both locations, and agent both locations using the same fixture.

```python
from copy import deepcopy
import pytest
from flowgency.configuration.models import parse_config
from flowgency.configuration.issues import ValidationFailed
from flowgency.configuration.effective import resolve_effective_policy

def test_sibling_policy_keeps_union_and_timeout(raw_config, tmp_path):
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["runtime"] = {"timeout": 1800}
    team["permissions"] = {
        "mode": "unrestricted",
        "rules": [{"path": ".", "tools": ["read", "search"]}],
    }
    team["agents"] = [{
        "name": "advisor", "blueprint": "advisor", "integration": "copilot",
        "runtime": {"timeout": 2400},
        "permissions": {"rules": [{"path": ".", "tools": ["write"]}]},
    }]
    config = parse_config(raw, tmp_path / "config.yaml").resolved
    policy = resolve_effective_policy(config, "newsletter", "advisor")
    assert policy.timeout == 2400
    assert policy.mode == "unrestricted"
    assert set(policy.rules[0].tools) == {"read", "search", "write"}
    assert policy.rules[0].path == config.teams["newsletter"].workspace_path
    assert not hasattr(config.teams["newsletter"].runtime, "permissions")

@pytest.mark.parametrize("level", ["team", "agent"])
@pytest.mark.parametrize("also_sibling", [False, True])
def test_nested_policy_never_silently_ignored(raw_config, tmp_path, level, also_sibling):
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["runtime"] = {"timeout": 1800}
    team.pop("permissions", None)
    team["agents"] = [{"name": "advisor", "blueprint": "advisor", "integration": "copilot"}]
    owner = team if level == "team" else team["agents"][0]
    owner.setdefault("runtime", {})["permissions"] = {"mode": "restricted"}
    if also_sibling:
        owner["permissions"] = {"mode": "unrestricted"}
    with pytest.raises(ValidationFailed) as caught:
        parse_config(raw, tmp_path / "config.yaml")
    assert any(issue.code == "relocated-permissions" for issue in caught.value.issues)
```

- [x] **Step 2: Run the new tests and observe assertion failures.** Run `python -m pytest tests/test_permission_relocation.py -q`. The current parser ignores sibling permissions and accepts the old nested location, so the new assertions must fail for those reasons, not a missing import or broken test fixture.

- [x] **Step 3: Relocate the model and reject the old keys.** Remove the `permissions` member from both runtime classes and add it to both owner classes with the same default factory. Add the guard to both runtime validation functions; do not remove the existing superseded-key guards. Change their old corrective hint from `runtime.permissions` to `permissions`.

```python
def _reject_nested_permissions(runtime: Any, scope: str) -> list[ValidationIssue]:
    if not _is_mapping(runtime) or "permissions" not in runtime:
        return []
    return [_build_issue(
        code="relocated-permissions",
        scope=scope,
        field=f"{scope}.runtime.permissions",
        message="Permissions belong alongside runtime, not inside it.",
        hint=f"Move {scope}.runtime.permissions to {scope}.permissions; keep timeout in runtime.",
    )]
```

`_resolve_permission_paths` should receive the owner dictionary, not its runtime dictionary. Rename its local `runtime_entry` parameter to `owner_entry`, keeping the same logic for `owner_entry["permissions"]`. In `_prepare_for_model`, pass `resolved_team` and `agent_entry`; do not manufacture an explicit `mode` where it was omitted. Update resolver reads to `team.permissions` and `agent.permissions`, including `"mode" in agent.permissions.model_fields_set` for inheritance.

- [x] **Step 4: Move raw writers in the same task.** `create_team_state` creates sibling blocks. `patch_team_settings_state` writes `team.setdefault("permissions", {})`; preserve `runtime["timeout"]`. Until Task 5 removes the old agent Runtime rule form, its existing save handler and `patch_agent_runtime` must write sibling agent permissions, so this intermediate commit is functional rather than emitting rejected configuration.

```python
"runtime": {"timeout": patch.runtime_timeout},
"permissions": {
    "mode": patch.permission_mode,
    "rules": list(deepcopy(patch.permission_rules)),
},
```

Update `admin_teams._team_settings_response` to `permissions = team_cfg.permissions`; update `_runtime_context` and `_apply_runtime_patch` in agent detail. Update permission path and integration validation diagnostics to `permissions.mode` / `permissions.rules`. Do not edit CLI enforcement switches or integration sandbox algorithms.

- [x] **Step 5: Update active configuration fixtures and producers structurally.** Search tracked active files from the worktree with `rg -n 'runtime\.permissions|"permissions"|permissions:' flowgency tests examples kb config.yaml.example README.md AGENTS.md .github/skills`. For each owner dictionary/YAML block containing nested permissions, move that block up one level; do not perform a blind string replacement of all `runtime` references. Keep deliberately old-shape rejection tests old. Update expected diagnostics and model attribute reads. Inspect `flowgency/instances.py` and instance/cache digest tests: model dumps may relocate automatically, but effective permission content must remain represented in compilation identity.

The allowable fixture-only transformation is:

```python
for team in raw["teams"].values():
    for owner in [team, *team.get("agents", [])]:
        runtime = owner.get("runtime", {})
        if "permissions" in runtime:
            assert "permissions" not in owner
            owner["permissions"] = runtime.pop("permissions")
```

Use this as an editing recipe, not a runtime loader, autouse test fixture, or committed compatibility helper. Check diffs so every active example uses the new shape and negative tests still prove rejection. Update active documentation to explain manual relocation at both levels and no fallback; preserve historic design documents.

- [x] **Step 6: Prove the task and its compatibility boundary.** Run `python -m pytest tests/test_permission_relocation.py tests/test_config_normalization.py tests/test_config_patches.py tests/test_effective_policy.py tests/test_executor_eligibility.py tests/test_agent_detail.py -q`, then `python -m pytest tests/ -q`. Require green results including existing team settings, setup, clone/move, cache, job, and CLI tests. Assert fixtures retain timeout and unrelated owner data; runtime readers must not create directories at rule target paths.

- [x] **Step 7: Review and commit the coherent relocation.** Inspect `git diff --check` and `git diff --stat`; stage only the files changed for this task using explicit path lists from that diff. Commit `refactor(config)!: lift team and agent permissions` with body/footer explaining old nested fields are rejected and `BREAKING CHANGE: move runtime.permissions to permissions on teams and agents`. Complete a task review before starting the catalog work.

### Task 2: Add a Truthful Read-Only Tool Catalog

**Files:**
- Create: `flowgency/integrations/tool_catalog.py`, `tests/test_tool_catalog.py`.
- Modify: `flowgency/integrations/__init__.py`, `flowgency/integrations/flowgency/copilot.py`, `kb/contributing-integrations.md`.

**Interfaces:**
- Consumes: `BaseIntegration.name` and existing declared/detected runtime capabilities only for separate enforcement information, never as the exhaustive vocabulary.
- Produces: `ToolDescriptor`, `ToolCatalog`, `catalog_id(catalog: ToolCatalog) -> str`, `available_names(catalog: ToolCatalog, target: RuleTarget) -> tuple[str, ...]`, and `get_tool_catalog(integration: BaseIntegration) -> ToolCatalog` in `tool_catalog.py`.
- Produces: `BaseIntegration.permission_tool_catalog() -> ToolCatalog`; default is incomplete/unknown, not complete/empty.

- [x] **Step 1: Write metadata tests with a closed fake catalog.** Define the public immutable types below and use them in tests before implementation. `targets=()` means applicability is unknown, so offer the name on either rule type without claiming path enforcement. `version` identifies the vocabulary evidence; completeness is global to the integration, not completeness of a filtered subset.

```python
from dataclasses import dataclass
from typing import Literal

RuleTarget = Literal["path", "no_path"]

@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    targets: tuple[RuleTarget, ...] = ()

@dataclass(frozen=True)
class ToolCatalog:
    integration: str
    version: str
    tools: tuple[ToolDescriptor, ...] = ()
    complete: bool = False
    warning: str | None = None
```

```python
from dataclasses import replace
from flowgency.integrations import BaseIntegration
from flowgency.integrations.tool_catalog import ToolCatalog, ToolDescriptor, catalog_id, get_tool_catalog

def test_default_catalog_does_not_claim_completeness():
    catalog = get_tool_catalog(BaseIntegration())
    assert catalog.complete is False
    assert catalog.tools == ()

def test_catalog_identity_includes_completeness_and_vocabulary():
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"),), True)
    assert catalog_id(catalog) != catalog_id(replace(catalog, complete=False))
    assert catalog_id(catalog) != catalog_id(replace(catalog, tools=()))

def test_detection_failure_is_not_empty_success(monkeypatch):
    integration = BaseIntegration()
    def fail():
        raise OSError("probe unavailable")
    monkeypatch.setattr(integration, "permission_tool_catalog", fail)
    catalog = get_tool_catalog(integration)
    assert not catalog.complete
    assert catalog.warning
```

- [x] **Step 2: Run `python -m pytest tests/test_tool_catalog.py -q` and confirm missing catalog API failures.** Keep production capability detection unmodified.

- [x] **Step 3: Implement the small catalog contract.** Hash a canonical JSON object containing integration, version, ordered `(name, targets)` pairs, and completeness; exclude the human warning string. Validate descriptor identifiers are nonempty strings, names unique, and target values from the literal set; bad metadata follows the incomplete failure path. `get_tool_catalog` catches discovery failures and returns `ToolCatalog(integration.name, "unavailable", warning="Tool availability could not be determined.")` without exposing raw exception text. Use `TYPE_CHECKING` for the BaseIntegration import to avoid a circular import.

```python
import hashlib
import json

def catalog_id(catalog: ToolCatalog) -> str:
    payload = {
        "integration": catalog.integration,
        "version": catalog.version,
        "tools": [(tool.name, tool.targets) for tool in catalog.tools],
        "complete": catalog.complete,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

def available_names(catalog: ToolCatalog, target: RuleTarget) -> tuple[str, ...]:
    return tuple(tool.name for tool in catalog.tools if not tool.targets or target in tool.targets)
```

The default method returns `ToolCatalog(self.name, "unknown", warning="Tool list is incomplete.")`. For Copilot, the repository documents permission identifiers `read`, `search`, `write`; expose these as known names, with unknown applicability and `complete=False`. Do not claim the list is exhaustive, do not advertise shell availability from a literal in a test, and do not infer tool availability from `path_scopable_tools`. Start with this explicit partial declaration:

```python
def permission_tool_catalog(self) -> ToolCatalog:
    return ToolCatalog(
        integration=self.name,
        version="flowgency-permission-names-v1",
        tools=tuple(ToolDescriptor(name) for name in ("read", "search", "write")),
        complete=False,
        warning="Additional integration tools may exist.",
    )
```

Other integrations retain the honest unknown fallback and configured/custom names remain editable. Do not invent a complete native CLI catalog without evidence, silently map actual CLI names onto new permission categories, or install/run external agents to discover tools.

- [x] **Step 4: Test the completeness acceptance boundary.** Add tests for invalid duplicate metadata, unknown target applicability, identity changes, filtered target lists, and `complete=True` on an empty fake catalog (must never map to all tools). Record in `kb/contributing-integrations.md` that a provider may mark complete only for a demonstrably closed vocabulary including extension tools; version changes require a new identity. Known catalogs may be partial, so creating/restoring an unbounded grant remains unavailable for those providers, exactly as the spec's safety fallback requires. Make this limitation explicit in completion evidence; do not claim complete YAML expressiveness for an incomplete provider. Request a design revision rather than adding an All tools toggle if full unbounded editing is later required without a complete provider.

- [x] **Step 5: Run `python -m pytest tests/test_tool_catalog.py tests/test_capability_detection.py tests/test_copilot_capability_detection.py -q`, review, and commit.** Commit `feat(permissions): expose read-only tool catalogs`. Runtime capability detection and enforcement must produce the same results as before.

### Task 3: Map Structured Drafts Without Losing Grants

**Files:**
- Create: `flowgency/permissions/forms.py`, `tests/test_permission_forms.py`.

**Interfaces:**
- Consumes: Task 2 `ToolCatalog`, `catalog_id`, `available_names` and original raw agent configuration loaded server-side at the requested revision.
- Produces: `RuleDraft`, `PermissionDraft`, `PermissionForm`, `PermissionFormError`; `build_form(agent_raw: dict[str, Any], catalog: ToolCatalog) -> PermissionForm`; `serialize_permissions(agent_raw: dict[str, Any], draft: PermissionDraft, catalog: ToolCatalog) -> dict[str, Any] | None`.
- `None` from serialization means the entire original permissions block was absent and still is; an empty dictionary is a present block and must not be treated as absence.

- [x] **Step 1: Define the wire contract and failing round-trip tests.** Use Pydantic with strict validation and `extra="forbid"`, not YAML or unvalidated dictionaries from the browser. `source_index` is an optional index into server-loaded raw rules; new rules have `None`. It is not a permission grant or a trusted original rule payload. `mode="inherit"` is a UI value, never written to config.

```python
from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from flowgency.configuration.issues import ValidationFailed
from flowgency.integrations.tool_catalog import RuleTarget

class RuleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_index: int | None = None
    target: RuleTarget
    path: str | None = None
    selected: list[str]

class PermissionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["inherit", "restricted", "unrestricted"]
    rules: list[RuleDraft]

@dataclass(frozen=True)
class PermissionForm:
    draft: PermissionDraft
    choices: tuple[tuple[str, ...], ...]
    unbounded: tuple[bool, ...]

class PermissionFormError(ValidationFailed):
    pass
```

```python
from copy import deepcopy
import pytest
from flowgency.integrations.tool_catalog import ToolCatalog, ToolDescriptor
from flowgency.permissions.forms import build_form, serialize_permissions

@pytest.mark.parametrize("rule", [
    {"path": "."}, {"path": ".", "tools": None},
    {"path": ".", "tools": ["read", "write"]},
    {"path": ".", "tools": []}, {"tools": ["custom_tool"]},
    {"path": None, "tools": []},
])
def test_unchanged_rule_is_lossless(rule):
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"), ToolDescriptor("write")), True)
    agent = {"permissions": {"rules": [rule]}, "runtime": {"timeout": 2400}}
    original = deepcopy(agent)
    draft = build_form(agent, catalog).draft
    assert serialize_permissions(agent, draft, catalog) == agent["permissions"]
    assert agent == original

def test_path_only_edit_never_unbounds_explicit_tools():
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"),), True)
    agent = {"permissions": {"rules": [{"path": ".", "tools": ["read"]}]}}
    draft = build_form(agent, catalog).draft
    draft.rules[0].path = "docs"
    assert serialize_permissions(agent, draft, catalog)["rules"] == [{"path": "docs", "tools": ["read"]}]
```

- [x] **Step 2: Run `python -m pytest tests/test_permission_forms.py -q` to establish the red case.** Add tests for absent permissions, absent rules, explicit empty rules, duplicate path entries, and null path/tool fields in the original raw configuration.

- [x] **Step 3: Implement deterministic form initialization.** Choices for each rule are ordered catalog names applicable to that target followed by original configured custom names in their original order; do not suppress configured names excluded by metadata. For an unbounded rule check every displayed choice, but keep the unbounded flag for the summary and preserve the raw source server-side. For finite rules check only their names. A no-path original with `path: null` initializes as no-path but round-trips its original key until the target is explicitly changed. Build form values from raw rules, not resolved Pydantic path values.

```python
def selected_for_rule(rule: dict[str, Any], choices: tuple[str, ...]) -> list[str]:
    tools = rule.get("tools")
    return list(choices) if tools is None else list(dict.fromkeys(tools))
```

Define `selected_for_rule` inside `forms.py` and test it via `build_form`. Preserve original duplicate tool entries/order for unchanged selections by returning the original raw tools representation, not by writing the deduplicated display list.

- [x] **Step 4: Implement serialization with shape-preserving comparison first.** Validate each original index is nonnegative, in bounds, unique, and in increasing order for surviving original rows; append new rows without reordering the original ones. Validate no-path drafts have `path is None`; path drafts have a nonblank path but preserve its authored nonblank text. Validate selected values are unique, nonempty strings. Do not constrain them to a hard-coded vocabulary. Build error fields such as `rules.2.path` and `rules.2.selected` with `ValidationIssue`, raising `PermissionFormError`.

For each surviving original row compare selected-name sets against `build_form(...).draft.rules[source_index].selected`. If unchanged, copy the original `tools` key exactly (including absence/null/duplicates/order); a path edit affects only `path`. If the entire form returns to baseline, return a deepcopy of the original block or `None`, so absent mode/rules stay absent. Only then apply new/changed selection mapping:

```python
def encode_selection(selected: list[str], catalog: ToolCatalog) -> dict[str, Any]:
    if not selected:
        return {"tools": []}
    whole_catalog = {tool.name for tool in catalog.tools}
    if catalog.complete and whole_catalog and set(selected) == whole_catalog:
        return {}
    return {"tools": list(selected)}
```

Define `encode_selection` in `forms.py`; use the whole catalog rather than the target-filtered choices because omitted `tools` denotes every integration tool. Omit `tools` only when the selected names exactly equal a complete nonempty whole catalog. If the selection also contains configured or newly added custom names, keep the full explicit list so the save does not silently widen the rule to future tools or discard authored names. A changed no-path rule omits `path`; unchanged null path keeps its null shape. Mode inherit removes only `mode`; explicit overrides set it. Never touch `runtime`, identity, or team data.

- [x] **Step 5: Add selection and invalid-input matrix tests.** Use this direct test plus parameterized cases for exact-all/subset/empty; complete-catalog-plus-custom; empty complete catalog; incomplete catalog; preserving an unbounded original under incomplete metadata; clearing such an original to an explicit empty list when it had visible choices; custom names; removing one of repeated rules; duplicate/out-of-range indices; blank path; no-path with path; and semantic no-op after toggling back.

```python
from flowgency.permissions.forms import PermissionDraft, RuleDraft

@pytest.mark.parametrize("complete,expected", [(True, {}), (False, {"tools": ["read", "write"]})])
def test_new_full_selection_respects_catalog_completeness(complete, expected):
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"), ToolDescriptor("write")), complete)
    draft = PermissionDraft(mode="inherit", rules=[RuleDraft(target="no_path", selected=["read", "write"])])
    assert serialize_permissions({}, draft, catalog) == {"rules": [expected]}
```

An incomplete empty catalog cannot distinguish a no-op unbounded source from clearing zero displayed choices: preserve the original and show the metadata limitation, rather than erasing its grant. Removing the rule and adding an empty rule remains explicit and supported. This test must prove the limitation is safe rather than inventing a hidden schema control.

- [x] **Step 6: Run the forms tests, review for raw mutation/grant widening, and commit.** Run `python -m pytest tests/test_permission_forms.py tests/test_tool_catalog.py -q`. Commit `feat(permissions): map structured rule drafts`.

### Task 4: Build Effective Presentation and Atomic Editor Service

**Files:**
- Create: `flowgency/permissions/presentation.py`, `flowgency/permissions/editor.py`, `tests/test_permission_presentation.py`, `tests/test_permission_editor.py`.
- Reuse without broad refactoring: `flowgency/configuration/store.py`, `flowgency/configuration/effective.py`, `flowgency/permissions/eligibility.py`.

**Interfaces:**
- Consumes: Tasks 1-3 models, form adapter/catalog; existing `ConfigSnapshot`, `ConfigStore.patch`, `parse_config`, `resolve_effective_policy`, `grants_write_on`.
- Produces in `presentation.py`: `Source = Literal["team", "agent"]`, `EffectiveGrant(name: str, sources: tuple[Source, ...])`, `EffectiveScope(path: str | None, grants: tuple[EffectiveGrant, ...], all_tools_sources: tuple[Source, ...])`, `PermissionSummary(mode: str, mode_source: Source, scopes: tuple[EffectiveScope, ...], workspace_write: bool, team_settings_href: str)`, all frozen dataclasses; `present_permissions(config: FlowgencyConfig, team_id: str, agent_id: str) -> PermissionSummary`.
- Produces in `editor.py`: `EditorRequest`, `CatalogConflictError`, `PreparedPermissions(candidate: dict[str, Any], form: PermissionForm, summary: PermissionSummary | None, catalog: ToolCatalog, issues: tuple[ValidationIssue, ...] = ())`, `load_editor(snapshot, team_id, agent_id) -> PreparedPermissions`, `prepare_permissions(snapshot, team_id, agent_id, request: EditorRequest, catalog: ToolCatalog) -> PreparedPermissions`, `save_permissions(store, team_id, agent_id, request: EditorRequest) -> ConfigSnapshot`.
- `EditorRequest` contains `revision: str`, `catalog_id: str`, `draft_version: int` (nonnegative), and `draft: PermissionDraft`; strict Pydantic validation, extra fields forbidden. `CatalogConflictError` is a `ValueError` subclass with a stable reload-catalog message.

Declare the transport type in `editor.py` as follows; all service signatures take `snapshot: ConfigSnapshot`, `team_id: str`, and `agent_id: str` where those names appear above.

```python
from pydantic import BaseModel, ConfigDict, Field
from flowgency.permissions.forms import PermissionDraft

class EditorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(min_length=1)
    catalog_id: str = Field(min_length=1)
    draft_version: int = Field(ge=0)
    draft: PermissionDraft

class CatalogConflictError(ValueError):
    pass
```

- [x] **Step 1: Add read-only summary tests using new sibling fixtures.** Construct a valid parsed config with team workspace read/search and agent workspace write plus a nested read/search-only rule. Override only the test integration's declared/runtime capabilities via monkeypatch to allow test policies; do not loosen production validation. Assert exact grants and source attribution, pathless separation, unbounded source attribution, mode inheritance versus override, and unchanged config bytes.

```python
def test_summary_sources_follow_same_path_union(raw_config, tmp_path, monkeypatch):
    from copy import deepcopy
    from flowgency.configuration.models import parse_config
    from flowgency.integrations import get_integration
    from flowgency.integrations.models import RuntimeCapabilities
    from flowgency.permissions.presentation import present_permissions
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["permissions"] = {"mode": "unrestricted", "rules": [{"path": ".", "tools": ["read"]}]}
    team["agents"] = [{"name": "advisor", "blueprint": "advisor", "integration": "copilot", "permissions": {"rules": [{"path": ".", "tools": ["write"]}]}}]
    integration = get_integration("copilot")
    monkeypatch.setattr(type(integration), "runtime_capabilities", property(lambda self: RuntimeCapabilities(permission_modes=frozenset({"restricted", "unrestricted"}), path_scopable_tools=frozenset({"write"}))))
    config = parse_config(raw, tmp_path / "config.yaml").resolved
    summary = present_permissions(config, "newsletter", "advisor")
    assert summary.workspace_write
    assert summary.mode_source == "team"
    assert summary.team_settings_href == "/admin/teams/newsletter/edit"
    assert [(grant.name, grant.sources) for grant in summary.scopes[0].grants] == [("read", ("team",)), ("write", ("agent",))]
```

- [x] **Step 2: Run `python -m pytest tests/test_permission_presentation.py -q` and implement the presenter.** Call `resolve_effective_policy` once, then walk its resolved rules. For provenance compare exact canonical paths to the owner's resolved rules, using `os.path.normcase(str(path.resolve(strict=False)))` on Windows and exact strings otherwise; use `None` as the pathless key. Do not attribute a parent path's tools to a more-specific child rule. For each finite effective tool collect sources that declare that tool or have tools omitted/null at this exact path; for an unbounded result store only sources that supplied an unbounded rule in `all_tools_sources`. The summary cannot be successfully built when the resolver rejects a policy.

```python
policy = resolve_effective_policy(config, team_id, agent_id)
team = config.teams[team_id]
agent = team.agents[agent_id]
mode_source = "agent" if "mode" in agent.permissions.model_fields_set else "team"
workspace_write = grants_write_on(policy.rules, team.workspace_path)
```

Construct `PermissionSummary` with those values, canonical forward-slash display paths, and the actual team settings URL. The fallback row is derived from `policy.mode`; no extra configured path or duplicate mode beside the selector. Unbounded scopes render as unbounded, not a fabricated list of all known/future tools.

- [x] **Step 3: Write persistence and preview tests around ConfigStore.** Use `ConfigStore.create(raw_config)` on the relocated conftest fixture, choose its existing builder agent, and monkeypatch `BaseIntegration.permission_tool_catalog` to a deterministic incomplete test catalog. Test no-op and changed mode/rules, saving only the target agent, conflict, catalog drift, unsupported mode, outside-lock byte changes, and validation exceptions leaving the file byte-identical. Test preview with directory initialization functions patched to raise so any write attempt fails the test.

```python
def test_preview_does_not_write(raw_config, config_paths, monkeypatch):
    from flowgency.configuration import ConfigStore
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import ToolCatalog, ToolDescriptor, catalog_id
    from flowgency.permissions.editor import EditorRequest, load_editor, prepare_permissions
    catalog = ToolCatalog("claude-code", "fixture-v1", (ToolDescriptor("read"),), False)
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store = ConfigStore(config_paths["config_path"])
    snapshot = store.create(raw_config)
    before = store.path.read_bytes()
    loaded = load_editor(snapshot, "newsletter", "builder")
    request = EditorRequest(revision=snapshot.revision, catalog_id=catalog_id(catalog), draft_version=1, draft=loaded.form.draft)
    prepared = prepare_permissions(snapshot, "newsletter", "builder", request, catalog)
    assert prepared.summary.mode == "unrestricted"
    assert store.path.read_bytes() == before
```

- [x] **Step 4: Implement shared preparation and locked save.** `load_editor` obtains the instance integration, calls `get_tool_catalog`, builds the form from its raw entry, and presents the saved policy. If current integration capabilities reject the saved policy, return that editable form with `summary=None` and the caught `ValidationFailed.issues`, not a broken GET. `prepare_permissions` checks the request revision and catalog hash, creates a deepcopy of snapshot raw, finds the agent by `name`, serializes permissions, removes/sets only that block, parses the candidate with `snapshot.path`, runs the presenter, and returns a `PreparedPermissions`. Invalid preparation raises instead of returning a success with no summary. It never calls ConfigStore create/replace, directory initialization, or job submission.

For Save, use `store.patch(request.revision, patcher)` so baseline comparison and validation execute under the existing lock. The callback constructs a ConfigSnapshot from the raw dictionary already loaded by the store (do not call `store.load` while holding its lock), discovers the current catalog, and invokes the same preparation. Preserve all unchanged top-level and agent values:

```python
def save_permissions(store: ConfigStore, team_id: str, agent_id: str, request: EditorRequest) -> ConfigSnapshot:
    def patcher(raw: dict[str, Any]) -> None:
        current = parse_config(raw, store.path).resolved
        snapshot = ConfigSnapshot(store.path, request.revision, raw, current)
        instance = current.teams[team_id].agents[agent_id]
        catalog = get_tool_catalog(get_integration(instance.integration))
        prepared = prepare_permissions(snapshot, team_id, agent_id, request, catalog)
        original_agent = next(entry for entry in raw["teams"][team_id]["agents"] if entry["name"] == agent_id)
        candidate_agent = next(entry for entry in prepared.candidate["teams"][team_id]["agents"] if entry["name"] == agent_id)
        if "permissions" in candidate_agent:
            original_agent["permissions"] = deepcopy(candidate_agent["permissions"])
        else:
            original_agent.pop("permissions", None)
    return store.patch(request.revision, patcher)
```

Import the already defined `ConfigSnapshot`, `ConfigStore`, `EditorRequest`, `get_tool_catalog`, `get_integration`, `parse_config`, `deepcopy`, and `Any` in `editor.py`. Return field-addressable errors through existing `ValidationFailed` / new form exceptions. The current revision plus server-loaded raw rule indices is the authoritative baseline; no client-submitted original rule payload, session cache, or token database is needed.

- [x] **Step 5: Prove concurrent writers cannot lose changes.** Reuse `tests._lock_helpers.hold_exclusive_lock` and neighboring ConfigStore concurrency tests. Submit two edits with one revision; exactly one succeeds and the second conflicts. Team permission edits after a page load conflict rather than being overwritten by the stale agent draft. Verify all unrelated runtime/prompts/routines/identity blocks match their pre-save values.

- [x] **Step 6: Run `python -m pytest tests/test_permission_presentation.py tests/test_permission_editor.py tests/test_config_store.py tests/test_executor_eligibility.py -q`, review, and commit.** Commit `feat(permissions): preview and save agent policies`. Do not start route work until pure mapping, policy attribution, and locked save tests pass.

### Task 5: Add the Permissions Page and HTTP Contracts

**Files:**
- Create: `flowgency/web/routes/agent_permissions.py`, `flowgency/templates/agent_detail_permissions.html`, `flowgency/templates/agent_permissions_summary.html`, `tests/test_agent_permissions.py`.
- Modify: `flowgency/web/routes/agent_detail.py`, `flowgency/web/routes/__init__.py`, `flowgency/app.py`, `flowgency/configuration/patches.py`, `flowgency/templates/agent_detail_profile.html`, `flowgency/templates/agent_detail_runtime.html`, `tests/test_agent_detail.py`.

**Interfaces:**
- Consumes: `EditorRequest`, `load_editor`, `prepare_permissions`, `save_permissions`, `PermissionFormError`, `CatalogConflictError`, Task 4 presenter types, existing `get_services` and `_detail_context`.
- Produces: GET `/{team}/agents/{agent}/permissions`, POST at the same path, POST `/{team}/agents/{agent}/permissions/preview`.
- Page initialization: JSON script `#permissions-initial` with `baseline` and `draft` as `EditorRequest` JSON, `choices` and `unbounded` from `PermissionForm`, serialized `catalog` and `catalog_id`, `preview_url`, `save_url`, `conflict: bool`, and `issues` list. `baseline` is not accepted as input authority; it is for local Discard/dirty comparison only.
- Preview success JSON: `{draft_version, revision, catalog_id, summary_html, workspace_write, issues: []}`. Failure JSON: `{draft_version, code, issues, summary_html: null}`; no stale summary on failure.
- Error codes/status: malformed transport 422, form/policy validation 422, config revision conflict 409 (`config-conflict`), catalog conflict 409 (`catalog-conflict`), unavailable transient preview 503 (`preview-unavailable`), unknown team/agent 404.
- Save request: normal POST form field `payload`, containing one serialized `EditorRequest`; success is 303 to the canonical Permissions URL. Typed but invalid user fields return the same page with draft retained and the corresponding error status. Invalid transport JSON returns 422 without executing a patch.

- [x] **Step 1: Add GET/preview/save ownership tests.** Reuse `_seed_app` from `tests/test_agent_detail.py` after Task 1's fixture relocation and `_revision`. Seed/test catalog independently using monkeypatch when an exact list is needed; never make production catalog completeness true for tests. Add this first regression:

```python
from tests.test_agent_detail import _seed_app

def test_permissions_owns_the_agent_editor(monkeypatch, tmp_path, raw_config):
        client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
        response = client.get("/newsletter/agents/advisor/permissions")
        assert response.status_code == 200
        assert 'id="permissions-initial"' in response.text
        assert '>Mode<' in response.text
        assert '>Rules<' in response.text
        assert 'permission_rules_yaml' not in response.text
        profile = client.get("/newsletter/agents/advisor/profile").text
        runtime = client.get("/newsletter/agents/advisor/runtime").text
        assert 'name="can_write"' not in profile
        assert 'permission_rules_yaml' not in runtime
        assert 'name="timeout"' in runtime
```

Add a fixture-local helper `initial_payload(html: str) -> dict` in this test file to locate `<script id="permissions-initial" type="application/json">` with `re.search`, then use `json.loads` (test extraction only). For POST tests copy its `draft`, edit a rule, and send `data={"payload": json.dumps(payload)}`. Test conflict by saving another config revision first, assert HTTP 409 and draft value still present in the initialization script, while file bytes remain at the new revision.

- [x] **Step 2: Run `python -m pytest tests/test_agent_permissions.py -q` and confirm the new route is missing.** Keep other routes working while adding registration.

- [x] **Step 3: Wire the route module and use one page snapshot.** Export/register `agent_permissions_router` in `flowgency/web/routes/__init__.py` and `flowgency/app.py`. Add `"permissions": "Permissions"` immediately after runtime in `_TAB_LABELS`. Extend `_detail_context` with keyword `snapshot: ConfigSnapshot | None = None`, loading only when not supplied; new routes pass the same snapshot used for form/preview so the header revision cannot drift. This is a narrow parameter addition, not a general context rewrite.

Define `render_permissions_page(request, services, snapshot, team: str, agent: str, *, submitted: EditorRequest | None = None, issues: tuple[ValidationIssue, ...] = (), conflict: bool = False, status_code: int = 200)` in the new route module. Build the saved form with `load_editor`; on errors use `submitted.draft` as the editable draft and the saved form as baseline. On conflicts keep the submitted old revision in the draft and disable resubmission until reload; never relabel it with a current revision. Merge custom names from the submitted draft into display choices without changing the authoritative catalog. Include any load-time policy errors when `summary is None`.

```python
@router.get("/{team}/agents/{agent}/permissions", response_class=HTMLResponse)
async def permissions_page(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
        snapshot = services.config_store.load()
        _get_snapshot_instance(snapshot, team, agent)
        return render_permissions_page(request, services, snapshot, team, agent)
```

Define `issue_dicts(issues: tuple[ValidationIssue, ...]) -> list[dict[str, str]]` using dataclass fields and map `corrective_hint` to `hint` for the existing banner convention. For Pydantic errors map `loc` to dot-separated fields and use a non-reflective message; do not return unescaped input snippets.

- [x] **Step 4: Implement preview and save using the service, not raw config mutation.** Preview parses `EditorRequest.model_validate(await request.json())`, loads current snapshot, verifies agent existence, loads the catalog through the pinned integration, and calls `prepare_permissions`. Render `agent_permissions_summary.html` through `request.app.state.templates.env.get_template(...).render(summary=prepared.summary, is_draft=True)` with the template environment's autoescaping. Return the exact success/error contracts above. Use a catch for expected configuration/form/catalog errors; unexpected errors remain server errors logged without returning internal exception text.

```python
@router.post("/{team}/agents/{agent}/permissions", response_class=HTMLResponse)
async def permissions_save(request: Request, team: str, agent: str, services: FlowgencyServices = Depends(get_services)):
        form = await request.form()
        submitted = EditorRequest.model_validate_json(str(form.get("payload", "")))
        save_permissions(services.config_store, team, agent, submitted)
        request.app.state.refresh_services()
        return RedirectResponse(f"/{team}/agents/{agent}/permissions", status_code=303)
```

Wrap the shown happy path: malformed JSON/Pydantic input -> 422; `ConfigConflictError` -> retained-draft page 409 with conflict flag; `CatalogConflictError` -> retained-draft page 409 requiring refreshed catalog; `ValidationFailed` -> retained-draft page 422. Unknown agent is 404, not a 500 from a missing raw index. No save happens until the service transaction passes.

- [x] **Step 5: Render the approved controls and escaped initial data.** Use a page-scoped root `#permission-editor`, a form `#permissions-form` with hidden `payload`, `#mode`, `[data-rule-list]`, and `#permission-summary`. Embed initial data with Jinja `tojson`, never Python repr or `|safe` on tool/path strings. A rule uses `[data-rule-row]`, `data-source-index`, `[data-rule-path]` when path-bearing, `[data-tool-name]` checkboxes, `[data-custom-tool]`, and `[data-add-custom-tool]`. Label/remove IDs must be unique even for repeated paths. Include named `<template>` blocks for path and no-path rows; they are markup sources for Task 6, not visible duplicate editors.

```html
<label for="mode">Mode</label>
<select id="mode" name="mode">
    <option value="inherit">Inherit from team</option>
    <option value="restricted">Restricted</option>
    <option value="unrestricted">Unrestricted</option>
</select>
<h3>Rules</h3>
<script id="permissions-initial" type="application/json">{{ permissions_initial | tojson }}</script>
```

Summary template: each path/no-path `EffectiveScope` gets a heading, then finite grants with their exact sources; an unbounded scope gets **All tools, including future tools** and its unbounded grant sources. The fallback row is **Paths without a matching rule**, **No access** or **All tools**, and mode with Team link/Agent source. Use team URLs already built by the presenter. Keep the configured/enforced distinction. The workspace-root status sits in the approved page heading and is updated from server `workspace_write`, not inferred from checkbox state. With `summary=None`, show **Preview unavailable** and the validation issues; do not show a success badge.

- [x] **Step 6: Remove competing writers and update tests.** Remove Profile `can_write` field and associated misleading copy. Runtime retains timeout inheritance and integration display but no permission rules/summary. Remove `rules` from `AgentRuntimePatch` and its update branches once its call sites and tests use the dedicated permission service. The old Runtime POST rejects the presence of `permission_rules_yaml` with HTTP 409 and a link/message directing the user to Permissions; retain submitted timeout text but make no write in that request. Timeout-only POST and Profile POST continue preserving sibling permissions.

Update `tests/test_agent_detail.py` so former runtime policy tests move to the new permission tests instead of being deleted without equivalent coverage. Update tab expectations to actual ordering including the new tab, preserving all preexisting tabs. `_runtime_context` becomes timeout/integration-only; it must not call policy resolution just to render a timeout form.

- [x] **Step 7: Run `python -m pytest tests/test_agent_permissions.py tests/test_agent_detail.py tests/test_admin_org_sandbox.py tests/test_config_patches.py -q`, review, and commit.** Include XSS values in custom names, paths, and validation messages, byte-preserving failed saves, preview no-write tests, unsupported policy GET/editability, and missing revision/catalog tests. Commit `feat(permissions): add dedicated agent settings tab`.

### Task 6: Complete the Structured Browser Editor

**Files:**
- Create: `flowgency/static/agent-permissions.js`, `flowgency/static/agent-permissions.css`, `flowgency/static/lucide.min.js`, `tests/ui/agent_permissions.spec.ts`.
- Modify: `flowgency/templates/agent_detail_permissions.html`, `package.json`, generated `package-lock.json`, `tests/ui/agent_configuration.spec.ts`, `tests/ui/accessibility.spec.ts`, `tests/ui/fixtures/config.yaml`.
- Reuse: `tests/ui/layout.ts`, `tests/ui/keyboard.ts`, `playwright.config.ts`, existing base-template theme behavior.

**Interfaces:**
- Consumes: page DOM/data and preview/save transport from Task 5; tool metadata applies only to choices and status, never to JS policy evaluation.
- Produces: in-browser `collectDraft()`, `renderDraft(draft)`, `schedulePreview()`, `requestPreview(version)`, `applyIssues(issues)`, `setSummaryPending()`, `setSummaryUnavailable()`, `setDirtyState()`, all file-local functions in one initialization closure. No global policy engine or app-wide state manager.
- Mutation contract: edits stay local until Save; newest draft version owns the summary; discarded/navigated drafts invalidate pending requests.

- [x] **Step 1: Write the first browser test.** Add the new Permissions page to the existing WCAG list and all-tab keyboard test. Preserve and update Runtime screenshot assertions so they still test timeout/integration and absence of permission editing.

```typescript
import { expect, test } from '@playwright/test';
import { assertNoLayoutIssues, assertNoConsoleErrors, installConsoleErrorGate } from './layout';

test.beforeEach(async ({ page }, testInfo) => {
    installConsoleErrorGate(page);
    await page.addInitScript(theme => localStorage.setItem('theme', theme), testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('structured rules discard without persisting', async ({ page }) => {
    await page.goto('/newsletter/agents/advisor/permissions');
    const rows = page.locator('[data-rule-row]');
    const originalCount = await rows.count();
    await page.getByRole('button', { name: 'Add rule', exact: true }).click();
    await page.getByRole('menuitem', { name: 'No-path rule', exact: true }).click();
    await expect(rows).toHaveCount(originalCount + 1);
    const added = rows.last();
    await expect(added.locator('[data-rule-path]')).toHaveCount(0);
    await expect(added.getByText('Tools', { exact: true })).toHaveCount(0);
    await added.locator('[data-custom-tool]').fill('custom_test_tool');
    await added.getByRole('button', { name: 'Add custom tool', exact: true }).click();
    await expect(added.getByRole('checkbox', { name: 'custom_test_tool', exact: true })).toBeChecked();
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    await expect(rows).toHaveCount(originalCount);
    await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
});
```

- [x] **Step 2: Prepare existing UI tooling and run the red test.** From the active worktree run `python -m venv .venv`, `.venv/Scripts/python.exe -m pip install -e ".[test]"`, `npm install`, and `npm exec playwright install chromium` if this worktree lacks those dependencies. Set `$env:PLAYWRIGHT_SKIP_BROWSER_GC = '1'` in PowerShell before browser installation so installing a matching browser does not garbage-collect other checkouts' browser caches. Do not use a live config or the user's running dashboard. Run `npm run test:ui -- tests/ui/agent_permissions.spec.ts --project=desktop-dark`; the new rule interaction must fail before implementation.

- [x] **Step 3: Add the pinned icon asset and page-scoped styling.** Run `npm install --save-dev --save-exact lucide@0.468.0`; copy `node_modules/lucide/dist/umd/lucide.min.js` into `flowgency/static/lucide.min.js` preserving its upstream notice. Load the local bundle only on the Permissions page. This is the library/version used in the mockup; do not add a second icon library or manually draw its glyphs. Keep the dependency lock as generated metadata. Add `plus`, `trash-2`, `chevron-down`, `folder`, and `wrench` icon bindings, accessible button names and hover titles.

Scope CSS below `#permission-editor`; use existing light/dark CSS conventions. Preserve the v7 hierarchy and spacing, with these layout rules:

```css
#permission-editor .permission-columns {
    display: grid;
    grid-template-columns: minmax(0, 1.55fr) minmax(270px, 1fr);
    gap: 28px;
}
#permission-editor .permission-columns > * { min-width: 0; }
#permission-editor .permission-rule { border-radius: 6px; padding: 16px; }
#permission-editor .permission-label { font-size: 14px; font-weight: 400; }
#permission-editor .permission-tools { display: flex; flex-wrap: wrap; gap: 16px; }
#permission-editor .permission-tools label { overflow-wrap: anywhere; max-width: 100%; }
#permission-editor .permission-icon { width: 34px; height: 34px; flex-shrink: 0; }
#permission-editor input[type="text"] { width: 100%; min-width: 0; }
@media (max-width: 1100px) {
    #permission-editor .permission-columns { grid-template-columns: minmax(0, 1fr); }
}
```

Set border/background/text colors from current app light/dark conventions, not the mockup's dark-only global CSS. Keep 14px Path/Tools labels consistent; no negative letter spacing or viewport-scaled fonts. Summary border moves from left to top in stacked mode. Do not add decorative sections/cards or inherited-rules panels.

- [x] **Step 4: Implement local rule editing and safe DOM updates.** Parse `#permissions-initial` once, keep immutable baseline copies using `structuredClone`, and maintain stable local row IDs for labels/errors independent of `source_index`. Instantiate row templates, fill values via DOM properties, and add custom tool text with `textContent`/`createTextNode`. `collectDraft` reads Mode, surviving original indices, target, raw path, and checked names. It emits Task 3's wire shape only. Do not serialize omitted/all flags or calculate final permission rules in JS.

```javascript
function collectDraft() {
    return {
        mode: document.getElementById('mode').value,
        rules: [...root.querySelectorAll('[data-rule-row]')].map(row => ({
            source_index: row.dataset.sourceIndex === '' ? null : Number(row.dataset.sourceIndex),
            target: row.dataset.target,
            path: row.dataset.target === 'path' ? row.querySelector('[data-rule-path]').value : null,
            selected: [...row.querySelectorAll('[data-tool-name]:checked')].map(input => input.dataset.toolName),
        })),
    };
}
```

Define `root` as `document.getElementById('permission-editor')` inside the initialization closure. For new rows `source_index=null`, initial selections empty. Show full provider choices for the target plus configured/custom choices; do not mark absent selections inherited or disable editing because the team also grants a tool. A custom name already present selects its checkbox, not duplicates it. Add a no-path custom entry control even though the static sample shows only one configured tool. On remove, restore focus to the neighboring row's remove control or Add rule. Discard rebuilds the baseline, clears custom draft-only choices/errors, resets Mode, invalidates requests, and restores the saved summary. In a revision conflict, Discard explicitly reloads the current page rather than falsely treating old data as current.

- [x] **Step 5: Implement draft-versioned preview.** On each input/change/add/remove increment `draftVersion`, mark summary pending immediately, disable Save, abort the old request, and debounce 250ms. Never retain a success badge during invalid/pending preview. Only render HTML supplied by this same-origin server's autoescaped summary template; tool/path/user strings must not be interpolated into local `innerHTML`.

```javascript
let draftVersion = initial.draft.draft_version;
let controller = null;
let previewTimer = null;
let validVersion = -1;
function schedulePreview() {
    draftVersion += 1;
    validVersion = -1;
    controller?.abort();
    clearTimeout(previewTimer);
    setSummaryPending();
    setDirtyState();
    const version = draftVersion;
    previewTimer = setTimeout(() => requestPreview(version), 250);
}
async function requestPreview(version) {
    controller = new AbortController();
    try {
        const response = await fetch(initial.preview_url, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            body: JSON.stringify({ revision: initial.draft.revision, catalog_id: initial.draft.catalog_id, draft_version: version, draft: collectDraft() }),
        });
        const payload = await response.json();
        if (version !== draftVersion || payload.draft_version !== version) return;
        if (!response.ok) { applyIssues(payload.issues); setSummaryUnavailable(); return; }
        document.getElementById('permission-summary').innerHTML = payload.summary_html;
        root.querySelector('[data-workspace-write]').textContent = payload.workspace_write ? 'Workspace-root write: granted' : 'Workspace-root write: not granted';
        validVersion = version;
        applyIssues([]);
        setDirtyState();
    } catch (error) {
        if (error.name === 'AbortError' || version !== draftVersion) return;
        setSummaryUnavailable();
    }
}
```

Implement the referenced local helpers: `setSummaryPending` sets `aria-busy=true`, hides prior content/status and shows **Updating preview**; `setSummaryUnavailable` clears busy, removes success status, shows **Preview unavailable** and a **Retry preview** command without losing fields; `applyIssues` attaches safe field text to unique row inputs with `aria-invalid`/`aria-describedby` and shows nonfield errors in an alert; `setDirtyState` compares collected draft to the original normalized display draft and disables Save unless dirty, not conflicted/submitting, and `validVersion === draftVersion`. Initial saved valid state permits display, not a dirty Save. A failed request must remain visibly failed, not spin forever. For `catalog-conflict` retain the draft and request explicit reload/review of current metadata; never silently adopt a new catalog hash and submit the same all-selected inference.

- [x] **Step 6: Complete Save, navigation protection, and keyboard behavior.** Submit a native POST only after the latest preview is valid: populate hidden `payload` from the current revision/catalog/version and `collectDraft`, set a submitting flag, disable duplicate submission, and allow the existing form POST/303 redirect. This does not bypass server-side revalidation. On error HTML, initialize with the submitted draft and noncurrent summary instead of treating it as saved.

Bind `input` for text/path edits as well as `change` for Mode/checkboxes; do not wait for blur to invalidate a preview. Store `validatedDraft` as a serialized copy of the collected draft whenever a preview succeeds. Verify it again during submit to cover programmatic/autofill edits that did not dispatch an input event. Introduce `let validatedDraft = null` beside `validVersion`; set it to the exact serialized request draft after a current successful response, and clear it when invalidating that version.

```javascript
form.addEventListener('submit', event => {
    if (validVersion !== draftVersion || conflict || submitting) { event.preventDefault(); return; }
    if (JSON.stringify(collectDraft()) !== validatedDraft) { event.preventDefault(); schedulePreview(); return; }
    form.elements.payload.value = JSON.stringify({ revision: initial.draft.revision, catalog_id: initial.draft.catalog_id, draft_version: draftVersion, draft: collectDraft() });
    submitting = true;
    setDirtyState();
});
window.addEventListener('beforeunload', event => {
    if (!dirty || submitting) return;
    event.preventDefault();
    event.returnValue = '';
});
```

Define `form`, `conflict`, `submitting`, and `dirty` in the initialization closure; update `dirty` from `setDirtyState`. Use a real button with `aria-expanded`, `aria-controls`, and menu items for Add rule. Opening focuses first item; arrow keys move, Escape closes and returns focus, choosing a type appends its rule and focuses Path/custom-tool as appropriate. Enter in custom-tool entry adds its name instead of submitting the entire form. Team links open a separate tab with `rel="noopener"`. No keyboard shortcut help copy is added to the page.

- [x] **Step 7: Add behavioral browser regressions with controlled preview responses.** Delay the first preview response using Playwright `page.route` and a Promise resolved by the test, not sleeps; send a second draft and release the first afterwards. Assert that the displayed preview corresponds only to the second draft. Return 422/409/503 in separate tests and assert retained inputs, unavailable summary, disabled Save, and working retry/reload flows. Use real preview/save endpoints for round-trip tests in addition to mocks.

```typescript
test('preview failure retains a custom tool draft', async ({ page }) => {
    await page.goto('/newsletter/agents/advisor/permissions');
    await page.route('**/permissions/preview', async route => {
        const request = route.request().postDataJSON();
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ draft_version: request.draft_version, code: 'preview-unavailable', issues: [], summary_html: null }) });
    });
    const row = page.locator('[data-rule-row]').first();
    await row.locator('[data-custom-tool]').fill('keep_this_tool');
    await row.getByRole('button', { name: 'Add custom tool', exact: true }).click();
    await expect(page.getByText('Preview unavailable', { exact: true })).toBeVisible();
    await expect(row.getByRole('checkbox', { name: 'keep_this_tool', exact: true })).toBeChecked();
    await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
});
```

Make browser-save tests self-cleaning: at test start read `#permissions-initial` with `JSON.parse(await locator.textContent())`, retain the original draft, and use `try/finally`. In cleanup GET a fresh Permissions page, use its current revision/catalog ID, mark every restored row `source_index=null` if rule deletion/reordering made the original indices invalid, and POST through the real endpoint. For exact omitted/null restoration tests, use the Python service fixtures instead, because recreating an unbounded rule requires a complete catalog. Restrict shared real-browser mutations to Mode and finite tool rules already representable with that provider, restoring original raw shape through a dedicated disposable fixture agent where needed. Prefer adding one `permissions-editor` fixture agent under the unrestricted research team with explicit finite rules and no routines; update roster counts deliberately rather than using advisor's unbounded fixtures for destructive tests. Do not make test order affect saved policy. Do not normalize away legitimate overflow with `pinToSingleLine` in new Permissions tests; long paths/custom names must fit through the implemented CSS.

- [x] **Step 8: Run the new file in all four projects, then accessibility and existing agent configuration tests.** Run `npm run test:ui -- tests/ui/agent_permissions.spec.ts`, then `npm run test:ui -- tests/ui/accessibility.spec.ts tests/ui/agent_configuration.spec.ts`. Update only expected snapshots affected by new navigation and removed Runtime fields; inspect every changed image. Run `python -m pytest tests/test_agent_permissions.py -q` after frontend edits. Review and commit `feat(permissions): build structured rule editor`.

### Task 7: Verify the Complete Workflow and Integrate

**Files:**
- Modify: new tests and active documentation owned by Tasks 1-6 only if acceptance exposes a specific missing case.
- Create: `docs/superpowers/verification/2026-09-07-agent-permissions-ui.md` for commands, results, screenshot comparison, catalog evidence/limitations, and whole-branch review outcome.
- Do not modify: live `config.yaml`, lock files, team state/logs/jobs, or approved design assets to disguise visual drift.

**Interfaces:**
- Consumes: all completed task contracts and the v7 HTML/PNG visual contract.
- Produces: verified branch ready for the repository's preauthorized fast-forward workflow; no new application abstraction.

- [x] **Step 1: Exercise real saved configuration and failure paths.** Run the focused Python suite and all browser tests. Use real GET -> edit -> preview -> POST -> 303 -> GET paths for subset, empty, complete fake catalog, inherited mode, explicit override, relative path, no-path custom tools, duplicate scopes, remove, discard, unsupported mode, and revision conflicts. Fake complete catalog tests prove serialization logic; they do not constitute evidence that real Copilot's catalog is complete. Keep the explicit incomplete-catalog limitation in the verification record.

```text
python -m pytest tests/test_permission_relocation.py tests/test_tool_catalog.py tests/test_permission_forms.py tests/test_permission_presentation.py tests/test_permission_editor.py tests/test_agent_permissions.py -q
python -m pytest tests/ -q
npm run test:ui
git diff --check
```

- [x] **Step 2: Compare actual UI with the approved assets.** Run the existing deterministic fixture server via Playwright; capture desktop `1440x1000` and mobile `390x844` in light and dark projects. Check the application region against v7 for column ratio/order, labels, normal Path/Tools weight, no Target selectors, no separate inherited panel, Mode source in summary, tool provenance links, workspace write status, and action placement. Inspect real saved image files; integrated browser screenshots can be cropped under zoom. Verify PNG dimensions cover the document content (native scrollbars may reduce content width). Capture nonempty/empty/error/long-path states; inspect keyboard menu/focus and asset loading. Document intentional sample-data differences, not layout excuses.

- [x] **Step 3: Audit config authority and active documentation.** Search active sources/tests/docs with `rg -n 'runtime\.permissions' flowgency tests kb README.md AGENTS.md config.yaml.example .github/skills examples`. Every remaining occurrence must be an old nested-location rejection guard/test, an explicit manual relocation example, or a Python field unrelated to canonical owner configuration. Check setup output, instance clones/moves, config patches, and generated projections use new model fields. Do not rewrite immutable historical job specs merely because canonical config fields moved. The full job/runtime regression suite establishes unchanged execution behavior.

- [x] **Step 4: Record and review evidence.** Write the verification file with exact passing/failing command counts, catalog completeness evidence, screenshots, and any residual integration-dependent enforcement limitations. No unsupported claim of universal all-tools editing. Perform per-spec coverage review plus a whole-branch bug/security review, including grant widening, concurrent writes, reflected markup, and lost unrelated data. Fix relevant findings through failing regression tests and rerun the affected checks. Commit tests and docs by their respective Conventional Commit types rather than combining an unrelated cleanup.

- [ ] **Step 5: Integrate only after implementation review and green tests.** Use the repository's preauthorized workflow; do not ask for merge-vs-PR choices. Check main checkout status and its tip. If master advanced, rebase this feature onto master, rerun the full suite and meaningful UI gates in the worktree, and review any conflict resolutions. Preserve main-checkout user edits with a named stash only if needed, restoring them afterwards; never include them in feature commits.

```text
git -C C:/Projekty/Flowgency status --short --branch
git -C C:/Projekty/Flowgency log -1 --oneline
git -C C:/Projekty/Flowgency merge --ff-only feat/agent-permissions-ui
```

Run `python -m pytest tests/ -q` from fast-forwarded master, not the feature worktree. Push both branches with `git push origin master feat/agent-permissions-ui`. Remove the linked worktree only once master contains the reviewed feature and the suite is green: `git worktree remove .worktrees/agent-permissions-ui`, then `git worktree prune`. Keep the feature branch. Stop and disclose if tests/push fail rather than destroying the worktree or claiming integration succeeded.

## Spec Coverage and Plan Self-Review

| Specification requirement | Task and check |
| --- | --- |
| Team/agent sibling policy, old nested/both-location rejection | Task 1 relocation tests and full regression suite |
| Team Settings persistence unchanged visually | Tasks 1 and 7, existing team/config UI tests |
| No schema migration/live config mutation | Task 1 guards; Task 7 authority audit |
| Catalog truth, targets, incompleteness/version | Task 2 metadata tests; Task 3 mapping; Task 4 catalog conflict |
| Absent/null/empty/explicit/unbounded fidelity | Task 3 round-trip and changed-selection matrix |
| Relative paths/custom names/repeated/pathless rows | Tasks 3, 4, 5, 6 |
| Mode inheritance, same-path union, nested scopes, provenance | Task 4 presenter and existing effective/eligibility tests |
| Locked revision save and draft retention | Tasks 4 and 5, concurrent and stale-revision tests |
| No stale preview or invented enforcement | Tasks 4 and 6, failure/out-of-order browser tests |
| Profile/Runtime single ownership and timeout preservation | Tasks 1 and 5, old writer rejection tests |
| Approved copy/layout/typography/links | Tasks 5-7, normative v7 comparison |
| Keyboard, error, empty, dirty, save/discard, light/dark/mobile | Task 6 four-project Playwright + Axe tests |
| Config/setup/example docs and preserved execution behavior | Tasks 1 and 7, full suite and active-source audit |
| Whole-branch review, fast-forward, publish, cleanup | Task 7 integration gate |

- [ ] Before execution, recheck spec amendments and baseline changes; update this plan in a documentation-only commit if its contracts changed.
- [ ] During implementation, mark steps complete only after their command/test evidence exists; do not mark the plan complete at creation.
- [ ] At execution handoff, choose subagent-driven or inline execution explicitly. No implementation has been performed by writing this plan.