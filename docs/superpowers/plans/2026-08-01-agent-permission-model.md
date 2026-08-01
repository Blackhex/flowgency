# Agent Permission Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `runtime.sandbox`, `runtime.tools` and `capabilities` with one `runtime.permissions` section of path/tool rules, make compilation a per-instance projection, and render the launch view as access zones.

**Architecture:** A permission is a tool acting on a path. `runtime.permissions` holds a `mode` and a list of rules, each `{path?, tools?}`. Group and instance carry the identical block; instance rules are additive and longest match governs. Agency contributes generated rules for the launch view zones. The compilation cache is keyed on blueprint × integration × the instance properties that affect rendering. `schema_version` becomes 5; version 4 is rejected and migrated by a CLI command.

**Tech Stack:** Python 3.13, Pydantic v2 config models, FastAPI/Jinja2, pytest. No new third-party dependencies.

## Global Constraints

- Run tests from the worktree root: `python -m pytest tests/ -q`. There is **no** `.venv`; the interpreter is `python` (3.13.14). The suite takes about five minutes — run the focused file while iterating and the whole suite once before committing.
- Baseline at the start of this plan: **1748 passed, 5 skipped, 0 failed**.
- Do **not** run `tests/test_runtime_projectors_live.py` on its own; it contains `real_runtime` tests that launch real CLIs and consume AI credits. The full-suite run covers them.
- `CONFIG_SCHEMA_VERSION` becomes `5`. A `schema_version: 4` document is rejected, never translated at load time.
- No integration may declare `path_scopable_tools` containing `write` in this plan. Behaviour is unchanged and narrowed policies still fail closed.
- Conventional Commits; subject ≤ 72 chars, imperative, lowercase, no trailing period; body wrapped at 72.
- PowerShell has **no heredoc**. Write multi-line commit messages to a temp file and use `git commit -F <file>`. Keep every terminal command on **one line**.
- Do not modify `config.yaml`, `config.yaml.lock`, group-state directories, or logs.
- All writes into Agency-owned storage use `atomic_write_text` / `atomic_write_bytes` from `agency/fs/atomic.py`.

## Shared interfaces

Every task depends on these. They are created in Task 1 and Task 2.

```python
# agency/configuration/models.py
PermissionMode = Literal["restricted", "unrestricted"]

class PermissionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path | None = None
    tools: tuple[str, ...] | None = None      # None means every tool

class RuntimePermissions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: PermissionMode = "unrestricted"
    rules: tuple[PermissionRule, ...] = ()
```

```python
# agency/integrations/models.py
@dataclass(frozen=True)
class ResolvedPermissionRule:
    path: Path | None
    tools: tuple[str, ...] | None             # None means every tool

@dataclass(frozen=True)
class EffectiveRuntimePolicy:
    timeout: int
    mode: PermissionMode
    rules: tuple[ResolvedPermissionRule, ...]

@dataclass(frozen=True)
class RuntimeCapabilities:
    permission_modes: frozenset[PermissionMode] = frozenset()
    path_scopable_tools: frozenset[str] = frozenset()
```

---

### Task 1: Permission models and schema version 5

**Files:**
- Modify: `agency/configuration/models.py`
- Test: `tests/test_permission_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PermissionMode`, `PermissionRule`, `RuntimePermissions`, `CONFIG_SCHEMA_VERSION = 5`; `GroupRuntime.permissions`, `AgentRuntime.permissions`.

Removed in this task: `GroupRuntimeSandbox`, `AgentRuntimeSandbox`, `RuntimeTools`, `AgentCapabilities`, `ToolMode`, `SandboxMode`, and the `sandbox` / `tools` / `capabilities` fields.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permission_models.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agency.configuration.models import (
    CONFIG_SCHEMA_VERSION,
    PermissionRule,
    RuntimePermissions,
)


def test_schema_version_is_five():
    assert CONFIG_SCHEMA_VERSION == 5


def test_omitted_tools_means_every_tool():
    assert PermissionRule(path=Path("/a")).tools is None


def test_empty_tools_is_distinct_from_omitted():
    assert PermissionRule(path=Path("/a"), tools=()).tools == ()


def test_rule_without_a_path_is_valid():
    assert PermissionRule(tools=("fetch",)).path is None


def test_unknown_rule_key_is_rejected():
    with pytest.raises(Exception):
        PermissionRule(path=Path("/a"), tool=["read"])


def test_permissions_default_to_unrestricted_with_no_rules():
    permissions = RuntimePermissions()

    assert permissions.mode == "unrestricted"
    assert permissions.rules == ()


def test_permissions_reject_an_unknown_mode():
    with pytest.raises(Exception):
        RuntimePermissions(mode="sandboxed")


def test_superseded_models_are_gone():
    import agency.configuration.models as models

    for name in (
        "GroupRuntimeSandbox",
        "AgentRuntimeSandbox",
        "RuntimeTools",
        "AgentCapabilities",
    ):
        assert not hasattr(models, name), f"{name} should have been removed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'PermissionRule'`

- [ ] **Step 3: Add the permission models**

In `agency/configuration/models.py`, replace the `ToolMode` and `SandboxMode` aliases with:

```python
PermissionMode = Literal["restricted", "unrestricted"]
```

and set:

```python
CONFIG_SCHEMA_VERSION = 5
```

Add, above `AgentRuntime`:

```python
class PermissionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path | None = None
    # None means every tool the integration offers; () means none.
    tools: tuple[str, ...] | None = None


class RuntimePermissions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: PermissionMode = "unrestricted"
    rules: tuple[PermissionRule, ...] = ()
```

- [ ] **Step 4: Replace the superseded fields**

Delete the `GroupRuntimeSandbox`, `AgentRuntimeSandbox`, `RuntimeTools` and `AgentCapabilities` classes.

`GroupRuntime` becomes:

```python
class GroupRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    permissions: RuntimePermissions = Field(default_factory=RuntimePermissions)
```

`AgentRuntime` becomes:

```python
class AgentRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    permissions: RuntimePermissions = Field(default_factory=RuntimePermissions)
```

Remove the `capabilities` field from `AgentInstance`.

- [ ] **Step 5: Update the schema-version gate**

