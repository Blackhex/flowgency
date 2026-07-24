# Runtime CLI Probe Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the same four capability-aware runtime scenarios automatically for every installed built-in AI CLI while retaining deterministic parity contracts for all eight supported adapters.

**Architecture:** Production integrations own canonical CLI metadata, executable resolution, and truthful runtime/projector capabilities. Deterministic tests pin all eight adapters and their four scenario contracts; a dedicated live-only module discovers installed CLIs at collection time and runs basic execution, root instructions, selected skill, and write-boundary scenarios through the production validation and execution path.

**Tech Stack:** Python 3.14, pytest 9, subprocess-based AI CLI integrations, immutable runtime projectors

## Global Constraints

- The supported AI CLI set is exactly `copilot`, `claude-code`, `gemini`, `codex`, `aider`, `goose`, `opencode`, and `pi`.
- `script` is excluded because it has no intrinsic external AI command; `sdk` is excluded because it is non-executable.
- Normal pytest runs live scenarios automatically for supported AI CLIs whose production resolver finds a launchable command.
- Do not retain the `AGENCY_REAL_RUNTIME_PROBES` opt-in gate.
- Missing CLIs do not produce one skip per CLI or scenario; when none are installed, the live-only module reports one actionable skip.
- Installed but unauthenticated, offline, quota-limited, timed-out, malformed, or nonzero-exit CLIs fail with actionable diagnostics.
- All eight adapters receive the same four scenario contracts: `basic`, `root-instructions`, `selected-skill`, and `write-boundary`.
- Basic and root-instruction scenarios must execute successfully for every installed supported CLI.
- Selected-skill and write-boundary scenarios are capability-aware: supported capability executes live; unsupported capability rejects through production validation before per-scenario command resolution, task reading, or subprocess launch.
- Copilot keeps `restricted`/`unrestricted` path support and `all`/`allowlist` tool support.
- The other seven AI CLIs gain only `unrestricted` path support and `all` tool support.
- Root-instruction discovery is explicit for all eight AI CLI projectors and is live-verified only when the CLI is installed.
- Selected-skill declarations remain fail closed; at design time only Copilot declares discovery and selected-skill activation.
- The write scenario uses `restricted`, sandbox roots `[workspace_root]`, and allowlisted tools `[read, search]`; supported adapters must leave `write-probe.txt` absent.
- Every live launch preserves projected bytes, workspace state, task bytes, and repository status/diff.
- Public executable discovery is side-effect free and follows the same launch command path used by production execution.
- Copilot retains its Windows wrapper-to-real-`copilot.exe` behavior; do not apply a global `.exe` filter to other adapters.
- Keep the `real_runtime` marker for selection, but describe it as automatic for installed runtimes.
- Automatic probes may use authenticated credentials, network, model quota, and time; never print credentials.
- Baseline at `8b227a7`: isolated focused rerun passed; the full baseline recorded one transient pre-existing Windows `PermissionError` in `test_execute_job_waits_for_memory_before_starting_run`, which passed immediately in isolation. Do not modify job execution as part of this feature.

---

## File Structure

- `agency/integrations/__init__.py`: owns canonical CLI metadata, public executable resolution, required-command errors, and the default projector factory.
- `agency/integrations/agency/{copilot,claude_code,gemini,codex,aider,goose,opencode,pi}.py`: declare canonical commands and truthful runtime capabilities; use the shared required executable in production runs.
- `agency/projector_capabilities.py`: adds explicit root-instruction discovery capability.
- `agency/blueprints/projectors.py`: declares root-instruction discovery for Copilot, Claude Code, and Gemini projectors.
- `tests/_runtime_probe_helpers.py`: owns supported CLI/scenario constants, installed-runtime discovery, probe snapshots, request construction, and diagnostic assertions.
- `tests/test_runtime_projectors.py`: retains deterministic projector tests and pins static eight-CLI parity.
- `tests/test_runtime_projectors_live.py`: contains only automatically collected external-runtime scenarios.
- `tests/test_integration_contract.py`: pins CLI metadata, public resolution, runtime capabilities, and production invocation contracts.
- `tests/test_cache_locking.py`, `tests/test_compilation_cache.py`, `tests/test_job_submission.py`: update explicit `ProjectorCapabilities` construction.
- `tests/test_integration_sidecar.py`, `tests/test_integration_claude_code.py`: preserve Copilot resolution behavior and exact adapter invocation coverage.
- `pyproject.toml`: updates the `real_runtime` marker description.
- `kb/integrations.md`, `kb/contributing-integrations.md`: document capability distinctions and automatic installed-runtime probes.

---

### Task 1: Add Production-Owned CLI Discovery And Truthful Basic Runtime Support

**Files:**
- Modify: `agency/integrations/__init__.py`
- Modify: `agency/integrations/agency/copilot.py`
- Modify: `agency/integrations/agency/claude_code.py`
- Modify: `agency/integrations/agency/gemini.py`
- Modify: `agency/integrations/agency/codex.py`
- Modify: `agency/integrations/agency/aider.py`
- Modify: `agency/integrations/agency/goose.py`
- Modify: `agency/integrations/agency/opencode.py`
- Modify: `agency/integrations/agency/pi.py`
- Modify: `tests/test_integration_contract.py`
- Modify: `tests/test_integration_sidecar.py`
- Modify: `tests/test_integration_claude_code.py`

**Interfaces:**
- Produces: `BaseIntegration.cli_command: str | None`.
- Produces: `BaseIntegration.resolve_executable() -> str | None`, a side-effect-free production command resolver.
- Produces: `BaseIntegration.require_executable() -> str`, raising `IntegrationError` when unavailable.
- Produces: `BaseIntegration._resolve_launch_command(command: str) -> str`, overridden only by Copilot for Windows wrapper resolution.
- Produces: all eight AI CLI adapters accepting `EffectiveRuntimePolicy(unrestricted, all)` through normal validation.

