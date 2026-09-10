from __future__ import annotations

import json
import re
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


def _observed_ticket_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    ref = payload.get("ref")
    if isinstance(ref, dict):
        ticket_id = ref.get("ticket_id")
        if isinstance(ticket_id, str):
            return ticket_id
    version = payload.get("version")
    if isinstance(version, dict):
        ref = version.get("ref")
        if isinstance(ref, dict):
            ticket_id = ref.get("ticket_id")
            if isinstance(ticket_id, str):
                return ticket_id
    return None


@contextmanager
def record_ticket_tool_calls(*, before_dispatch=None, after_dispatch=None):
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
        if before_dispatch is not None:
            before_dispatch(service, registry, token, operation, payload)
        try:
            result = original(service, registry, token, operation, payload)
        except Exception as error:
            error_code = None
            error_message = None
            if hasattr(error, "as_dict"):
                details = error.as_dict()
                if isinstance(details, dict):
                    code = details.get("code")
                    if isinstance(code, str):
                        error_code = code
                    message = details.get("message")
                    if isinstance(message, str):
                        error_message = message
            with lock:
                calls.append(
                    {
                        "operation": operation,
                        "tool": TICKET_TOOL_FOR_OPERATION.get(operation, operation),
                        "ok": False,
                        "error_code": error_code,
                        "error_message": error_message,
                        "operation_id": payload.get("operation_id") if isinstance(payload, dict) else None,
                        "ticket_id": _observed_ticket_id(payload),
                    }
                )
            raise
        if after_dispatch is not None:
            after_dispatch(service, registry, token, operation, payload, result)
        ok = True
        error = None
        if isinstance(result, dict) and "ok" in result:
            ok = bool(result.get("ok"))
            error = result.get("error")
        with lock:
            calls.append(
                {
                    "operation": operation,
                    "tool": TICKET_TOOL_FOR_OPERATION.get(operation, operation),
                    "ok": ok,
                    "error_code": error.get("code") if isinstance(error, dict) else None,
                    "error_message": error.get("message") if isinstance(error, dict) else None,
                    "operation_id": payload.get("operation_id") if isinstance(payload, dict) else None,
                    "ticket_id": _observed_ticket_id(payload),
                }
            )
        return result

    broker_mod._dispatch_authenticated_request = recording
    try:
        yield calls
    finally:
        broker_mod._dispatch_authenticated_request = original


# --------------------------------------------------------------------------- #
# Sandbox write-denial proof from the job's own Copilot session events.
# --------------------------------------------------------------------------- #
#
# The stored ``write_attempts`` list is captured at ``tool.execution_start`` and
# so only proves the agent *tried* to write a path -- a malformed patch that the
# tool itself rejects produces the same observable, and an absent output file is
# equally consistent with a write that simply never ran. Proving the *sandbox
# policy* refused the write needs the correlated completion of that same call:
# ``success`` false together with an explicit ``sandbox_denied`` policy flag.
#
# This is cooperative, in-process containment of a built-in file edit, not an
# operating-system file-write guarantee; the metadata proves the policy denied
# the edit, not that the kernel would have.

_SANDBOX_DENIED_KEYS = ("sandbox_denied", "sandboxDenied")

_APPLY_PATCH_HEADERS = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
)


@dataclass(frozen=True)
class SandboxDenial:
    call_id: str
    tool_name: str
    target: str


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path.strip())[-1]


def _truthy_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _apply_patch_paths(arguments: object) -> list[str]:
    """Target paths named inside an ``apply_patch`` envelope string."""
    if not isinstance(arguments, str):
        return []
    paths: list[str] = []
    for line in arguments.splitlines():
        for prefix in _APPLY_PATCH_HEADERS:
            if line.startswith(prefix):
                target = line[len(prefix):].strip()
                if target:
                    paths.append(target)
                break
    return paths


def _write_targets_for_start(data: dict) -> list[str]:
    """Paths a ``tool.execution_start`` event attempts to write, or ``[]``."""
    tool_name = data.get("toolName")
    arguments = data.get("arguments")
    if tool_name == "apply_patch":
        return _apply_patch_paths(arguments)
    obj: dict | None = arguments if isinstance(arguments, dict) else None
    if obj is None and isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (ValueError, TypeError):
            parsed = None
        obj = parsed if isinstance(parsed, dict) else None
    if obj is None:
        return []
    path = obj.get("path")
    return [path] if isinstance(path, str) and path else []


