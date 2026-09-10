from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
from uuid import uuid4

from flowgency.configuration import ValidationFailed
from flowgency.fs.snapshot import SnapshotFile, TreeSnapshot, compute_source_digest
from flowgency.integrations import REGISTRY, RunResult
from flowgency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
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


def supported_ticket_adapters(registry=REGISTRY) -> frozenset[str]:
    """Adapters that explicitly declare a live ticket transport.

    This is a deterministic declaration Task 8 makes on the adapter class, not a
    probe of the installed binary: an adapter that names a `live_ticket_transport`
    in its declared capabilities is a candidate. A successful live run is what
    turns a declared candidate into measured availability -- capability selection
    never recursively depends on this suite passing, and no flag is overridden
    to force support here.
    """
    return frozenset(
        name
        for name in AI_CLI_COMMANDS
        if registry[name].declared_runtime_capabilities.live_ticket_transport is not None
    )


def ticket_capable_installed_runtimes(registry=REGISTRY) -> tuple[InstalledRuntime, ...]:
    """Installed runtimes intersected with the explicitly supported adapters."""
    supported = supported_ticket_adapters(registry)
    return tuple(
        runtime
        for runtime in installed_ai_cli_runtimes(registry)
        if runtime.name in supported
    )


def selected_skill_supported(integration) -> bool:
    capabilities = integration.projector.capabilities
    return capabilities.discovers_skills and capabilities.activates_selected_skill


def write_boundary_supported(integration) -> bool:
    capabilities = integration.runtime_capabilities
    return (
        "restricted" in capabilities.permission_modes
        and bool(capabilities.path_scopable_tools)
    )


def unique_token(label: str) -> str:
    return f"FLOWGENCY_{label}_{uuid4().hex.upper()}"


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
    mode: str = "unrestricted",
    roots: tuple[Path, ...] = (),
    tools: tuple[str, ...] | None = None,
    skill: str | None = None,
) -> IntegrationRunRequest:
    rules = tuple(
        ResolvedPermissionRule(path=root, tools=tools)
        for root in roots
    )
    return IntegrationRunRequest(
        workspace_root=workspace_root,
        launch_dir=launch_dir,
        task_file=task_file,
        timeout=180,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=180,
            mode=mode,
            rules=rules,
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


# --------------------------------------------------------------------------- #
# Live MCP tool-call observation.
# --------------------------------------------------------------------------- #

# Map the fixed live catalog's tool names to the internal service operations they
# dispatch (see ``flowgency/tickets/mcp_server.py``). A recorded ``operation`` is
# translated back to the caller-visible ``tool`` name so live assertions can name
# ``ticket_get`` even though the shared dispatch path uses ``get_ticket``.
TICKET_TOOL_FOR_OPERATION = {
    "list_workflows": "workflows_list",
    "list_tickets": "tickets_list",
    "get_ticket": "ticket_get",
    "create_ticket": "ticket_create",
    "start_work": "ticket_start_work",
    "update_ticket": "ticket_update",
    "report_ticket": "ticket_report",
    "transition_ticket": "ticket_transition",
    "end_work": "ticket_end_work",
    "sign_off": "ticket_sign_off",
    "publish_artifact": "ticket_artifact_publish",
}


@contextmanager
def record_ticket_tool_calls():
    """Observe every ticket tool the live run dispatches through the broker.

    The worker-owned broker runs in-process (only the assistant CLI is a child),
    so wrapping the shared ``_dispatch_authenticated_request`` boundary captures
    the real, authenticated tool calls the installed CLI makes over MCP HTTP —
    both the ``/mcp`` tools path and the raw ``/operations`` path resolve that
    name late, so this sees every structured tool result with its success flag.
    A raised ``TicketStorageError`` (auth/validation/forbidden) is recorded as an
    unsuccessful call rather than a missing one, so revoke and stale-version
    denials remain observable.
    """
    from flowgency.tickets import broker as broker_mod

    calls: list[dict] = []
    lock = threading.Lock()
    original = broker_mod._dispatch_authenticated_request

    def recording(service, registry, token, operation, payload):
        try:
            result = original(service, registry, token, operation, payload)
        except Exception:
            with lock:
                calls.append(
                    {
                        "operation": operation,
                        "tool": TICKET_TOOL_FOR_OPERATION.get(operation, operation),
                        "ok": False,
                    }
                )
            raise
        with lock:
            calls.append(
                {
                    "operation": operation,
                    "tool": TICKET_TOOL_FOR_OPERATION.get(operation, operation),
                    "ok": True,
                }
            )
        return result

    broker_mod._dispatch_authenticated_request = recording
    try:
        yield calls
    finally:
        broker_mod._dispatch_authenticated_request = original