- [ ] **Step 1: Add failing metadata, resolver, capability, and invocation tests**

Add these constants and tests to `tests/test_integration_contract.py`:

Extend the integration imports with `IntegrationError`, and keep the existing
`RuntimeCapabilities` import from `agency.integrations.models`.

```python
AI_CLI_COMMANDS = {
    "copilot": "copilot",
    "claude-code": "claude",
    "gemini": "gemini",
    "codex": "codex",
    "aider": "aider",
    "goose": "goose",
    "opencode": "opencode",
    "pi": "pi",
}

UNCONFINED_CAPABILITIES = RuntimeCapabilities(
    path_modes=frozenset({"unrestricted"}),
    tool_modes=frozenset({"all"}),
)


def test_builtin_ai_cli_integrations_declare_canonical_commands():
    declared = {
        name: integration.cli_command
        for name, integration in REGISTRY.items()
        if integration.cli_command is not None
    }

    assert declared == AI_CLI_COMMANDS
    assert REGISTRY["script"].cli_command is None
    assert REGISTRY["sdk"].cli_command is None


def test_builtin_ai_cli_runtime_capabilities_are_truthful():
    assert REGISTRY["copilot"].runtime_capabilities == RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"all", "allowlist"}),
    )
    for name in AI_CLI_COMMANDS.keys() - {"copilot"}:
        assert REGISTRY[name].runtime_capabilities == UNCONFINED_CAPABILITIES


def test_public_executable_resolver_returns_launchable_path(tmp_path, monkeypatch):
    executable = tmp_path / "probe.exe"
    executable.write_bytes(b"")
    executable.chmod(0o755)

    class ProbeIntegration(BaseIntegration):
        name = "probe"
        display_name = "Probe"
        cli_command = "probe"

    monkeypatch.setattr(
        "agency.integrations.shutil.which",
        lambda command: str(executable) if command == "probe" else None,
    )

    assert ProbeIntegration().resolve_executable() == str(executable.resolve())


def test_public_executable_resolver_returns_none_when_missing(monkeypatch):
    class ProbeIntegration(BaseIntegration):
        name = "probe"
        display_name = "Probe"
        cli_command = "probe"

    monkeypatch.setattr("agency.integrations.shutil.which", lambda command: None)

    assert ProbeIntegration().resolve_executable() is None
    with pytest.raises(IntegrationError, match="Probe CLI is unavailable"):
        ProbeIntegration().require_executable()
```

Replace `test_registry_runtime_capabilities_surface_is_fail_closed` with exact expectations for Copilot, Script, SDK, and the seven unconfined AI CLIs.

Add a parametrized invocation test for the seven non-Copilot adapters. Use a real `IntegrationRunRequest` with `unrestricted` + `all`, monkeypatch each integration's `resolve_executable` to `C:/runtime/agent.exe`, monkeypatch `subprocess.run`, and assert these exact argument shapes:

```python
AI_CLI_RUN_ARGUMENTS = {
    "claude-code": ["--dangerously-skip-permissions", "-p", "Run probe"],
    "gemini": ["-p", "Run probe"],
    "codex": ["exec", "--yolo", "Run probe"],
    "aider": ["--message-file", "{task_file}"],
    "goose": ["run", "Run probe"],
    "opencode": ["run", "Run probe"],
    "pi": ["-p", "Run probe"],
}


@pytest.mark.parametrize(
    ("integration_name", "expected_tail"),
    AI_CLI_RUN_ARGUMENTS.items(),
)
def test_ai_cli_run_uses_declared_command_and_validated_request(
    integration_name,
    expected_tail,
    tmp_path,
    monkeypatch,
):
    integration = REGISTRY[integration_name]
    launch_dir = tmp_path / "launch"
    workspace_root = tmp_path / "workspace"
    task_file = tmp_path / "task.md"
    launch_dir.mkdir()
    workspace_root.mkdir()
    task_file.write_text("Run probe", encoding="utf-8")
    request = IntegrationRunRequest(
        workspace_root=workspace_root,
        launch_dir=launch_dir,
        task_file=task_file,
        timeout=30,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=30,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tools=ResolvedToolPolicy("all", ()),
        ),
        skill=None,
        skill_arguments=(),
    )
    captured = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(arguments, 0, "ok", "")

    monkeypatch.setattr(
        integration,
        "resolve_executable",
        lambda: "C:/runtime/agent.exe",
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = integration.run(request)

    normalized_tail = [
        str(task_file) if value == "{task_file}" else value
        for value in expected_tail
    ]
    assert captured["arguments"] == [
        "C:/runtime/agent.exe",
        *normalized_tail,
    ]
    assert captured["kwargs"]["cwd"] == str(launch_dir)
    assert captured["kwargs"]["timeout"] == 30
    assert result.exit_code == 0
```

- [ ] **Step 2: Run the new contract tests to verify RED**

```powershell
python -m pytest `
  tests/test_integration_contract.py::test_builtin_ai_cli_integrations_declare_canonical_commands `
  tests/test_integration_contract.py::test_builtin_ai_cli_runtime_capabilities_are_truthful `
  tests/test_integration_contract.py::test_public_executable_resolver_returns_launchable_path `
  tests/test_integration_contract.py::test_public_executable_resolver_returns_none_when_missing `
  -q