Change `schema_version: Literal[4]` to `Literal[5]`, and the validation block near line 717 to:

```python
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        issues.append(
            ValidationIssue(
                code="unsupported-schema-version",
                scope="config",
                field="schema_version",
                message="schema_version must be 5.",
                hint=(
                    "Run `christag-agency config migrate` to convert a "
                    "schema_version 4 configuration."
                ),
            )
        )
```

- [ ] **Step 6: Run the focused tests**

Run: `python -m pytest tests/test_permission_models.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and report**

Run: `python -m pytest tests/ -q`
Expected: **many failures.** Every fixture builds a `schema_version: 4` config with `sandbox`/`tools`/`capabilities`. Do not fix them here — Tasks 2 to 8 change the code they exercise. Record the failure count in your report so later tasks can tell progress from regression.

- [ ] **Step 8: Commit**

```bash
git add agency/configuration/models.py tests/test_permission_models.py
git commit -m "feat(config): add permission rules and schema version 5"
```

---

### Task 2: Resolution, merging, and the effective policy

**Files:**
- Modify: `agency/integrations/models.py`
- Modify: `agency/configuration/effective.py`
- Create: `agency/permissions/__init__.py`
- Create: `agency/permissions/zones.py`
- Test: `tests/test_permission_resolution.py`

**Interfaces:**
- Consumes: `PermissionRule`, `RuntimePermissions` (Task 1).
- Produces:
  - `ResolvedPermissionRule`, and `EffectiveRuntimePolicy` reshaped to `(timeout, mode, rules)`
  - `EffectiveRuntimePolicy.tools_for(path) -> tuple[str, ...] | None`
  - `EffectiveRuntimePolicy.scoped_tools -> frozenset[str]`
  - `agency.permissions.zones.ZONE_INSTRUCTIONS`, `ZONE_OUTBOX`, `ZONE_MEMORY`
  - `agency.permissions.zones.launch_zone_rules(launch_dir) -> tuple[ResolvedPermissionRule, ...]`
  - `EffectiveRuntimePolicy.with_launch_zones(launch_dir) -> EffectiveRuntimePolicy`

Removed: `sandbox_mode`, `sandbox_roots`, `writable_roots`, `writes_narrowed`, `narrows_writes`, `ResolvedToolPolicy`, `PathPolicyMode`, `ToolPolicyMode`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permission_resolution.py`:

```python
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.store import ConfigStore
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule
from agency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(timeout=60, mode=mode, rules=tuple(rules))


def rule(path, tools):
    return ResolvedPermissionRule(path=None if path is None else Path(path), tools=tools)


def test_longest_match_governs():
    p = policy(rule("/ws", ("read",)), rule("/ws/tests", ("read", "write")))

    assert p.tools_for(Path("/ws/src/a.py")) == ("read",)
    assert p.tools_for(Path("/ws/tests/t.py")) == ("read", "write")


def test_empty_tools_is_a_carve_out():
    p = policy(rule("/ws", ("read", "write")), rule("/ws/.env", ()))

    assert p.tools_for(Path("/ws/.env")) == ()


def test_omitted_tools_means_every_tool():
    p = policy(rule("/ws", None))

    assert p.tools_for(Path("/ws/a")) is None


def test_uncovered_path_is_forbidden_when_restricted():
    p = policy(rule("/ws", ("read",)), mode="restricted")

    assert p.tools_for(Path("/elsewhere")) == ()


def test_uncovered_path_is_permitted_when_unrestricted():
    p = policy(rule("/ws", ("read",)), mode="unrestricted")

    assert p.tools_for(Path("/elsewhere")) is None


def test_scoped_tools_names_tools_whose_grant_differs():
    p = policy(rule("/ws", ("read",)), rule("/ws/tests", ("read", "write")))

    assert p.scoped_tools == frozenset({"write"})


def test_scoped_tools_is_empty_when_every_rule_agrees():
    p = policy(rule("/a", ("read",)), rule("/b", ("read",)))

    assert p.scoped_tools == frozenset()


def test_launch_zones_are_appended(tmp_path: Path):
    zoned = policy(rule("/ws", ("read",))).with_launch_zones(tmp_path)

    assert zoned.tools_for(tmp_path / ZONE_INSTRUCTIONS / "AGENTS.md") == ("read",)
    assert zoned.tools_for(tmp_path / ZONE_OUTBOX / "observations") == ("read", "write")
    assert zoned.tools_for(tmp_path / ZONE_MEMORY / "memory.md") == ("read", "write")


def test_launch_zones_cannot_be_widened_by_configuration(tmp_path: Path):
    authored = policy(rule(str(tmp_path / ZONE_INSTRUCTIONS), ("read", "write")))

    zoned = authored.with_launch_zones(tmp_path)

    assert zoned.tools_for(tmp_path / ZONE_INSTRUCTIONS / "AGENTS.md") == ("read",)


def _config(tmp_path: Path, raw_config, *, group_rules, agent_rules):
    raw = deepcopy(raw_config)
    raw["schema_version"] = 5
    raw["groups"]["newsletter"]["runtime"] = {
        "permissions": {"mode": "restricted", "rules": group_rules}
    }
    raw["groups"]["newsletter"]["agents"][0]["runtime"] = {
        "permissions": {"rules": agent_rules}
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_instance_rules_are_additive(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        group_rules=[{"path": "C:/ws", "tools": ["read"]}],
        agent_rules=[{"path": "C:/ws/tests", "tools": ["read", "write"]}],
    )

    resolved = resolve_effective_policy(config, "newsletter", "builder")
    paths = [r.path for r in resolved.rules if r.path is not None]

    assert Path("C:/ws") in paths
    assert Path("C:/ws/tests") in paths


def test_same_path_in_group_and_instance_unions_tools(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        group_rules=[{"path": "C:/ws", "tools": ["read"]}],
        agent_rules=[{"path": "C:/ws", "tools": ["search"]}],
    )

    resolved = resolve_effective_policy(config, "newsletter", "builder")

    assert set(resolved.tools_for(Path("C:/ws/a"))) == {"read", "search"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission_resolution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.permissions'`

- [ ] **Step 3: Reshape the policy model**

