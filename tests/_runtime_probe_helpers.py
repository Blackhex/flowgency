from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
from uuid import uuid4

from agency.configuration import ValidationFailed
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
    return capabilities.discovers_skills and capabilities.activates_selected_skill


def write_boundary_supported(integration) -> bool:
    capabilities = integration.runtime_capabilities
    return (
        "restricted" in capabilities.path_modes
        and "allowlist" in capabilities.tool_modes
    )


def unique_token(label: str) -> str:
    return f"AGENCY_{label}_{uuid4().hex.upper()}"


def runtime_probe_label(runtime: InstalledRuntime, scenario: str) -> str:
    return f"{runtime.name}/{scenario} ({runtime.command})"


def assert_live_success(
    result: RunResult,
    runtime: InstalledRuntime,
    scenario: str,
    token: str,
) -> None:
    label = runtime_probe_label(runtime, scenario)
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


def assert_projection_valid(
    projector,
    source: TreeSnapshot,
    launch_dir: Path,
    runtime: InstalledRuntime,
    scenario: str,
) -> None:
    issues = projector.validate_output(source, launch_dir)
    label = runtime_probe_label(runtime, scenario)
    assert issues == (), f"{label}: projection validation failed: {ValidationFailed(issues)!r}; issues={issues!r}"


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


def snapshot(
    instruction: str,
    skill: str = "Neutral skill instructions.",
) -> TreeSnapshot:
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
    writable_roots: tuple[Path, ...] | None = None,
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
            writable_roots=sandbox_roots if writable_roots is None else writable_roots,
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
    runtime: InstalledRuntime,
    scenario: str,
) -> None:
    after = capture_protected_state(
        launch_dir,
        workspace_root,
        task_file,
        repository_root,
    )
    label = runtime_probe_label(runtime, scenario)
    assert after.projection == before.projection, f"{label}: projection changed"
    assert after.workspace == before.workspace, f"{label}: workspace changed"
    assert after.task == before.task, f"{label}: task changed"
    assert after.repository == before.repository, f"{label}: repository changed"