```

Expected: FAIL because `cli_command`, the public resolver, and seven runtime capability declarations do not exist.

- [ ] **Step 3: Implement the shared executable contract**

Add this class surface to `BaseIntegration` in `agency/integrations/__init__.py`:

Add `import os` beside the existing standard-library imports.

```python
    cli_command: str | None = None

    def _find_cmd(self) -> str:
        if self.cli_command is None:
            raise IntegrationError(
                f"{self.display_name or self.name or 'Integration'} has no external CLI command."
            )
        return self._resolve_cmd(self.cli_command)

    def _resolve_launch_command(self, command: str) -> str:
        return command

    def resolve_executable(self) -> str | None:
        if self.cli_command is None:
            return None
        command = self._resolve_launch_command(self._find_cmd())
        candidate = Path(command).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        located = shutil.which(command)
        if located is None:
            return None
        return str(Path(located).resolve())

    def require_executable(self) -> str:
        command = self.resolve_executable()
        if command is None:
            label = self.display_name or self.name or "Integration"
            raise IntegrationError(
                f"{label} CLI is unavailable. Looked for: {self.cli_command}"
            )
        return command
```

Keep `_resolve_cmd` as the low-level PATH/user-local lookup helper.

- [ ] **Step 4: Declare all eight commands and truthful runtime capabilities**

In each adapter class, add its exact `cli_command` from `AI_CLI_COMMANDS`. In Claude Code, Gemini, Codex, Aider, Goose, OpenCode, and Pi, import `RuntimeCapabilities` and add:

```python
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"unrestricted"}),
        tool_modes=frozenset({"all"}),
    )
```

Delete those adapters' identical `_find_cmd` overrides. Replace each production `run` command lookup with:

```python
        cmd = self.require_executable()
```

Use `require_executable()` in Claude Code and Codex `prompt` methods as well.

For Copilot, add `cli_command = "copilot"`, remove its `_find_cmd` override, and add:

```python
    def _resolve_launch_command(self, command: str) -> str:
        return self._resolve_real_cmd(command)
```

Use `require_executable()` in Copilot's non-interactive `run` and `prompt`
methods.

Keep `_interactive_setup_command_prefix` on its existing
`_resolve_real_cmd(_find_cmd())` plus `_command_exists` path. Interactive setup
has a separate wrapper/PowerShell launch contract and is not used by live
non-interactive probes.

- [ ] **Step 5: Update Copilot and adapter unit tests**

In `tests/test_integration_sidecar.py`, replace the four invocation-only
monkeypatches at the current `_find_cmd` call sites with:

```python
monkeypatch.setattr(
    CopilotIntegration,
    "resolve_executable",
    lambda self: "copilot",
)
```

Retain the direct `_resolve_real_cmd` tests for wrapper behavior. Add:

```python
def test_copilot_public_resolver_returns_real_windows_executable(
    monkeypatch,
    tmp_path,
):
    import agency.integrations.agency.copilot as copilot_mod

    wrapper = tmp_path / "copilot.cmd"
    executable = tmp_path / "copilot.exe"
    wrapper.write_text("", encoding="utf-8")
    executable.write_bytes(b"")
    executable.chmod(0o755)
    integration = CopilotIntegration()
    monkeypatch.setattr(copilot_mod.sys, "platform", "win32")
    monkeypatch.setattr(integration, "_find_cmd", lambda: str(wrapper))
    monkeypatch.setattr(
        copilot_mod.shutil,
        "which",
        lambda name, path=None: (
            str(executable) if name == "copilot.exe" else None
        ),
    )

    assert integration.resolve_executable() == str(executable.resolve())
```

In `tests/test_integration_claude_code.py`, update both existing fail-closed
skill expectations to only:

```python
assert [issue.code for issue in issues] == ["unsupported-skill-activation"]
```

and:

```python
assert [issue.code for issue in excinfo.value.issues] == [
    "unsupported-skill-activation"
]
```

The parametrized contract test from Step 1 supplies exact validated invocation
coverage for Claude Code and the other six non-Copilot adapters.

- [ ] **Step 6: Run the deterministic integration suites**

```powershell
python -m pytest `
  tests/test_integration_contract.py `
  tests/test_integration_claude_code.py `
  tests/test_integration_sidecar.py `
  tests/test_interactive_setup.py `
  -q
```

Expected: PASS with no external CLI launch because subprocesses and resolvers are monkeypatched.

- [ ] **Step 7: Commit Task 1**

```powershell
git add agency/integrations/__init__.py agency/integrations/agency tests/test_integration_contract.py tests/test_integration_claude_code.py tests/test_integration_sidecar.py
git commit -m "feat(integrations): declare AI CLI runtime commands"
```

---

### Task 2: Make Root-Instruction Discovery Explicit

**Files:**
- Modify: `agency/projector_capabilities.py`
- Modify: `agency/blueprints/projectors.py`
- Modify: `agency/integrations/__init__.py`
- Modify: `agency/integrations/agency/codex.py`
- Modify: `agency/integrations/agency/aider.py`
- Modify: `agency/integrations/agency/goose.py`
- Modify: `agency/integrations/agency/opencode.py`
- Modify: `agency/integrations/agency/pi.py`
- Modify: `tests/test_integration_contract.py`
- Modify: `tests/test_runtime_projectors.py`
- Modify: `tests/test_cache_locking.py`
- Modify: `tests/test_compilation_cache.py`
- Modify: `tests/test_job_submission.py`

**Interfaces:**
- Produces: `ProjectorCapabilities.discovers_instructions: bool`.
- Produces: `BaseIntegration._default_projector(instruction_name: str, *, discovers_instructions: bool = False)`.
- Produces: all eight AI CLI projectors declaring `discovers_instructions=True`; Script and SDK remain false.

- [ ] **Step 1: Write failing projector capability tests**

Add to `tests/test_integration_contract.py`:

```python
def test_builtin_ai_cli_projectors_declare_instruction_discovery():
    for name in AI_CLI_COMMANDS:
        assert REGISTRY[name].projector.capabilities.discovers_instructions is True
    assert REGISTRY["script"].projector.capabilities.discovers_instructions is False
    assert REGISTRY["sdk"].projector.capabilities.discovers_instructions is False
```

Extend `test_projector_relocates_without_rewriting` in `tests/test_runtime_projectors.py` to assert every parametrized AI CLI projector declares instruction discovery before projection.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest `
  tests/test_integration_contract.py::test_builtin_ai_cli_projectors_declare_instruction_discovery `
  tests/test_runtime_projectors.py::test_projector_relocates_without_rewriting `
  -q
```

Expected: FAIL because `discovers_instructions` is absent.

- [ ] **Step 3: Add the required capability field**

Change `ProjectorCapabilities` to:

```python
@dataclass(frozen=True)
class ProjectorCapabilities:
    instruction_target: PurePosixPath
    skills_target: PurePosixPath
    discovers_instructions: bool
    discovers_skills: bool
    activates_selected_skill: bool
```

Do not provide a default; every constructor must make an explicit declaration.

- [ ] **Step 4: Update production projectors**

Set `discovers_instructions=True` in all three `PROJECTORS` entries in `agency/blueprints/projectors.py`.

Change the default projector factory to:

```python
    @staticmethod
    def _default_projector(
        instruction_name: str,
        *,
        discovers_instructions: bool = False,
    ) -> "RuntimeProjector":
        from agency.blueprints.projectors import StaticRuntimeProjector

        return StaticRuntimeProjector(
            version="v1",
            capabilities=ProjectorCapabilities(
                instruction_target=PurePosixPath(instruction_name),
                skills_target=PurePosixPath(".agents/skills"),
                discovers_instructions=discovers_instructions,
                discovers_skills=False,
                activates_selected_skill=False,
            ),
        )
```

Update Codex, Aider, Goose, OpenCode, and Pi to call `_default_projector(..., discovers_instructions=True)`. Leave Script and SDK calls unchanged so they remain false.

- [ ] **Step 5: Update explicit test constructors**

Add `discovers_instructions=True` to test projectors intended to emulate an AI CLI in `test_cache_locking.py`, `test_compilation_cache.py`, and `test_job_submission.py`. Use false only where a test intentionally models an instruction-incompatible projector.

- [ ] **Step 6: Run projector and integration regression suites**

```powershell
python -m pytest `
  tests/test_runtime_projectors.py `
  tests/test_integration_contract.py `
  tests/test_cache_locking.py `
  tests/test_compilation_cache.py `
  tests/test_job_submission.py `
  -q -m "not real_runtime"
```

Expected: PASS without external model calls.

- [ ] **Step 7: Commit Task 2**

```powershell
git add agency/projector_capabilities.py agency/blueprints/projectors.py agency/integrations tests/test_integration_contract.py tests/test_runtime_projectors.py tests/test_cache_locking.py tests/test_compilation_cache.py tests/test_job_submission.py
git commit -m "feat(projectors): declare instruction discovery"
```

---

### Task 3: Build Installed-Runtime Collection And Basic/Instruction Probes

**Files:**
- Create: `tests/_runtime_probe_helpers.py`
- Create: `tests/test_runtime_projectors_live.py`
- Modify: `tests/test_runtime_projectors.py`

**Interfaces:**
- Produces: `AI_CLI_COMMANDS: dict[str, str]` and `LIVE_SCENARIOS: tuple[str, ...]` in the shared test helper.
- Produces: `InstalledRuntime(name: str, command: str)`.
- Produces: `installed_ai_cli_runtimes(registry=REGISTRY) -> tuple[InstalledRuntime, ...]`.
- Produces: shared state capture and immutable-run assertions.
- Produces: one availability skip when no supported CLI is installed; otherwise basic and root-instruction live cases for every installed CLI.

- [ ] **Step 1: Write failing deterministic collection tests**

Create `tests/_runtime_probe_helpers.py` initially with only these contracts:

```python
AI_CLI_COMMANDS = {
    "copilot": "copilot",
    "claude-code": "claude",
    "gemini": "gemini",
    "codex": "codex",
    "aider": "aider",
    "goose": "goose",
    "opencode": "opencode",
    "pi": "pi",
}

LIVE_SCENARIOS = (
    "basic",
    "root-instructions",
    "selected-skill",
    "write-boundary",
)
```

Add to `tests/test_runtime_projectors.py`:

```python
def test_live_scenario_contract_covers_all_builtin_ai_clis():
    assert AI_CLI_COMMANDS == {
        "copilot": "copilot",
        "claude-code": "claude",
        "gemini": "gemini",
        "codex": "codex",
        "aider": "aider",
        "goose": "goose",
        "opencode": "opencode",
        "pi": "pi",
    }
    assert LIVE_SCENARIOS == (
        "basic",
        "root-instructions",
        "selected-skill",
        "write-boundary",
    )
    assert {
        name: REGISTRY[name].cli_command for name in AI_CLI_COMMANDS
    } == AI_CLI_COMMANDS


def test_installed_runtime_collection_omits_unavailable_clis(monkeypatch):
    monkeypatch.setattr(REGISTRY["copilot"], "resolve_executable", lambda: "C:/bin/copilot.exe")
    for name in AI_CLI_COMMANDS.keys() - {"copilot"}:
        monkeypatch.setattr(REGISTRY[name], "resolve_executable", lambda: None)

    assert installed_ai_cli_runtimes() == (
        InstalledRuntime("copilot", "C:/bin/copilot.exe"),
    )
```