In `agency/integrations/models.py`, delete `PathPolicyMode`, `ToolPolicyMode` and `ResolvedToolPolicy`, and replace `EffectiveRuntimePolicy` and `RuntimeCapabilities` with:

```python
PermissionMode = Literal["restricted", "unrestricted"]


@dataclass(frozen=True)
class ResolvedPermissionRule:
    path: Path | None
    tools: tuple[str, ...] | None


@dataclass(frozen=True)
class EffectiveRuntimePolicy:
    timeout: int
    mode: PermissionMode
    rules: tuple[ResolvedPermissionRule, ...] = ()

    def tools_for(self, path: Path) -> tuple[str, ...] | None:
        """Tools permitted on `path`. None means every tool."""
        target = Path(path)
        best: ResolvedPermissionRule | None = None
        for candidate in self.rules:
            if candidate.path is None:
                continue
            if not _covers(candidate.path, target):
                continue
            if best is None or len(candidate.path.parts) > len(best.path.parts):
                best = candidate
        if best is not None:
            return best.tools
        return None if self.mode == "unrestricted" else ()

    @property
    def scoped_tools(self) -> frozenset[str]:
        """Tools whose grant differs between rules."""
        granted: list[frozenset[str] | None] = [
            None if rule.tools is None else frozenset(rule.tools)
            for rule in self.rules
            if rule.path is not None
        ]
        if len(granted) < 2:
            return frozenset()
        universe: set[str] = set()
        for entry in granted:
            if entry is not None:
                universe |= entry
        return frozenset(
            name
            for name in universe
            if any((entry is None or name in entry) for entry in granted)
            and any((entry is not None and name not in entry) for entry in granted)
        )

    def with_launch_zones(self, launch_dir: Path) -> "EffectiveRuntimePolicy":
        from agency.permissions.zones import launch_zone_rules

        authored = tuple(
            rule
            for rule in self.rules
            if rule.path is None or not _under_launch(rule.path, launch_dir)
        )
        return replace(self, rules=authored + launch_zone_rules(launch_dir))


def _covers(rule_path: Path, target: Path) -> bool:
    try:
        target.resolve(strict=False).relative_to(rule_path.resolve(strict=False))
        return True
    except ValueError:
        return False


def _under_launch(rule_path: Path, launch_dir: Path) -> bool:
    return _covers(Path(launch_dir), Path(rule_path))


@dataclass(frozen=True)
class RuntimeCapabilities:
    permission_modes: frozenset[PermissionMode] = frozenset()
    path_scopable_tools: frozenset[str] = frozenset()
```

Add `from dataclasses import dataclass, replace`, `from pathlib import Path` and
`from typing import Literal` to the imports.

- [ ] **Step 4: Create the zones module**

Create `agency/permissions/__init__.py`:

```python
from .zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX, launch_zone_rules

__all__ = [
    "ZONE_INSTRUCTIONS",
    "ZONE_MEMORY",
    "ZONE_OUTBOX",
    "launch_zone_rules",
]
```

Create `agency/permissions/zones.py`:

```python
"""Agency's own grants inside a job's launch view.

These rules are generated, never authored, and cannot be widened by
configuration: the instructions an agent runs under must not be writable by
that agent.
"""

from __future__ import annotations

from pathlib import Path

from agency.integrations.models import ResolvedPermissionRule

ZONE_INSTRUCTIONS = "instructions"
ZONE_OUTBOX = ".agency/outbox"
ZONE_MEMORY = ".agency/memory"


def launch_zone_rules(launch_dir: Path) -> tuple[ResolvedPermissionRule, ...]:
    launch_dir = Path(launch_dir)
    return (
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_INSTRUCTIONS.split("/")),
            tools=("read",),
        ),
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_OUTBOX.split("/")),
            tools=("read", "write"),
        ),
        ResolvedPermissionRule(
            path=launch_dir.joinpath(*ZONE_MEMORY.split("/")),
            tools=("read", "write"),
        ),
    )
```

- [ ] **Step 5: Rewrite resolution**

In `agency/configuration/effective.py`, replace `_resolve_tools` and `_resolve_sandbox` with a single merge, and rewrite `resolve_effective_policy` to build the new policy:

```python
def _merge_rules(
    group: GroupConfig,
    agent: AgentInstance,
) -> tuple[ResolvedPermissionRule, ...]:
    """Instance rules are additive; identical paths union their tools."""
    merged: list[ResolvedPermissionRule] = []
    index: dict[str | None, int] = {}

    for source in (group.runtime.permissions.rules, agent.runtime.permissions.rules):
        for rule in source:
            key = None if rule.path is None else _platform_path_key(Path(rule.path))
            resolved = ResolvedPermissionRule(
                path=None if rule.path is None else Path(rule.path).resolve(strict=False),
                tools=None if rule.tools is None else tuple(rule.tools),
            )
            if key in index:
                existing = merged[index[key]]
                if existing.tools is None or resolved.tools is None:
                    union = None
                else:
                    union = tuple(dict.fromkeys((*existing.tools, *resolved.tools)))
                merged[index[key]] = replace(existing, tools=union)
                continue
            index[key] = len(merged)
            merged.append(resolved)

    return tuple(merged)


def _resolve_mode(group: GroupConfig, agent: AgentInstance) -> str:
    if "permissions" in agent.runtime.model_fields_set and (
        "mode" in agent.runtime.permissions.model_fields_set
    ):
        return agent.runtime.permissions.mode
    return group.runtime.permissions.mode
```

and in `resolve_effective_policy`:

```python
    policy = EffectiveRuntimePolicy(
        timeout=_resolve_timeout(group, agent, timeout_override),
        mode=_resolve_mode(group, agent),
        rules=_merge_rules(group, agent),
    )
```

Delete the `sandbox-contradiction` issue: under the new mode semantics a path rule is meaningful in both modes.

- [ ] **Step 6: Run the focused tests**

Run: `python -m pytest tests/test_permission_resolution.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agency/integrations/models.py agency/configuration/effective.py agency/permissions/ tests/test_permission_resolution.py
git commit -m "feat(permissions): resolve rules into an effective policy"
```

---