def _has_sandbox_denied(data: dict) -> bool:
    telemetry = data.get("toolTelemetry")
    if isinstance(telemetry, dict):
        properties = telemetry.get("properties")
        if isinstance(properties, dict):
            for key in _SANDBOX_DENIED_KEYS:
                if _truthy_flag(properties.get(key)):
                    return True
    for key in _SANDBOX_DENIED_KEYS:
        if _truthy_flag(data.get(key)):
            return True
    return False


def iter_jsonl_events(text: str):
    """Yield structured event dicts from a Copilot JSONL stream.

    Malformed or non-object lines are skipped rather than raising, so a partial
    or truncated session log still yields every well-formed event.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            yield event


def load_job_session_events(job) -> list[dict]:
    """Structured Copilot session events the completed job persisted itself.

    Reads the job-owned run stream (``stdout_path``) and, when present, the
    per-job ``copilot_home`` session log. Both are structured JSONL owned by the
    job. The raw events carry the task payload, so callers must correlate and
    assert on scalar fields rather than print whole event objects.
    """
    texts: list[str] = []
    stdout_path = getattr(job, "stdout_path", None)
    if stdout_path:
        path = Path(stdout_path)
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    home = getattr(job, "copilot_home", None)
    session_id = getattr(job, "session_id", None)
    if home and session_id:
        events_path = Path(home) / "session-state" / str(session_id) / "events.jsonl"
        if events_path.is_file():
            texts.append(events_path.read_text(encoding="utf-8"))
    events: list[dict] = []
    for text in texts:
        events.extend(iter_jsonl_events(text))
    return events


def assert_sandbox_denied_write(events, *, target_name: str) -> SandboxDenial:
    """Prove the sandbox policy refused a write to ``target_name``.

    Correlates the write's ``tool.execution_start`` (call id and named target)
    with its ``tool.execution_complete`` and requires that completion to carry
    ``success`` false *and* an explicit ``sandbox_denied`` policy flag on the
    same call. A generic tool failure with no policy flag, a denial of some
    other path, or a merely absent output file are each rejected -- none of them
    proves the policy, rather than the tool or chance, blocked the write.

    Error messages name only scalar fields (call id, tool name, target, the
    success and denied flags); the raw arguments and full event objects, which
    carry the task payload, are never printed.
    """
    starts: dict[str, tuple[object, list[str]]] = {}
    completes: dict[str, dict] = {}
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        call_id = data.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            continue
        event_type = event.get("type")
        if event_type == "tool.execution_start":
            targets = _write_targets_for_start(data)
            if targets:
                starts[call_id] = (data.get("toolName"), targets)
        elif event_type == "tool.execution_complete":
            completes[call_id] = data

    matching = [
        (call_id, tool_name)
        for call_id, (tool_name, targets) in starts.items()
        if any(_basename(target) == target_name for target in targets)
    ]
    observed_targets = sorted(
        {_basename(target) for _, targets in starts.values() for target in targets}
    )
    assert matching, (
        f"no tool.execution_start attempted to write {target_name!r}; "
        f"observed write targets: {observed_targets}"
    )

    denials: list[SandboxDenial] = []
    diagnostics: list[tuple[str, object, bool]] = []
    for call_id, tool_name in matching:
        complete = completes.get(call_id)
        if complete is None:
            diagnostics.append((call_id, "no-completion", False))
            continue
        success = complete.get("success")
        denied = _has_sandbox_denied(complete)
        diagnostics.append((call_id, success, denied))
        if success is False and denied:
            denials.append(SandboxDenial(call_id, str(tool_name), target_name))

    assert denials, (
        f"the write to {target_name!r} was not refused by the sandbox policy; "
        f"correlated (call_id, success, sandbox_denied) completions: {diagnostics}; "
        f"a generic tool failure or a missing policy flag is not a denial proof"
    )
    return denials[0]