Delete the old `LIVE_RUNTIME_PROBE_INTEGRATIONS`, `_real_runtime_probes_enabled`, `_available_real_executable`, opt-in marker test, and combined live test from `tests/test_runtime_projectors.py`.

- [ ] **Step 2: Run collection RED**

```powershell
python -m pytest tests/test_runtime_projectors.py -q -m "not real_runtime"
```

Expected: FAIL because `InstalledRuntime` and `installed_ai_cli_runtimes` do not exist.

- [ ] **Step 3: Implement shared installed-runtime and isolation helpers**

Complete `tests/_runtime_probe_helpers.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
from uuid import uuid4

from agency.fs.snapshot import SnapshotFile, TreeSnapshot, compute_source_digest
from agency.integrations import REGISTRY, RunResult
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    PathPolicyMode,
    ResolvedToolPolicy,
    ToolPolicyMode,
)


AI_CLI_COMMANDS = {
    "copilot": "copilot",
    "claude-code": "claude",
    "gemini": "gemini",
    "codex": "codex",
    "aider": "aider",
    "goose": "goose",
    "opencode": "opencode",
    "pi": "pi",
}

LIVE_SCENARIOS = (
    "basic",
    "root-instructions",
    "selected-skill",
    "write-boundary",
)


@dataclass(frozen=True)
class InstalledRuntime:
    name: str
    command: str


@dataclass(frozen=True)
class ProtectedState:
    projection: dict[PurePosixPath, bytes]
    workspace: dict[PurePosixPath, bytes]
    task: bytes
    repository: tuple[bytes, bytes]


def installed_ai_cli_runtimes(registry=REGISTRY) -> tuple[InstalledRuntime, ...]:
    installed = []
    for name in AI_CLI_COMMANDS:
        command = registry[name].resolve_executable()
        if command is not None:
            installed.append(InstalledRuntime(name, command))
    return tuple(installed)


def selected_skill_supported(integration) -> bool:
    capabilities = integration.projector.capabilities
    return (
        capabilities.discovers_skills
        and capabilities.activates_selected_skill
    )


def write_boundary_supported(integration) -> bool:
    capabilities = integration.runtime_capabilities
    return (
        "restricted" in capabilities.path_modes
        and "allowlist" in capabilities.tool_modes
    )


def unique_token(label: str) -> str:
    return f"AGENCY_{label}_{uuid4().hex.upper()}"


def assert_live_success(
    result: RunResult,
    runtime: InstalledRuntime,
    scenario: str,
    token: str,
) -> None:
    label = f"{runtime.name}/{scenario} ({runtime.command})"
    assert result.exit_code == 0, (
        f"{label}: exit={result.exit_code}; stderr={result.stderr!r}"
    )
    assert token in result.stdout, (
        f"{label}: missing {token!r}; stdout={result.stdout!r}; "
        f"stderr={result.stderr!r}"
    )
    assert result.changed_files == [], (
        f"{label}: unexpected changed files: {result.changed_files!r}"
    )


def tree_bytes(root: Path) -> dict[PurePosixPath, bytes]:
    return {
        PurePosixPath(*path.relative_to(root).parts): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def repository_state(root: Path) -> tuple[bytes, bytes]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return status, diff


def snapshot(instruction: str, skill: str = "Neutral skill instructions.") -> TreeSnapshot:
    files = (
        SnapshotFile(PurePosixPath("AGENTS.md"), instruction.encode()),
        SnapshotFile(
            PurePosixPath(".agents/skills/runtime-probe/SKILL.md"),
            (
                "---\nname: runtime-probe\n"
                "description: Use for the live runtime parity probe.\n---\n\n"
                f"{skill}\n"
            ).encode(),
        ),
    )
    return TreeSnapshot(files, compute_source_digest(files))


def request(
    workspace_root: Path,
    launch_dir: Path,
    task_file: Path,
    *,
    sandbox_mode: PathPolicyMode = "unrestricted",
    sandbox_roots: tuple[Path, ...] = (),
    tool_mode: ToolPolicyMode = "all",
    tool_names: tuple[str, ...] = (),
    skill: str | None = None,
) -> IntegrationRunRequest:
    return IntegrationRunRequest(
        workspace_root=workspace_root,
        launch_dir=launch_dir,
        task_file=task_file,
        timeout=180,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=180,
            sandbox_mode=sandbox_mode,
            sandbox_roots=sandbox_roots,
            tools=ResolvedToolPolicy(tool_mode, tool_names),
        ),
        skill=skill,
        skill_arguments=(),
        enforce_validation=True,
        memory_working_dir=None,
    )


def create_probe_directories(root: Path) -> tuple[Path, Path, Path]:
    launch_dir = root / "launch"
    workspace_root = root / "workspace"
    task_dir = root / "task"
    launch_dir.mkdir()
    workspace_root.mkdir()
    task_dir.mkdir()
    return launch_dir, workspace_root, task_dir


def capture_protected_state(
    launch_dir: Path,
    workspace_root: Path,
    task_file: Path,
    repository_root: Path,
) -> ProtectedState:
    return ProtectedState(
        projection=tree_bytes(launch_dir),
        workspace=tree_bytes(workspace_root),
        task=task_file.read_bytes(),
        repository=repository_state(repository_root),
    )


def assert_protected_state_unchanged(
    before: ProtectedState,
    launch_dir: Path,
    workspace_root: Path,
    task_file: Path,
    repository_root: Path,
    *,
    label: str,
) -> None:
    after = capture_protected_state(
        launch_dir,
        workspace_root,
        task_file,
        repository_root,
    )
    assert after.projection == before.projection, f"{label}: projection changed"
    assert after.workspace == before.workspace, f"{label}: workspace changed"
    assert after.task == before.task, f"{label}: task changed"
    assert after.repository == before.repository, f"{label}: repository changed"
```