### Task 3: Capability negotiation

**Files:**
- Modify: `agency/integrations/__init__.py`
- Modify: all nine integrations under `agency/integrations/agency/`
- Test: `tests/test_permission_capabilities.py`

**Interfaces:**
- Consumes: `EffectiveRuntimePolicy.scoped_tools`, `RuntimeCapabilities` (Task 2).
- Produces: validation issue codes `unsupported-permission-mode` and `unsupported-tool-scoping`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permission_capabilities.py`:

```python
from __future__ import annotations

from pathlib import Path

from agency.integrations import BaseIntegration, get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    ResolvedPermissionRule,
    RuntimeCapabilities,
)


class Scoping(BaseIntegration):
    name = "scoping"
    display_name = "Scoping"
    supports_execution = True
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"restricted", "unrestricted"}),
        path_scopable_tools=frozenset({"write"}),
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


class Flat(Scoping):
    name = "flat"
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"unrestricted"}),
    )


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=60,
        mode=mode,
        rules=tuple(
            ResolvedPermissionRule(path=Path(p), tools=t) for p, t in rules
        ),
    )


def test_flat_integration_rejects_an_unsupported_mode():
    issues = Flat().validate_runtime_policy(policy(("/a", ("read",))))

    assert "unsupported-permission-mode" in {i.code for i in issues}


def test_flat_integration_rejects_differing_tool_grants():
    unrestricted = policy(
        ("/a", ("read",)), ("/b", ("read", "write")), mode="unrestricted"
    )

    issues = Flat().validate_runtime_policy(unrestricted)

    assert [i.code for i in issues] == ["unsupported-tool-scoping"]
    assert "write" in issues[0].message
    assert "flat" in issues[0].message


def test_flat_integration_accepts_uniform_grants():
    uniform = policy(("/a", ("read",)), ("/b", ("read",)), mode="unrestricted")

    assert Flat().validate_runtime_policy(uniform) == ()


def test_scoping_integration_accepts_differing_write_grants():
    assert Scoping().validate_runtime_policy(
        policy(("/a", ("read",)), ("/b", ("read", "write")))
    ) == ()


def test_no_shipped_integration_scopes_write():
    for name in (
        "copilot", "claude-code", "codex", "gemini",
        "aider", "goose", "opencode", "pi", "script",
    ):
        caps = get_integration(name).runtime_capabilities
        assert "write" not in caps.path_scopable_tools, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_permission_capabilities.py -v`
Expected: FAIL with `AttributeError` on `permission_modes`

- [ ] **Step 3: Rewrite the validator**

In `agency/integrations/__init__.py`, replace the two checks inside `validate_runtime_policy` with:

```python
        if policy.mode not in self.runtime_capabilities.permission_modes:
            issues.append(
                ValidationIssue(
                    code="unsupported-permission-mode",
                    scope=f"integrations.{self.name}",
                    field="runtime.permissions.mode",
                    message=(
                        f"Integration '{self.name}' cannot enforce permission "
                        f"mode '{policy.mode}'."
                    ),
                    corrective_hint="Use a mode this integration supports.",
                )
            )

        unscopable = policy.scoped_tools - self.runtime_capabilities.path_scopable_tools
        if unscopable:
            differing = ", ".join(
                str(rule.path) for rule in policy.rules if rule.path is not None
            )
            issues.append(
                ValidationIssue(
                    code="unsupported-tool-scoping",
                    scope=f"integrations.{self.name}",
                    field="runtime.permissions.rules",
                    message=(
                        f"Integration '{self.name}' cannot vary "
                        f"{', '.join(sorted(unscopable))} between paths. The "
                        f"rules grant it differently across: {differing}."
                    ),
                    corrective_hint=(
                        "Grant these tools identically on every rule, or run "
                        "this agent on an integration that can scope them."
                    ),
                )
            )
```

- [ ] **Step 4: Update every integration's capabilities**

In each of `aider.py`, `claude_code.py`, `codex.py`, `gemini.py`, `goose.py`, `opencode.py`, `pi.py`, `script.py`, replace the capabilities block with:

```python
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"unrestricted"}),
    )
```

In `copilot.py`:

```python
    runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"restricted", "unrestricted"}),
    )
```

No integration declares `path_scopable_tools`. That is deliberate and is what keeps behaviour unchanged.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/test_permission_capabilities.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/ tests/test_permission_capabilities.py
git commit -m "feat(integrations): negotiate permission modes and tool scoping"
```

---

### Task 4: Derived executor eligibility

**Files:**
- Modify: `agency/app.py` (`execution_agent_options`, and the decide-form validation)
- Modify: `agency/records/validation.py` (`writable_agent_names`)
- Modify: `agency/cli.py` (the two `capabilities` reads)
- Test: `tests/test_executor_eligibility.py`

**Interfaces:**
- Consumes: `resolve_effective_policy`, `EffectiveRuntimePolicy.tools_for`.
- Produces: `agency.permissions.eligibility.may_execute_decisions(config, group_key, agent_name) -> bool`.

An agent is eligible when its effective policy grants `write` on a rule whose path is the group's `workspace_path` **itself**. A grant on a subdirectory does not qualify.

- [ ] **Step 1: Write the failing test**

Create `tests/test_executor_eligibility.py`:

```python
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from agency.configuration.store import ConfigStore
from agency.permissions.eligibility import may_execute_decisions


def _config(tmp_path: Path, raw_config, rules):
    raw = deepcopy(raw_config)
    raw["schema_version"] = 5
    workspace = raw["groups"]["newsletter"]["workspace_path"]
    raw["groups"]["newsletter"]["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [dict(r, path=r["path"].replace("<ws>", workspace)) for r in rules],
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_write_on_the_workspace_confers_eligibility(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["read", "write"]}])

    assert may_execute_decisions(config, "newsletter", "builder") is True


def test_read_only_workspace_does_not(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["read"]}])

    assert may_execute_decisions(config, "newsletter", "builder") is False


def test_write_on_a_subdirectory_does_not(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        [
            {"path": "<ws>", "tools": ["read"]},
            {"path": "<ws>/scratch", "tools": ["read", "write"]},
        ],
    )

    assert may_execute_decisions(config, "newsletter", "builder") is False


def test_omitted_tools_confers_eligibility(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>"}])

    assert may_execute_decisions(config, "newsletter", "builder") is True


def test_unknown_group_is_not_eligible(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, [{"path": "<ws>", "tools": ["write"]}])

    assert may_execute_decisions(config, "nope", "builder") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_executor_eligibility.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.permissions.eligibility'`

- [ ] **Step 3: Implement the derivation**

Create `agency/permissions/eligibility.py`:

```python
"""Whether an agent may be trusted to execute a decision.

Derived from the permission rules rather than stored, so the answer cannot
disagree with the policy the agent actually runs under.
"""

from __future__ import annotations

from pathlib import Path

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.issues import ValidationFailed


def may_execute_decisions(config, group_key: str, agent_name: str) -> bool:
    group = config.groups.get(group_key)
    if group is None or agent_name not in group.agents:
        return False
    try:
        policy = resolve_effective_policy(config, group_key, agent_name)
    except (ValidationFailed, KeyError):
        return False

    workspace = Path(group.workspace_path).resolve(strict=False)
    for rule in policy.rules:
        if rule.path is None:
            continue
        if Path(rule.path).resolve(strict=False) != workspace:
            continue
        return rule.tools is None or "write" in rule.tools
    return False
```

Export it from `agency/permissions/__init__.py`.

- [ ] **Step 4: Re-point the call sites**

In `agency/app.py`, `execution_agent_options` currently reads
`instance.get("capabilities", {}).get("write") is True`. Replace that condition with
`may_execute_decisions(config, group_key, instance_name)`, keeping the existing
`integration.supports_execution` check beside it. Apply the same substitution to the
decide-form validation that rejects a non-writable executor.

In `agency/records/validation.py`, rewrite `writable_agent_names` to call
`may_execute_decisions` for each agent in the group instead of reading
`agent.capabilities.write`.

In `agency/cli.py`, replace both `capabilities` reads the same way.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/test_executor_eligibility.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/permissions/ agency/app.py agency/records/validation.py agency/cli.py tests/test_executor_eligibility.py
git commit -m "feat(permissions): derive executor eligibility from the rules"
```

---

### Task 5: Job spec carries the policy and the instance digest

**Files:**
- Modify: `agency/jobs/models.py` (`RuntimePolicySnapshot`, `BlueprintRef`)
- Modify: `agency/jobs/resolution.py`
- Test: `tests/test_job_policy_snapshot.py`

**Interfaces:**
- Consumes: `EffectiveRuntimePolicy` (Task 2).
- Produces: `RuntimePolicySnapshot(timeout, mode, rules)` where `rules` is a tuple of `{"path": str | None, "tools": list[str] | None}`; `BlueprintRef.instance_digest: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_job_policy_snapshot.py`:

```python
from __future__ import annotations

from pathlib import Path

from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule
from agency.jobs.models import RuntimePolicySnapshot


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=60,
        mode=mode,
        rules=tuple(ResolvedPermissionRule(path=p, tools=t) for p, t in rules),
    )


def test_round_trip_preserves_rules():
    original = policy((Path("/ws"), ("read",)), (Path("/ws/tests"), ("read", "write")))

    restored = RuntimePolicySnapshot.from_effective_policy(original).to_effective_policy()

    assert restored.mode == "restricted"
    assert [(r.path, r.tools) for r in restored.rules] == [
        (Path("/ws").resolve(strict=False), ("read",)),
        (Path("/ws/tests").resolve(strict=False), ("read", "write")),
    ]


def test_round_trip_preserves_omitted_tools():
    restored = RuntimePolicySnapshot.from_effective_policy(
        policy((Path("/ws"), None))
    ).to_effective_policy()

    assert restored.rules[0].tools is None


def test_round_trip_preserves_empty_tools():
    restored = RuntimePolicySnapshot.from_effective_policy(
        policy((Path("/ws"), ()))
    ).to_effective_policy()

    assert restored.rules[0].tools == ()