- [ ] **Step 4: Create conditional live collection and basic/root tests**

Create `tests/test_runtime_projectors_live.py` with these imports and module
values:

```python
from pathlib import Path

import pytest

from agency.configuration import ValidationFailed
from agency.integrations import REGISTRY
from tests._runtime_probe_helpers import (
    AI_CLI_COMMANDS,
    assert_live_success,
    assert_protected_state_unchanged,
    capture_protected_state,
    create_probe_directories,
    installed_ai_cli_runtimes,
    request,
    selected_skill_supported,
    snapshot,
    unique_token,
    write_boundary_supported,
)


INSTALLED_RUNTIMES = installed_ai_cli_runtimes()
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
```

Use one module-level conditional. If the tuple is empty, define only:

```python
@pytest.mark.real_runtime
def test_no_supported_ai_cli_is_installed():
    pytest.skip(
        "No supported AI CLI is installed; expected one of: "
        + ", ".join(AI_CLI_COMMANDS.values())
    )
```

In the `else:` branch, define scenario functions parametrized with
`INSTALLED_RUNTIMES` and IDs from `runtime.name`. In Task 3 implement the first
two functions below. Task 4 adds the remaining two functions inside this same
branch, so a no-runtime machine continues to collect exactly one skip:

```python
@pytest.mark.real_runtime
@pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
def test_live_basic_execution(runtime, tmp_path):
    integration = REGISTRY[runtime.name]
    token = unique_token("BASIC")
    launch_dir = tmp_path / "launch"
    workspace_root = tmp_path / "workspace"
    task_dir = tmp_path / "task"
    launch_dir.mkdir()
    workspace_root.mkdir()
    task_dir.mkdir()
    source = snapshot("# Neutral runtime probe\n")
    integration.projector.project(source, launch_dir)
    assert integration.projector.validate_output(source, launch_dir) == ()
    task_file = task_dir / "basic.md"
    task_file.write_text(
        f"Reply with exactly {token}. Do not use tools or modify files.\n",
        encoding="utf-8",
    )
    before = capture_protected_state(
        launch_dir, workspace_root, task_file, REPOSITORY_ROOT
    )

    result = integration.run(request(workspace_root, launch_dir, task_file))

    assert_live_success(result, runtime, "basic", token)
    assert_protected_state_unchanged(
        before, launch_dir, workspace_root, task_file, REPOSITORY_ROOT,
        label=f"{runtime.name}/basic",
    )


@pytest.mark.real_runtime
@pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
def test_live_root_instructions(runtime, tmp_path):
    integration = REGISTRY[runtime.name]
    token = unique_token("INSTRUCTION")
    launch_dir = tmp_path / "launch"
    workspace_root = tmp_path / "workspace"
    task_dir = tmp_path / "task"
    launch_dir.mkdir()
    workspace_root.mkdir()
    task_dir.mkdir()
    source = snapshot(
        "# Runtime probe\n\nReply with this exact instruction token when asked: "
        f"{token}\n"
    )
    integration.projector.project(source, launch_dir)
    assert integration.projector.validate_output(source, launch_dir) == ()
    task_file = task_dir / "instructions.md"
    task_file.write_text(
        "Follow the projected root instructions and return their exact token. "
        "Do not use tools or modify files.\n",
        encoding="utf-8",
    )
    before = capture_protected_state(
        launch_dir, workspace_root, task_file, REPOSITORY_ROOT
    )

    result = integration.run(request(workspace_root, launch_dir, task_file))

    assert_live_success(result, runtime, "root-instructions", token)
    assert_protected_state_unchanged(
        before, launch_dir, workspace_root, task_file, REPOSITORY_ROOT,
        label=f"{runtime.name}/root-instructions",
    )
```

Wrap each live body in `try/finally` only if its integration creates resources outside `tmp_path`; normal pytest temporary-directory cleanup is sufficient otherwise.

- [ ] **Step 5: Verify installed-only collection**

```powershell
python -m pytest tests/test_runtime_projectors_live.py --collect-only -q
```

Expected on the current machine: exactly two Copilot live tests at this task boundary and no cases for unavailable CLIs.

- [ ] **Step 6: Run deterministic tests, then the two real Copilot probes once**

```powershell
python -m pytest tests/test_runtime_projectors.py -q -m "not real_runtime"
python -m pytest tests/test_runtime_projectors_live.py -v -m real_runtime
```

Expected: deterministic tests pass; current machine launches real `copilot.exe` for basic and root-instruction scenarios. Any authentication/network/runtime failure is a test failure, not a skip.

- [ ] **Step 7: Commit Task 3**

```powershell
git add tests/_runtime_probe_helpers.py tests/test_runtime_projectors.py tests/test_runtime_projectors_live.py
git commit -m "test(runtime): probe installed AI CLIs automatically"
```

---

### Task 4: Add Capability-Aware Selected-Skill And Write-Boundary Scenarios

**Files:**
- Modify: `tests/_runtime_probe_helpers.py`
- Modify: `tests/test_runtime_projectors.py`
- Modify: `tests/test_runtime_projectors_live.py`

**Interfaces:**
- Consumes: `InstalledRuntime`, shared snapshot/state/request helpers, and production capability declarations.
- Produces: four collected scenarios per installed CLI.
- Produces: exact fail-closed issue expectations before per-scenario command resolution for unsupported skill or policy.

- [ ] **Step 1: Add the failing deterministic capability-outcome test**

Use the capability helpers introduced by Task 3 and add to
`tests/test_runtime_projectors.py`:

Add this local guard to `tests/test_runtime_projectors_live.py` so unsupported
cases prove validation happens before task reading without changing the task:

```python
def guard_against_task_read(monkeypatch, task_file: Path) -> None:
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        if path == task_file:
            raise AssertionError("scenario read task before validation rejection")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
```

Add to `tests/test_runtime_projectors.py`:

```python
def test_all_builtin_ai_clis_have_four_capability_aware_scenarios():
    for name in AI_CLI_COMMANDS:
        integration = REGISTRY[name]
        assert integration.projector.capabilities.discovers_instructions is True
        assert isinstance(selected_skill_supported(integration), bool)
        assert isinstance(write_boundary_supported(integration), bool)
    assert LIVE_SCENARIOS == (
        "basic",
        "root-instructions",
        "selected-skill",
        "write-boundary",
    )
```

- [ ] **Step 2: Add selected-skill live/fail-closed scenario**

In the installed-runtime branch of `test_runtime_projectors_live.py`, add:

```python
@pytest.mark.real_runtime
@pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
def test_live_selected_skill(runtime, tmp_path, monkeypatch):
    integration = REGISTRY[runtime.name]
    token = unique_token("SKILL")
    launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
    source = snapshot(
        "# Neutral root instructions\n",
        skill=f"Return this exact selected-skill token: {token}",
    )
    integration.projector.project(source, launch_dir)
    task_file = task_dir / "selected-skill.md"
    task_file.write_text(
        "Use the explicitly selected runtime-probe skill and return its exact "
        "token. Do not use tools or modify files.\n",
        encoding="utf-8",
    )
    probe_request = request(
        workspace_root, launch_dir, task_file, skill="runtime-probe"
    )
    before = capture_protected_state(
        launch_dir, workspace_root, task_file, REPOSITORY_ROOT
    )

    if selected_skill_supported(integration):
        result = integration.run(probe_request)
        assert_live_success(result, runtime, "selected-skill", token)
    else:
        guard_against_task_read(monkeypatch, task_file)

        def unexpected_resolution():
            raise AssertionError("scenario re-resolved executable before rejection")

        monkeypatch.setattr(integration, "resolve_executable", unexpected_resolution)
        with pytest.raises(ValidationFailed) as excinfo:
            integration.run(probe_request)
        assert [issue.code for issue in excinfo.value.issues] == [
            "unsupported-skill-activation"
        ]

    assert_protected_state_unchanged(
        before, launch_dir, workspace_root, task_file, REPOSITORY_ROOT,
        label=f"{runtime.name}/selected-skill",
    )
```

- [ ] **Step 3: Add write-boundary live/fail-closed scenario**

Add:

```python
@pytest.mark.real_runtime
@pytest.mark.parametrize("runtime", INSTALLED_RUNTIMES, ids=lambda item: item.name)
def test_live_write_boundary(runtime, tmp_path, monkeypatch):
    integration = REGISTRY[runtime.name]
    token = unique_token("WRITE")
    launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
    source = snapshot("# Neutral write-boundary instructions\n")
    integration.projector.project(source, launch_dir)
    target = workspace_root / "write-probe.txt"
    task_file = task_dir / "write-boundary.md"
    task_file.write_text(
        f"Attempt to create {target} containing {token}. Regardless of whether "
        f"the write is denied, reply with exactly {token}.\n",
        encoding="utf-8",
    )
    probe_request = request(
        workspace_root,
        launch_dir,
        task_file,
        sandbox_mode="restricted",
        sandbox_roots=(workspace_root,),
        tool_mode="allowlist",
        tool_names=("read", "search"),
    )
    before = capture_protected_state(
        launch_dir, workspace_root, task_file, REPOSITORY_ROOT
    )

    if write_boundary_supported(integration):
        result = integration.run(probe_request)
        assert_live_success(result, runtime, "write-boundary", token)
    else:
        guard_against_task_read(monkeypatch, task_file)

        def unexpected_resolution():
            raise AssertionError("scenario re-resolved executable before rejection")

        monkeypatch.setattr(integration, "resolve_executable", unexpected_resolution)
        with pytest.raises(ValidationFailed) as excinfo:
            integration.run(probe_request)
        assert [issue.code for issue in excinfo.value.issues] == [
            "unsupported-path-policy",
            "unsupported-tool-policy",
        ]

    assert not target.exists()
    assert_protected_state_unchanged(
        before, launch_dir, workspace_root, task_file, REPOSITORY_ROOT,
        label=f"{runtime.name}/write-boundary",
    )
```

- [ ] **Step 4: Run collection and deterministic parity checks**

```powershell
python -m pytest tests/test_runtime_projectors_live.py --collect-only -q
python -m pytest tests/test_runtime_projectors.py -q -m "not real_runtime"
```

Expected on the current machine: exactly four Copilot live cases. Static tests still cover all eight CLI names and all four scenario definitions.

- [ ] **Step 5: Run all four real Copilot scenarios once**

```powershell
python -m pytest tests/test_runtime_projectors_live.py -v -m real_runtime
```

Expected: four passing Copilot cases. The selected-skill case returns the skill token; write-boundary returns its token and leaves `write-probe.txt` absent.

- [ ] **Step 6: Run deterministic integration/projector regression**

```powershell
python -m pytest `
  tests/test_runtime_projectors.py `
  tests/test_integration_contract.py `
  tests/test_integration_claude_code.py `
  tests/test_integration_sidecar.py `
  -q -m "not real_runtime"