def test_round_trip_preserves_a_pathless_rule():
    restored = RuntimePolicySnapshot.from_effective_policy(
        policy((None, ("fetch",)))
    ).to_effective_policy()

    assert restored.rules[0].path is None
    assert restored.rules[0].tools == ("fetch",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_job_policy_snapshot.py -v`
Expected: FAIL with `TypeError` on the removed `sandbox_mode` argument

- [ ] **Step 3: Reshape the snapshot**

In `agency/jobs/models.py` (add `from pathlib import Path` and import
`EffectiveRuntimePolicy` and `ResolvedPermissionRule` from
`agency.integrations.models`):

```python
@dataclass(frozen=True)
class RuntimePolicySnapshot:
    timeout: int
    mode: str
    rules: tuple[dict, ...] = ()

    @classmethod
    def from_effective_policy(cls, policy) -> "RuntimePolicySnapshot":
        return cls(
            timeout=policy.timeout,
            mode=policy.mode,
            rules=tuple(
                {
                    "path": None if rule.path is None else str(Path(rule.path).resolve(strict=False)),
                    "tools": None if rule.tools is None else list(rule.tools),
                }
                for rule in policy.rules
            ),
        )

    def to_effective_policy(self) -> EffectiveRuntimePolicy:
        return EffectiveRuntimePolicy(
            timeout=self.timeout,
            mode=self.mode,
            rules=tuple(
                ResolvedPermissionRule(
                    path=None if entry["path"] is None else Path(entry["path"]),
                    tools=None if entry["tools"] is None else tuple(entry["tools"]),
                )
                for entry in self.rules
            ),
        )
```

Add `instance_digest: str = ""` to `BlueprintRef`.

- [ ] **Step 4: Leave the digest empty for now**

`BlueprintRef.instance_digest` defaults to `""`. Do **not** try to populate it in
this task — the value is produced by `instance_digest()`, which Task 6 creates.
Task 6 wires the two together. Leave `agency/jobs/resolution.py` alone apart from
any change needed to keep it importable.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/test_job_policy_snapshot.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/jobs/models.py agency/jobs/resolution.py tests/test_job_policy_snapshot.py
git commit -m "feat(jobs): snapshot permission rules on the job spec"
```

---

### Task 6: Compilation becomes a per-instance projection

**Files:**
- Modify: `agency/blueprints/cache.py`
- Modify: `agency/jobs/resolution.py` (the `cache.ensure_compiled` call)
- Test: `tests/test_instance_projection.py`

**Interfaces:**
- Consumes: `EffectiveRuntimePolicy` (Task 2), `AgentIdentity`.
- Produces:
  - `CacheRef.instance_digest: str`
  - `agency.blueprints.cache.instance_digest(identity, policy) -> str`
  - `_entry_path` gains the digest component.

- [ ] **Step 1: Write the failing test**

Create `tests/test_instance_projection.py`:

```python
from __future__ import annotations

from pathlib import Path

from agency.blueprints.cache import CacheRef, _entry_path, instance_digest
from agency.configuration.models import AgentIdentity
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def policy(*rules, timeout=60, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=timeout,
        mode=mode,
        rules=tuple(ResolvedPermissionRule(path=Path(p), tools=t) for p, t in rules),
    )


IDENTITY = AgentIdentity(display_name="Duncan", title="Test Engineer")


def test_identity_changes_the_digest():
    other = AgentIdentity(display_name="Gurney", title="Test Engineer")

    assert instance_digest(IDENTITY, policy()) != instance_digest(other, policy())


def test_permission_rules_change_the_digest():
    a = policy(("/ws", ("read",)))
    b = policy(("/ws", ("read", "write")))

    assert instance_digest(IDENTITY, a) != instance_digest(IDENTITY, b)


def test_mode_changes_the_digest():
    a = policy(("/ws", ("read",)), mode="restricted")
    b = policy(("/ws", ("read",)), mode="unrestricted")

    assert instance_digest(IDENTITY, a) != instance_digest(IDENTITY, b)


def test_timeout_does_not_change_the_digest():
    a = policy(("/ws", ("read",)), timeout=60)
    b = policy(("/ws", ("read",)), timeout=1800)

    assert instance_digest(IDENTITY, a) == instance_digest(IDENTITY, b)


def test_digest_is_stable_across_calls():
    assert instance_digest(IDENTITY, policy()) == instance_digest(IDENTITY, policy())


def test_entry_path_includes_the_instance_digest(tmp_path: Path):
    ref = CacheRef(
        integration="copilot",
        projector_version="v1",
        source_digest="abc",
        instance_digest="def",
    )

    assert _entry_path(tmp_path, ref) == tmp_path / "copilot" / "v1" / "abc" / "def"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instance_projection.py -v`
Expected: FAIL with `ImportError: cannot import name 'instance_digest'`

- [ ] **Step 3: Implement the digest and re-key**

In `agency/blueprints/cache.py`:

```python
@dataclass(frozen=True)
class CacheRef:
    integration: str
    projector_version: str
    source_digest: str
    instance_digest: str = ""


def instance_digest(identity, policy) -> str:
    """Digest the instance properties that change what is rendered.

    Timeout, memory selection, routines and prompt registrations are excluded:
    none of them alters the projected runtime.
    """
    payload = {
        "identity": {
            "display_name": identity.display_name,
            "title": identity.title,
            "emoji": identity.emoji,
        },
        "mode": policy.mode,
        "rules": [
            {
                "path": None if rule.path is None else str(Path(rule.path).resolve(strict=False)),
                "tools": None if rule.tools is None else sorted(rule.tools),
            }
            for rule in policy.rules
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

Update `_cache_key` and `_entry_path`:

```python
def _cache_key(ref: CacheRef) -> str:
    return (
        f"{ref.integration}--{ref.projector_version}--"
        f"{ref.source_digest}--{ref.instance_digest}"
    )


def _entry_path(root: Path, ref: CacheRef) -> Path:
    return (
        root
        / ref.integration
        / ref.projector_version
        / ref.source_digest
        / ref.instance_digest
    )
```

Give `ensure_compiled` an `instance_digest` keyword and thread it into the `CacheRef` it builds.

- [ ] **Step 4: Pass the digest from resolution**

In `agency/jobs/resolution.py`, compute the digest after the policy is resolved,
pass it to `cache.ensure_compiled`, and set it on the `BlueprintRef` that Task 5
added:

```python
    digest = instance_digest(agent.identity, runtime_policy)
    artifact = cache.ensure_compiled(agent.integration, inspection, instance_digest=digest)
```

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/test_instance_projection.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/blueprints/cache.py agency/jobs/resolution.py tests/test_instance_projection.py
git commit -m "feat(cache): key compiled artifacts on the agent instance"
```

---

### Task 7: Zoned launch view

**Files:**
- Modify: `agency/jobs/launch_view.py`
- Modify: `agency/records/outbox.py` (zone constants)
- Modify: `agency/jobs/execution.py` (apply the zones to the policy)
- Test: `tests/test_launch_zones.py`

**Interfaces:**
- Consumes: `ZONE_INSTRUCTIONS`, `ZONE_OUTBOX`, `ZONE_MEMORY`, `launch_zone_rules`, `EffectiveRuntimePolicy.with_launch_zones` (Task 2).
- Produces: `create_launch_view` places the projected runtime under `instructions/`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_launch_zones.py`:

```python
from __future__ import annotations

from pathlib import Path

from agency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX


def test_zone_names_are_distinct():
    assert len({ZONE_INSTRUCTIONS, ZONE_OUTBOX, ZONE_MEMORY}) == 3


def test_outbox_and_memory_live_under_the_agency_directory():
    assert ZONE_OUTBOX.startswith(".agency/")
    assert ZONE_MEMORY.startswith(".agency/")


def test_instructions_zone_is_not_under_the_agency_directory():
    assert not ZONE_INSTRUCTIONS.startswith(".agency")
```

Add to the same file a test driving `create_launch_view`:

```python
from types import SimpleNamespace

from agency.jobs.launch_view import create_launch_view


def _artifact(tmp_path: Path):
    entry = tmp_path / "entry"
    runtime = entry / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    (runtime / ".agents").mkdir()
    (runtime / ".agents" / "skill.md").write_text("skill\n", encoding="utf-8")
    return SimpleNamespace(entry_path=entry, runtime_path=runtime)


def test_projected_runtime_lands_under_the_instructions_zone(tmp_path: Path):
    launch = create_launch_view(_artifact(tmp_path), tmp_path / "launch")

    assert (launch / ZONE_INSTRUCTIONS / "AGENTS.md").is_file()
    assert (launch / ZONE_INSTRUCTIONS / ".agents" / "skill.md").is_file()
    assert not (launch / "AGENTS.md").exists()


def test_zone_directories_are_created(tmp_path: Path):
    launch = create_launch_view(_artifact(tmp_path), tmp_path / "launch")

    assert launch.joinpath(*ZONE_OUTBOX.split("/")).is_dir()
    assert launch.joinpath(*ZONE_MEMORY.split("/")).is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_launch_zones.py -v`
Expected: FAIL on the missing `instructions/` placement

- [ ] **Step 3: Place the projected runtime under `instructions/`**

In `agency/jobs/launch_view.py`, `create_launch_view` currently copies the cached runtime into the destination root. Copy it into `destination / ZONE_INSTRUCTIONS` instead, and create the outbox and memory zone directories beside it. Keep every existing reparse-point and non-regular-file guard exactly as it is.

- [ ] **Step 4: Point the outbox at the zone constants**

In `agency/records/outbox.py`, redefine the relative constants in terms of the zones so the two modules cannot drift:

```python
from agency.permissions.zones import ZONE_MEMORY, ZONE_OUTBOX

OUTBOX_RELATIVE_OBSERVATIONS = f"{ZONE_OUTBOX}/observations"
OUTBOX_RELATIVE_PROPOSALS = f"{ZONE_OUTBOX}/proposals"
OUTBOX_RELATIVE_MEMORY = ZONE_MEMORY
```

- [ ] **Step 5: Apply the zones to the policy at launch**

In `agency/jobs/execution.py`, where the `IntegrationRunRequest` is built, pass
`runtime_policy=runtime_policy.with_launch_zones(launch_view)` so the integration
receives a policy that already contains Agency's generated rules.

- [ ] **Step 6: Run the focused tests**

Run: `python -m pytest tests/test_launch_zones.py tests/test_records_outbox.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agency/jobs/launch_view.py agency/records/outbox.py agency/jobs/execution.py tests/test_launch_zones.py
git commit -m "feat(jobs): render the launch view as access zones"
```

---

### Task 8: Migration command

**Files:**
- Create: `agency/configuration/migrate.py`
- Modify: `agency/cli.py` (register `config migrate`)
- Test: `tests/test_config_migrate.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — it operates on raw mappings, not models.
- Produces: `migrate_v4_to_v5(raw: dict) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_migrate.py`:

```python
from __future__ import annotations

import pytest

from agency.configuration.migrate import migrate_v4_to_v5


def v4(group_runtime, agent):
    return {
        "schema_version": 4,
        "agency": {"title": "Agency"},
        "groups": {
            "g": {
                "name": "G",
                "workspace_path": "C:/ws",
                "path": "C:/state",
                "default_integration": "copilot",
                "runtime": group_runtime,
                "agents": [dict({"name": "a", "blueprint": "b", "integration": "copilot"}, **agent)],
            }
        },
    }


def rules_of(result, group="g"):
    return result["groups"][group]["runtime"]["permissions"]["rules"]


def test_schema_version_becomes_five():
    result = migrate_v4_to_v5(v4({"sandbox": {"mode": "unrestricted"}}, {}))

    assert result["schema_version"] == 5


def test_restricted_roots_become_one_rule_each():
    result = migrate_v4_to_v5(
        v4(
            {
                "sandbox": {"mode": "restricted", "roots": ["C:/ws", "C:/other"]},
                "tools": {"mode": "allowlist", "names": ["read", "search"]},
            },
            {},
        )
    )

    assert rules_of(result) == [
        {"path": "C:/ws", "tools": ["read", "search"]},
        {"path": "C:/other", "tools": ["read", "search"]},
    ]
    assert result["groups"]["g"]["runtime"]["permissions"]["mode"] == "restricted"


def test_tools_all_omits_the_tools_key():
    result = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "all"}}, {})
    )

    assert rules_of(result) == [{"path": "C:/ws"}]


def test_tools_none_becomes_an_empty_list():
    result = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "none"}}, {})
    )

    assert rules_of(result) == [{"path": "C:/ws", "tools": []}]


def test_unrestricted_becomes_a_single_pathless_rule():
    result = migrate_v4_to_v5(
        v4({"sandbox": {"mode": "unrestricted"}, "tools": {"mode": "allowlist", "names": ["fetch"]}}, {})
    )

    assert rules_of(result) == [{"tools": ["fetch"]}]


def test_capabilities_write_true_adds_write_to_the_workspace_rule():
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"capabilities": {"write": True}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    assert {"path": "C:/ws", "tools": ["read", "write"]} in agent["runtime"]["permissions"]["rules"]


def test_capabilities_write_false_adds_no_write():
    result = migrate_v4_to_v5(
        v4(
            {"sandbox": {"mode": "restricted", "roots": ["C:/ws"]}, "tools": {"mode": "allowlist", "names": ["read"]}},
            {"capabilities": {"write": False}},
        )
    )
    agent = result["groups"]["g"]["agents"][0]

    assert "capabilities" not in agent
    assert agent.get("runtime", {}).get("permissions", {}).get("rules", []) == []


def test_superseded_keys_are_removed():
    result = migrate_v4_to_v5(v4({"sandbox": {"mode": "unrestricted"}, "tools": {"mode": "all"}}, {}))
    runtime = result["groups"]["g"]["runtime"]

    assert "sandbox" not in runtime
    assert "tools" not in runtime


def test_a_version_five_document_is_refused():
    with pytest.raises(ValueError, match="already"):
        migrate_v4_to_v5({"schema_version": 5})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.configuration.migrate'`

- [ ] **Step 3: Implement the migration**

Create `agency/configuration/migrate.py`:

```python
"""Translate a schema_version 4 document into version 5.

Works on plain mappings, never on the config models: a document the current
models reject must still be migratable.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _tools_for(tools: dict[str, Any]) -> list[str] | None:
    """None means "omit the key", which in version 5 means every tool."""
    mode = tools.get("mode", "all")
    if mode == "all":
        return None
    if mode == "none":
        return []
    return list(tools.get("names", ()))


def _rule(path: str | None, tools: list[str] | None) -> dict[str, Any]:
    rule: dict[str, Any] = {}
    if path is not None:
        rule["path"] = path
    if tools is not None:
        rule["tools"] = tools
    return rule


def _permissions(runtime: dict[str, Any], roots: list[str]) -> dict[str, Any]:
    sandbox = runtime.get("sandbox", {}) or {}
    tools = _tools_for(runtime.get("tools", {}) or {})
    mode = sandbox.get("mode", "unrestricted")
    if mode == "unrestricted":
        return {"mode": "unrestricted", "rules": [_rule(None, tools)]}
    return {"mode": "restricted", "rules": [_rule(root, tools) for root in roots]}


def migrate_v4_to_v5(raw: dict[str, Any]) -> dict[str, Any]:
    version = raw.get("schema_version")
    if version == 5:
        raise ValueError("configuration is already at schema_version 5")
    if version != 4:
        raise ValueError(f"cannot migrate schema_version {version!r}")

    result = deepcopy(raw)
    result["schema_version"] = 5

    for group in (result.get("groups") or {}).values():
        runtime = group.setdefault("runtime", {})
        sandbox = runtime.get("sandbox", {}) or {}
        group_roots = [str(root) for root in (sandbox.get("roots") or ())]
        group_tools = _tools_for(runtime.get("tools", {}) or {})
        workspace = str(group.get("workspace_path", ""))

        runtime["permissions"] = _permissions(runtime, group_roots)
        runtime.pop("sandbox", None)
        runtime.pop("tools", None)

        for agent in group.get("agents") or ():
            agent_runtime = agent.setdefault("runtime", {})
            agent_sandbox = agent_runtime.get("sandbox", {}) or {}
            extra = [str(root) for root in (agent_sandbox.get("additional_roots") or ())]
            agent_tools = (
                _tools_for(agent_runtime["tools"])
                if "tools" in agent_runtime
                else group_tools
            )

            rules = [_rule(root, agent_tools) for root in extra]

            capabilities = agent.pop("capabilities", {}) or {}
            if capabilities.get("write") and workspace:
                writable = list(agent_tools) if agent_tools is not None else None
                if writable is not None and "write" not in writable:
                    writable.append("write")
                rules.append(_rule(workspace, writable))

            agent_runtime.pop("sandbox", None)
            agent_runtime.pop("tools", None)
            if rules:
                agent_runtime["permissions"] = {"rules": rules}
            elif not agent_runtime:
                agent.pop("runtime", None)

    return result
```

- [ ] **Step 4: Register the CLI command**

Add a `config migrate` subcommand to `agency/cli.py` that loads the raw YAML, calls `migrate_v4_to_v5`, and writes it back through the same locked, revision-checked, atomic path other config writes use. It must refuse when the document is already at version 5, and print the path it rewrote.

- [ ] **Step 5: Run the focused tests**

Run: `python -m pytest tests/test_config_migrate.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/configuration/migrate.py agency/cli.py tests/test_config_migrate.py
git commit -m "feat(config): migrate schema version 4 to 5"
```

---

### Task 9: Fixtures, documentation, and the full suite

By this point the suite has been red since Task 1. This task makes it green and updates the authority documents.

**Files:**
- Modify: `tests/conftest.py` and every fixture building a v4 config
- Modify: `AGENTS.md`, `kb/configuration.md`, `config.yaml.example`
- Modify: `skills/agency-setup/references/*` where they describe the old keys

- [ ] **Step 1: Convert the shared fixtures**

Update `raw_config` in `tests/conftest.py` to `schema_version: 5` with a `runtime.permissions` block, and remove `capabilities` from its agents. Then work outward: run the suite, take the first failing module, convert its fixture, repeat.

- [ ] **Step 2: Judge each remaining failure**

Most failures are fixtures. Some are assertions about behaviour that genuinely changed — the group root is now read-only, executor eligibility is derived, `writable_roots` no longer exists. For each, decide whether the test encodes intended behaviour or incidental exactness, and say which in your report. Do not delete a test to make the suite green; if one asserts something the design deliberately changed, rewrite it to assert the new behaviour.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. Report the final counts against the 1748/5 baseline and account for the difference.

- [ ] **Step 4: Update `AGENTS.md`**

Replace the sandbox and tools paragraphs in the configuration section with the permission model: one `runtime.permissions` block of `{path?, tools?}` rules, identical at group and instance level, instance rules additive, longest match governing, `tools` omitted meaning every tool and `[]` meaning none, and the two modes deciding only what happens to an uncovered path.

State that Agency contributes generated rules for the launch view zones which configuration cannot widen, and that compilation is a projection of blueprint, integration and the instance properties that affect rendering.

Replace the `capabilities.write` paragraphs — including the phase-1 wording about `enforces_write_boundary` — with the derived executor eligibility rule. Update the example configuration to the new shape.

- [ ] **Step 5: Update `kb/configuration.md` and `config.yaml.example`**

Same content, in each document's own voice. `config.yaml.example` must be a valid version-5 document.

- [ ] **Step 6: Run the full suite once more**

Run: `python -m pytest tests/ -q`
Expected: PASS, including `tests/test_agency_setup_skill.py`.

- [ ] **Step 7: Commit**

```bash
git add tests/ AGENTS.md kb/configuration.md config.yaml.example skills/
git commit -m "docs(permissions): document the permission model"
```

---

## Completion

- [ ] Run the complete suite from the worktree root and confirm it is green.
- [ ] Review the whole branch before integrating.
- [ ] Fast-forward `master` to the reviewed tip, re-run the suite, push both branches, and remove the worktree.
- [ ] Reinstall the editable package from the main checkout after the worktree is removed.
- [ ] `config.yaml` in the main checkout is still version 4 and Agency will refuse to start until `christag-agency config migrate` is run against it. That is intended; do not migrate it as part of this branch.