```

Expected: PASS without additional model calls.

- [ ] **Step 7: Commit Task 4**

```powershell
git add tests/_runtime_probe_helpers.py tests/test_runtime_projectors.py tests/test_runtime_projectors_live.py
git commit -m "test(runtime): enforce CLI scenario parity"
```

---

### Task 5: Document Automatic Live Probes And Finalize Marker Semantics

**Files:**
- Modify: `pyproject.toml`
- Modify: `kb/integrations.md`
- Modify: `kb/contributing-integrations.md`
- Modify: `tests/test_runtime_projectors.py`

**Interfaces:**
- Consumes: automatic installed-only collection and the final four-scenario matrix.
- Produces: user-facing guidance for normal, live-only, and live-excluded test commands.

- [ ] **Step 1: Write failing documentation contract assertions**

Add to `tests/test_runtime_projectors.py`:

```python
def test_live_runtime_marker_and_docs_describe_automatic_installed_probes(
    pytestconfig,
):
    marker = next(
        value for value in pytestconfig.getini("markers")
        if value.split(":", 1)[0].strip() == "real_runtime"
    )
    integrations_doc = (
        Path(__file__).parents[1] / "kb" / "integrations.md"
    ).read_text(encoding="utf-8")
    contributing_doc = (
        Path(__file__).parents[1] / "kb" / "contributing-integrations.md"
    ).read_text(encoding="utf-8")

    assert "automatic" in marker.lower()
    assert "AGENCY_REAL_RUNTIME_PROBES" not in integrations_doc
    assert "AGENCY_REAL_RUNTIME_PROBES" not in contributing_doc
    for text in (integrations_doc, contributing_doc):
        assert "python -m pytest -m real_runtime -v" in text
        assert 'python -m pytest -m "not real_runtime" -q' in text
        assert "credentials" in text.lower()
        assert "network" in text.lower()
        assert "quota" in text.lower()
```

- [ ] **Step 2: Run documentation RED**

```powershell
python -m pytest tests/test_runtime_projectors.py::test_live_runtime_marker_and_docs_describe_automatic_installed_probes -q
```

Expected: FAIL because the marker and contributing guide still call probes opt-in.

- [ ] **Step 3: Update marker and integration documentation**

Change the marker in `pyproject.toml` to:

```toml
"real_runtime: automatic probes against installed external AI runtimes",
```

In `kb/integrations.md`, add an `## Runtime verification` section that states:

```markdown
Normal pytest runs automatically execute four live scenarios for each installed built-in AI CLI: basic execution, native root instructions, selected skill, and write boundary. Missing CLIs do not create per-scenario skips. Installed runtimes are expected to be authenticated and operational; authentication, network, quota, timeout, and runtime failures fail the suite.

```text
python -m pytest -m real_runtime -v
python -m pytest -m "not real_runtime" -q
```

Live scenarios can consume configured CLI credentials, network access, model quota, and time. Deterministic integration and projector contracts still cover all eight built-in AI CLIs when their executables are absent.
```

In `kb/contributing-integrations.md`, replace the opt-in checklist item with requirements to declare `cli_command`, truthful runtime policies, root-instruction discovery, selected-skill capabilities, and participation in the four static scenarios. Include the same two commands and credential/network/quota warning.

- [ ] **Step 4: Run deterministic docs and repository-boundary tests**

```powershell
python -m pytest `
  tests/test_runtime_projectors.py::test_live_runtime_marker_and_docs_describe_automatic_installed_probes `
  tests/test_repository_boundaries.py `
  -q -m "not real_runtime"
```

Expected: PASS with no model calls.

- [ ] **Step 5: Run final focused deterministic suite**

```powershell
python -m pytest `
  tests/test_runtime_projectors.py `
  tests/test_integration_contract.py `
  tests/test_integration_claude_code.py `
  tests/test_integration_sidecar.py `
  tests/test_compilation_cache.py `
  tests/test_cache_locking.py `
  tests/test_job_submission.py `
  -q -m "not real_runtime"
```

Expected: PASS without external model calls.

- [ ] **Step 6: Run the automatic live matrix and then the normal full suite**

```powershell
python -m pytest -m real_runtime -v
python -m pytest tests/ -q
```

Expected on the current machine: four real Copilot scenarios pass in the marker run; the normal full suite includes those same automatic cases. If the unrelated Windows job-memory race recurs, rerun its exact node once and report it separately rather than changing job execution in this feature.

- [ ] **Step 7: Check final diff and commit Task 5**

```powershell
git diff --check
git status --short
git add pyproject.toml kb/integrations.md kb/contributing-integrations.md tests/test_runtime_projectors.py
git commit -m "docs(runtime): explain automatic CLI probes"
```

---

## Final Verification

- [ ] Confirm exactly the installed live matrix is collected:

```powershell
python -m pytest tests/test_runtime_projectors_live.py --collect-only -q
```

Expected on a Copilot-only machine: four cases with IDs containing `copilot`; no Claude, Gemini, Codex, Aider, Goose, OpenCode, or Pi cases.

- [ ] Run deterministic coverage without external model calls:

```powershell
python -m pytest tests/ -q -m "not real_runtime"
```

Expected: all deterministic tests pass; only unrelated platform-specific skips remain.

- [ ] Run the installed external-runtime matrix:

```powershell
python -m pytest -m real_runtime -v
```

Expected: every collected installed CLI/scenario case passes; installed runtime failures are not skipped.

- [ ] Run the normal complete suite with automatic probes:

```powershell
python -m pytest tests/ -q
```

Expected: deterministic and automatic installed-runtime cases pass together.

- [ ] Verify branch cleanliness and scope:

```powershell
git diff --check master...HEAD
git status --short
git diff --stat master...HEAD
```

Expected: clean status; diff limited to the approved integration, projector, live-test, marker, and documentation surfaces plus the committed design and plan.