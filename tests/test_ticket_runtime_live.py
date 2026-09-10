"""Installed-runtime ticket acceptance against a real AI CLI.

Unlike ``tests/test_ticket_end_to_end.py`` (a deterministic harness whose
stand-in integration drives the broker in-process), this module launches the
actually installed assistant CLI and requires *it* to operate the live ticket
tools over its advertised transport. A green run is measured evidence that a
packaged CLI can inspect, claim, report on, and transition a ticket through the
real durable-job worker, broker, access registry, service, and provider.

The candidate set is ``ticket_capable_installed_runtimes()``: installed CLIs
intersected with the adapters that explicitly declare a live ticket transport
(currently Copilot). Nothing here overrides a capability flag to force support,
and authentication/quota/network/timeout failures remain failures, not skips.
Run with ``-m real_runtime``.

Scope note (measured on Copilot 1.0.84-3, worker-owned MCP HTTP transport): an
*enabled* Copilot sandbox classifies the worker's ``127.0.0.1`` MCP endpoint as
local network and, by default, drops it -- so a restricted ticket run cannot see
the ticket tools unless the agent instance carries the explicit, canonical
``integration_config.allow_local_network`` opt-in approved for this fixed
loopback endpoint. With that per-agent consent set (and nothing else in the
sandbox widened), the restricted read/search-only run reaches the endpoint and
operates the ticket tools; without it, the run fails a preflight gate before any
CLI launch. This supersedes the earlier stdio-era note that restricted ticket
runs were unattainable; see the task report.
"""
from __future__ import annotations

import base64
import subprocess
import sys
import hashlib
import json
import re
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from flowgency.integrations import REGISTRY
from flowgency.workflows.models import ArtifactRef
from tests._runtime_probe_helpers import (
    AI_CLI_COMMANDS,
    assert_sandbox_denied_write,
    load_job_session_events,
    supported_ticket_adapters,
    ticket_capable_installed_runtimes,
)
from tests._ticket_helpers import make_workflow_environment


TICKET_CAPABLE_RUNTIMES = ticket_capable_installed_runtimes()


# --------------------------------------------------------------------------- #
# Deterministic capability-selection guards (no CLI launched).
# --------------------------------------------------------------------------- #


def test_supported_ticket_adapters_are_exactly_the_declaring_ones():
    """Support is a declared adapter capability, not a probe of the binary."""
    supported = supported_ticket_adapters()
    for name in AI_CLI_COMMANDS:
        declared = REGISTRY[name].declared_runtime_capabilities.live_ticket_transport
        assert (name in supported) == (declared is not None), name
    # An adapter that declares no live transport must fail closed here: it is
    # never selected as ticket-capable, so it can never reach a file-outbox
    # fallback or a direct ticket-root write in place of the broker.
    assert "copilot" in supported
    assert "claude-code" not in supported


def test_ticket_capable_runtimes_are_installed_and_supported():
    installed_names = {
        name for name in AI_CLI_COMMANDS if REGISTRY[name].resolve_executable() is not None
    }
    supported = supported_ticket_adapters()
    for runtime in TICKET_CAPABLE_RUNTIMES:
        assert runtime.name in installed_names
        assert runtime.name in supported


def test_record_ticket_tool_calls_observes_live_dispatch(workflow_env):
    """Deterministic proof the live observer captures real broker dispatch.

    The live tests below assert on ``ticket_get`` even though the shared dispatch
    path names the operation ``get_ticket``; this guards that translation and the
    in-process wrap the live suite relies on, without launching any CLI.
    """
    from tests._runtime_probe_helpers import record_ticket_tool_calls

    env = workflow_env
    ticket = env.create(values={"summary": "hello"})
    env.service.assign(
        env.user, ticket.version, "builder", env.operation("assign")
    )
    ref = env.read(ticket.ref).ref
    authority = env.running_job("builder", "recorder-probe")

    with record_ticket_tool_calls() as calls:
        with env.broker_for(authority) as client:
            result = client.call("get_ticket", {"ref": ref.model_dump(mode="json")})

    assert result["ok"] is True
    ticket_get_calls = [call for call in calls if call["tool"] == "ticket_get"]
    assert ticket_get_calls, calls
    assert all(call["ok"] for call in ticket_get_calls)


def _denied_write_start_event(call_id: str, target: str) -> dict:
    """A real-shaped ``apply_patch`` start whose patch body names ``target``."""
    return {
        "type": "tool.execution_start",
        "data": {
            "toolCallId": call_id,
            "toolName": "apply_patch",
            "arguments": (
                "*** Begin Patch\n"
                f"*** Add File: {target}\n"
                "+blocked note\n"
                "*** End Patch\n"
            ),
        },
    }


def _completion_event(call_id: str, *, success: bool, sandbox_denied: bool | None) -> dict:
    properties = {"command": "apply_patch"}
    if sandbox_denied is not None:
        properties["sandbox_denied"] = "true" if sandbox_denied else "false"
        properties["sandboxed"] = "true"
    return {
        "type": "tool.execution_complete",
        "data": {
            "toolCallId": call_id,
            "success": success,
            "toolTelemetry": {"properties": properties},
        },
    }


def test_assert_sandbox_denied_write_accepts_only_a_correlated_policy_denial():
    """Deterministic proof the denial parser distinguishes a real policy refusal
    from look-alikes, using real-shaped Copilot events and no CLI launch.

    The stored ``write_attempts`` list alone cannot tell these apart: each case
    below emits the same canary ``tool.execution_start``, so only the correlated
    completion's ``success``/``sandbox_denied`` fields separate a genuine sandbox
    denial from a generic tool failure or an unrelated target.
    """
    target = "blocked-note.txt"

    # Explicit, correlated policy denial: accepted.
    denial = assert_sandbox_denied_write(
        [
            _denied_write_start_event("call-1", target),
            _completion_event("call-1", success=False, sandbox_denied=True),
        ],
        target_name=target,
    )
    assert denial.call_id == "call-1"
    assert denial.tool_name == "apply_patch"
    assert denial.target == target

    # A generic malformed-patch failure (no sandbox flag) is not a denial proof.
    with pytest.raises(AssertionError, match="not refused by the sandbox policy"):
        assert_sandbox_denied_write(
            [
                _denied_write_start_event("call-2", target),
                _completion_event("call-2", success=False, sandbox_denied=None),
            ],
            target_name=target,
        )

    # A sandbox denial of some *other* path does not prove the canary was denied.
    with pytest.raises(AssertionError, match="attempted to write"):
        assert_sandbox_denied_write(
            [
                _denied_write_start_event("call-3", "unrelated.txt"),
                _completion_event("call-3", success=False, sandbox_denied=True),
            ],
            target_name=target,
        )

    # A merely absent file (no write event at all) is not a denial proof.
    with pytest.raises(AssertionError, match="attempted to write"):
        assert_sandbox_denied_write([], target_name=target)


# --------------------------------------------------------------------------- #
# Live probe.
# --------------------------------------------------------------------------- #


def _hash_files(paths: dict[str, Path]) -> dict[str, str]:
    return {
        label: hashlib.sha256(path.read_bytes()).hexdigest()
        for label, path in paths.items()
    }


def _ticket_agent_config(raw_config: dict, runtime_name: str) -> dict:
    """A single-agent team bound to the installed ticket-capable runtime.

    The agent inherits the team's unrestricted, path-less policy, so Copilot's
    sandbox stays off and the worker-owned MCP HTTP endpoint is reachable without
    any network opt-in.
    """
    raw = deepcopy(raw_config)
    builder = raw["teams"]["newsletter"]["agents"][0]
    assert builder["name"] == "builder"
    builder["integration"] = runtime_name
    return raw


def _restricted_ticket_agent_config(
    raw_config: dict,
    runtime_name: str,
    *,
    allow_local_network: bool = True,
) -> dict:
    """A restricted read/search-only ticket agent.

    The restricted sandbox treats the worker's loopback MCP endpoint as local
    network, so a ticket run needs the explicit per-agent
    ``integration_config.allow_local_network`` consent to reach it. Consented
    cases set it here in the canonical agent config (not a test-side sandbox
    patch); the preflight-denial case passes ``allow_local_network=False`` to
    prove the default-off gate fails before any CLI launch.
    """
    raw = _ticket_agent_config(raw_config, runtime_name)
    builder = raw["teams"]["newsletter"]["agents"][0]
    workspace = Path(raw["teams"]["newsletter"]["workspace_path"])
    raw["teams"]["newsletter"]["permissions"] = {
        "mode": "restricted",
        "rules": [{"path": str(workspace), "tools": ["read", "search"]}],
    }
    if allow_local_network:
        builder["integration_config"] = {"allow_local_network": True}
    return raw


def _run_tiny_project_check(workspace: Path) -> bytes:
    test_file = workspace / "test_presatisfied_result.py"
    test_file.write_text(
        "def test_existing_result_is_valid():\n"
        "    assert 6 * 7 == 42\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    # These are genuine pytest report bytes, but pytest pads the progress line
    # with a terminal-width-dependent run of spaces before ``[100%]``. That long
    # whitespace run is exactly what a model cannot faithfully base64-reproduce,
    # so the artifact round-trip would flake on the padding rather than on the
    # result. Collapsing internal space runs keeps the real tokens (the dots,
    # ``[100%]``, and the ``N passed in Xs`` summary) while making the bytes
    # short enough for the agent to publish back exactly.
    normalized = re.sub(r"[ \t]{2,}", " ", completed.stdout)
    return normalized.encode("utf-8")


def _agent_task(ref_json: str, project_result: Path) -> str:
    return (
        "Operate a single Flowgency workflow ticket using the live ticket tools "
        'exposed by the "flowgency-tickets" MCP server. Do every step, in order.\n\n'
        f"The project's completed result file is {project_result} and it already "
        "contains a correct, passing test result. Read that file first and use its "
        "contents as your evidence. It is already correct: never modify, overwrite, "
        "delete, or recreate it or any other project file.\n\n"
        "Ticket reference (JSON):\n"
        f"{ref_json}\n\n"
        "Steps:\n"
        "1. Call the ticket_get tool with that exact ref to load the ticket and its "
        'current `version`. Confirm it is assigned to you ("builder") and note the '
        'transition named "Complete" (id "complete") and its criterion '
        '(id "evidence-reviewed").\n'
        "2. Call ticket_get again to obtain the freshest `version`, then call "
        'ticket_start_work with that version and operation_id "start-1".\n'
        "3. Read the result file bytes exactly as they are on disk, base64-encode "
        "them, call ticket_get again to obtain the current `version`, then call "
        'ticket_artifact_publish with that version, filename "result.txt", '
        'media_type "text/plain", and content_b64 set to the exact base64 payload. '
        "Keep the returned artifact reference for the transition output.\n"
        "4. Call ticket_get again to obtain the current `version`, then call "
        'ticket_report with that version, operation_id "report-1", a message that '
        "summarizes the passing result you read from the result file, and exactly one "
        'assessment: criterion_id "evidence-reviewed", satisfied true, with reasoning '
        "that cites the passing result.\n"
        "5. Call ticket_get again to obtain the current `version`, then call "
        'ticket_transition with that version, operation_id "complete-1", transition_id '
        '"complete", inputs {"verdict": true}, outputs {"summary": "<one short line '
        'summarizing the verified result>", "evidence": "<the retained artifact '
        'reference from ticket_artifact_publish>"}, and assessments with one entry: '
        'criterion_id "evidence-reviewed", satisfied true, reasoning "<short>".\n'
        "6. Confirm the transition response `ok` is true, then stop and reply with the "
        "single word DONE.\n\n"
        "Always pass the exact `version` object returned by your most recent ticket_get "
        "call to the next mutating call. Never invent a revision number or a digest. "
        "Do not create, edit, or delete any file on disk at any point."
    )


def _read_only_agent_task(ref_json: str) -> str:
    return (
        "Operate a single Flowgency workflow ticket using only the read-only "
        'ticket_get tool exposed by the "flowgency-tickets" MCP server. Do not '
        "create, edit, or delete any file on disk, and do not call any other ticket "
        "tool or any shell, git, or write tool at any point.\n\n"
        "Ticket reference (JSON):\n"
        f"{ref_json}\n\n"
        "Steps:\n"
        "1. Call the ticket_get tool with that exact ref to load the ticket and its "
        "current `version`.\n"
        "2. Confirm the response `ok` is true, then stop and reply with the single "
        "word DONE.\n\n"
        "Do not attempt any workspace write and do not call any mutating ticket tool."
    )


def _restricted_agent_task(ref_json: str, project_result: Path, blocked_note: Path) -> str:
    return (
        "Operate a single Flowgency workflow ticket using the live ticket tools "
        'exposed by the "flowgency-tickets" MCP server. Do every step, in order.\n\n'
        f"The project result file is {project_result}. Read it first. The workspace "
        f"path {blocked_note} is intentionally read-only for writes in this run: try "
        "once to create that file with a short note, then continue after the refusal. "
        "Do not try any other workspace write.\n\n"
        "Ticket reference (JSON):\n"
        f"{ref_json}\n\n"
        "Steps:\n"
        "1. Call ticket_get with that exact ref to load the ticket and its current `version`.\n"
        "2. Call ticket_get again, then call ticket_start_work with operation_id \"start-1\".\n"
        "3. Read the result file bytes exactly as they are on disk, base64-encode them, call "
        "ticket_get again, then call ticket_artifact_publish with filename \"result.txt\", "
        "media_type \"text/plain\", and the exact content_b64 payload. Keep the returned "
        "artifact reference.\n"
        f"4. Attempt exactly one workspace write by creating {blocked_note} with a short note. "
        "If the write is refused, continue normally and do not retry.\n"
        "5. Call ticket_get again, then call ticket_report with operation_id \"report-1\", a "
        "short message summarizing the passing result and the denied write attempt, and one "
        "satisfied evidence-reviewed assessment.\n"
        "6. Call ticket_get again, then call ticket_transition with operation_id \"complete-1\", "
        "transition_id \"complete\", inputs {\"verdict\": true}, outputs containing both summary "
        "and the retained evidence artifact reference, and one satisfied evidence-reviewed "
        "assessment.\n"
        "7. Confirm the transition response `ok` is true, then stop and reply with the single word DONE.\n\n"
        "Always pass the exact `version` object returned by your most recent ticket_get call to the "
        "next mutating call. Never invent a revision number or a digest. Never use shell, git, or "
        "any tool outside the ticket tools and ordinary file reads."
    )


def _stale_refresh_and_sign_off_task(ref_a_json: str, ref_b_json: str) -> str:
    return (
        "Operate two Flowgency workflow tickets using the live ticket tools exposed by "
        'the "flowgency-tickets" MCP server. Do every step, in order. Do not create, '
        "edit, or delete any file on disk at any point.\n\n"
        "Ticket A must prove stale refresh during completion. Ticket B must prove sign-off.\n\n"
        f"Ticket A reference (JSON):\n{ref_a_json}\n\n"
        f"Ticket B reference (JSON):\n{ref_b_json}\n\n"
        "Steps:\n"
        "1. Call ticket_get on ticket A, then call ticket_start_work on A with the returned version and operation_id \"start-a-1\".\n"
        "2. Call ticket_get on ticket A again and save that exact version object as STALE_VERSION. Then call ticket_transition on A with STALE_VERSION, operation_id \"complete-stale\", transition_id \"complete\", inputs {\"verdict\": true}, outputs {\"summary\": \"completed after refresh\"}, and exactly one assessment: {\"criterion_id\": \"evidence-reviewed\", \"satisfied\": true, \"reasoning\": \"Reviewed the current ticket state after refresh.\", \"supporting_fields\": [\"summary\"]}.\n"
        "3. Inspect the tool result from step 2. If it returns ok false with error.code \"stale-ticket\", immediately call ticket_get on ticket A again, discard STALE_VERSION, copy the entire newly returned version object as FRESH_VERSION, and retry ticket_transition exactly once with operation_id \"complete-fresh\" using FRESH_VERSION and the same inputs, outputs, and assessment. Do not reuse any earlier version object. If step 2 unexpectedly succeeds, do not retry.\n"
        "4. Call ticket_get on ticket B, then call ticket_start_work on B with the returned version and operation_id \"start-b-1\".\n"
        "5. Call ticket_get on ticket B again, then call ticket_sign_off on B with the returned version and operation_id \"signoff-b-1\".\n"
        "6. Reply with the single word DONE.\n\n"
        "Always pass the exact version object returned by your most recent ticket_get for a ticket to the next mutating call on that same ticket. Never invent a revision number or a digest."
    )


def _transition_then_timeout_task(ref_json: str, artifact_b64: str) -> str:
    return (
        "Operate a single Flowgency workflow ticket using the live ticket tools exposed by "
        'the "flowgency-tickets" MCP server. Do every step, in order. Do not create, '
        "edit, or delete any file on disk at any point.\n\n"
        "Use the exact retained artifact payload provided below; do not modify or re-encode it except to pass it through unchanged to ticket_artifact_publish.\n\n"
        "Ticket reference (JSON):\n"
        f"{ref_json}\n\n"
        "Artifact content_b64:\n"
        f"{artifact_b64}\n\n"
        "Steps:\n"
        "1. Call ticket_get with that exact ref, then call ticket_start_work with the returned version and operation_id \"start-timeout-1\".\n"
        "2. Call ticket_get again, then call ticket_artifact_publish with that fresh version, filename \"result.txt\", media_type \"text/plain\", and the exact content_b64 payload shown above. Keep the returned artifact reference.\n"
        "3. Call ticket_get again, then call ticket_transition with that fresh version, operation_id \"complete-timeout\", transition_id \"complete\", inputs {\"verdict\": true}, outputs {\"summary\": \"Committed before timeout\", \"evidence\": "
        "<the retained artifact reference from ticket_artifact_publish>}, and exactly one satisfied assessment: {\"criterion_id\": \"evidence-reviewed\", \"satisfied\": true, \"reasoning\": \"The retained result proves the ticket is complete.\", \"supporting_fields\": [\"summary\", \"evidence\"]}.\n"
        "4. After the transition response returns, stop and reply with the single word DONE.\n\n"
        "Always pass the exact version object returned by your most recent ticket_get call to the next mutating call. Never invent a revision number or a digest."
    )


if not TICKET_CAPABLE_RUNTIMES:

    @pytest.mark.real_runtime
    def test_no_ticket_capable_runtime_is_installed():
        pytest.skip(
            "No ticket-capable AI CLI is installed; expected one of: "
            + ", ".join(sorted(supported_ticket_adapters()))
        )

else:

    @pytest.mark.parametrize(
        "runtime",
        tuple(runtime for runtime in TICKET_CAPABLE_RUNTIMES if runtime.name == "copilot"),
        ids=lambda item: item.name,
    )
    def test_restricted_ticket_run_requires_explicit_local_network_opt_in(
        runtime, tmp_path, raw_config
    ):
        """The preflight gate: a restricted Copilot ticket run whose agent has no
        ``allow_local_network`` consent is rejected at job resolution, before any
        job record is created or any CLI is launched. This is the default-off half
        of the approved opt-in and needs no live subprocess -- the sandbox-confining
        preflight rejects the submission deterministically.
        """
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.configuration.issues import ValidationFailed
        from flowgency.jobs.models import JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.prompts import PromptStore

        raw = _restricted_ticket_agent_config(
            raw_config, runtime.name, allow_local_network=False
        )
        env = make_workflow_environment(tmp_path, raw)
        env.publish_criteria_workflow()

        workspace = Path(env.store.load().config.teams[env.team_id].workspace_path)
        project_result = workspace / "result.txt"
        project_result.write_bytes(_run_tiny_project_check(workspace))

        ticket = env.create(values={"summary": "initial"})
        env.service.assign(
            env.user,
            ticket.version,
            "builder",
            env.operation("assign", actor_name="local-user"),
        )
        ref = env.read(ticket.ref).ref

        snapshot = env.store.load()
        task = _read_only_agent_task(json.dumps(ref.model_dump(mode="json")))

        with pytest.raises(ValidationFailed) as excinfo:
            resolve_job_request(
                JobRequest(
                    config_path=env.store.path,
                    team_key=env.team_id,
                    agent_name="builder",
                    trigger="manual_prompt",
                    routine_id=None,
                    task_input=task,
                    timeout_override=300,
                ),
                config_store=env.store,
                library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
                cache=CompilationCache(
                    snapshot.config.flowgency.compilation_cache,
                    {runtime.name: REGISTRY[runtime.name].projector},
                ),
                prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
                integrations={runtime.name: REGISTRY[runtime.name]},
            )

        codes = {issue.code for issue in excinfo.value.issues}
        assert "ticket-local-network-required" in codes, (
            f"{runtime.name}: preflight did not raise the local-network gate: {codes}"
        )
        assert "local-network consent" in str(excinfo.value)
        # No job ran and the ticket is untouched: no start, no active run, still review.
        final = env.read(ref).record
        assert final.active_run is None
        assert final.state_id == "review"

    @pytest.mark.real_runtime
    @pytest.mark.parametrize(
        "runtime",
        tuple(runtime for runtime in TICKET_CAPABLE_RUNTIMES if runtime.name == "copilot"),
        ids=lambda item: item.name,
    )
    def test_restricted_agent_reads_ticket_over_http_without_editing(
        runtime, tmp_path, raw_config
    ):
        """The first live acceptance gate: a restricted read/search-only Copilot
        run with explicit ``allow_local_network`` consent reaches the worker-owned
        MCP HTTP endpoint and calls ``ticket_get`` successfully, with no stdio
        child, no widening of the sandbox beyond the single approved local-network
        grant, and no change to the project on disk.
        """
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.jobs.execution import execute_job
        from flowgency.jobs.models import JobRecord, JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.jobs.store import read_job
        from flowgency.prompts import PromptStore
        from tests._runtime_probe_helpers import record_ticket_tool_calls

        raw = _restricted_ticket_agent_config(raw_config, runtime.name)
        env = make_workflow_environment(tmp_path, raw)
        env.publish_criteria_workflow()

        workspace = Path(env.store.load().config.teams[env.team_id].workspace_path)
        project_result = workspace / "result.txt"
        project_result.write_bytes(_run_tiny_project_check(workspace))
        project_before = project_result.read_bytes()

        ticket = env.create(values={"summary": "initial"})
        env.service.assign(
            env.user,
            ticket.version,
            "builder",
            env.operation("assign", actor_name="local-user"),
        )
        ref = env.read(ticket.ref).ref

        snapshot = env.store.load()
        protected = {
            "config": env.store.path,
            "blueprint": snapshot.config.flowgency.agent_library
            / "builder-blueprint"
            / "AGENTS.md",
            "workflow": env.library.root / env.blueprint_id / "workflow.yaml",
            "project": project_result,
        }
        hashes_before = _hash_files(protected)

        task = _read_only_agent_task(json.dumps(ref.model_dump(mode="json")))

        spec = resolve_job_request(
            JobRequest(
                config_path=env.store.path,
                team_key=env.team_id,
                agent_name="builder",
                trigger="manual_prompt",
                routine_id=None,
                task_input=task,
                timeout_override=300,
            ),
            config_store=env.store,
            library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
            cache=CompilationCache(
                snapshot.config.flowgency.compilation_cache,
                {runtime.name: REGISTRY[runtime.name].projector},
            ),
            prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
            integrations={runtime.name: REGISTRY[runtime.name]},
        )
        authority = env.job_store.create(JobRecord.from_spec(spec))

        with record_ticket_tool_calls() as calls:
            record = execute_job(authority)

        job = read_job(authority.path)
        assert record.status == "complete", (
            f"{runtime.name}: restricted read-only probe did not complete: "
            f"status={record.status!r}; summary={job.execution_summary!r}; "
            f"stderr_path={job.stderr_path!r}; stdout_path={job.stdout_path!r}"
        )
        assert record.exit_code == 0, (
            f"{runtime.name}: non-zero exit {record.exit_code!r}; "
            f"summary={job.execution_summary!r}"
        )
        ticket_get_calls = [call for call in calls if call["tool"] == "ticket_get"]
        assert ticket_get_calls, (
            f"{runtime.name}: ticket_get never reached the broker over MCP HTTP; "
            f"observed={[call['tool'] for call in calls]}; stdout_path={job.stdout_path!r}"
        )
        assert all(call["ok"] for call in ticket_get_calls), (
            f"{runtime.name}: a ticket_get call failed at the broker: {ticket_get_calls}"
        )
        # A read-only run leaves the ticket and every protected input untouched.
        final = env.read(ref).record
        assert final.active_run is None
        assert project_result.read_bytes() == project_before
        assert _hash_files(protected) == hashes_before

    @pytest.mark.real_runtime
    @pytest.mark.parametrize(
        "runtime",
        tuple(runtime for runtime in TICKET_CAPABLE_RUNTIMES if runtime.name == "copilot"),
        ids=lambda item: item.name,
    )
    def test_restricted_agent_refreshes_stale_transition_and_signs_off_second_ticket(
        runtime, tmp_path, raw_config, monkeypatch
    ):
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.jobs.execution import execute_job
        from flowgency.jobs.models import JobRecord, JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.jobs.store import read_job
        from flowgency.prompts import PromptStore
        from tests._runtime_probe_helpers import record_ticket_tool_calls

        raw = _restricted_ticket_agent_config(raw_config, runtime.name)
        env = make_workflow_environment(tmp_path, raw)
        env.publish_criteria_workflow()

        ticket_a = env.create(title="Stale refresh live", values={"summary": "initial"})
        env.service.assign(
            env.user,
            ticket_a.version,
            "builder",
            env.operation("assign-a", actor_name="local-user"),
        )
        ref_a = env.read(ticket_a.ref).ref

        ticket_b = env.create(title="Sign-off live", values={"summary": "follow-up"})
        env.service.assign(
            env.user,
            ticket_b.version,
            "builder",
            env.operation("assign-b", actor_name="local-user"),
        )
        ref_b = env.read(ticket_b.ref).ref

        snapshot = env.store.load()
        task = _stale_refresh_and_sign_off_task(
            json.dumps(ref_a.model_dump(mode="json")),
            json.dumps(ref_b.model_dump(mode="json")),
        )
        injected = {"stale": False}

        def before_dispatch(service, registry, token, operation, payload):
            if (
                operation == "transition_ticket"
                and isinstance(payload, dict)
                and payload.get("operation_id") == "complete-stale"
                and not injected["stale"]
            ):
                injected["stale"] = True
                current = env.read(ref_a)
                env.service.update(
                    env.user,
                    current.version,
                    current.patch(description="Concurrent user refresh bump."),
                    env.operation("remote-bump", actor_name="local-user"),
                )

        spec = resolve_job_request(
            JobRequest(
                config_path=env.store.path,
                team_key=env.team_id,
                agent_name="builder",
                trigger="manual_prompt",
                routine_id=None,
                task_input=task,
                timeout_override=300,
            ),
            config_store=env.store,
            library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
            cache=CompilationCache(
                snapshot.config.flowgency.compilation_cache,
                {runtime.name: REGISTRY[runtime.name].projector},
            ),
            prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
            integrations={runtime.name: REGISTRY[runtime.name]},
        )
        authority = env.job_store.create(JobRecord.from_spec(spec))

        with record_ticket_tool_calls(before_dispatch=before_dispatch) as calls:
            record = execute_job(authority)
        job = read_job(authority.path)
        assert record.status == "complete", (
            f"{runtime.name}: stale-refresh/sign-off run did not complete: "
            f"status={record.status!r}; summary={job.execution_summary!r}; "
            f"stderr_path={job.stderr_path!r}; stdout_path={job.stdout_path!r}"
        )
        assert injected["stale"], f"{runtime.name}: stale transition was never forced"

        stale_calls = [
            call
            for call in calls
            if call["tool"] == "ticket_transition"
            and call.get("operation_id") == "complete-stale"
        ]
        assert stale_calls, f"{runtime.name}: no stale transition attempt was observed"
        assert stale_calls[-1]["ok"] is False, stale_calls
        assert stale_calls[-1]["error_code"] == "stale-ticket", stale_calls

        fresh_calls = [
            call
            for call in calls
            if call["tool"] == "ticket_transition"
            and call.get("operation_id") == "complete-fresh"
        ]
        assert fresh_calls, f"{runtime.name}: no fresh transition retry was observed"
        assert fresh_calls[-1]["ok"] is True, fresh_calls

        signoff_calls = [
            call
            for call in calls
            if call["tool"] == "ticket_sign_off"
            and call.get("operation_id") == "signoff-b-1"
        ]
        assert signoff_calls, f"{runtime.name}: no ticket_sign_off call was observed"
        assert signoff_calls[-1]["ok"] is True, signoff_calls

        final_a = env.read(ref_a).record
        assert final_a.state_id == "done"
        assert final_a.assignee == "builder"
        assert final_a.active_run is None

        final_b = env.read(ref_b).record
        assert final_b.assignee is None
        assert final_b.active_run is None
        assert any(event.kind == "signed-off" for event in final_b.events)

    @pytest.mark.real_runtime
    @pytest.mark.parametrize(
        "runtime",
        tuple(runtime for runtime in TICKET_CAPABLE_RUNTIMES if runtime.name == "copilot"),
        ids=lambda item: item.name,
    )
    def test_restricted_agent_timeout_after_committed_transition_keeps_ticket_state(
        runtime, tmp_path, raw_config, monkeypatch
    ):
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.integrations.flowgency import copilot as copilot_mod
        from flowgency.jobs.execution import execute_job
        from flowgency.jobs.models import JobRecord, JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.jobs.store import read_job
        from flowgency.prompts import PromptStore
        from tests._runtime_probe_helpers import record_ticket_tool_calls

        timeout_seconds = 120
        raw = _restricted_ticket_agent_config(raw_config, runtime.name)
        env = make_workflow_environment(tmp_path, raw)
        env.publish_artifact_field_workflow()
        env.publish_criteria_workflow()

        workspace = Path(env.store.load().config.teams[env.team_id].workspace_path)
        project_result = workspace / "result.txt"
        project_result.write_bytes(_run_tiny_project_check(workspace))
        project_before = project_result.read_bytes()

        ticket = env.create(title="Commit before timeout", values={"summary": "initial", "evidence": None})
        env.service.assign(
            env.user,
            ticket.version,
            "builder",
            env.operation("assign-timeout", actor_name="local-user"),
        )
        ref = env.read(ticket.ref).ref

        snapshot = env.store.load()
        task = _transition_then_timeout_task(
            json.dumps(ref.model_dump(mode="json")),
            base64.b64encode(project_before).decode("ascii"),
        )

        committed = threading.Event()
        release_response = threading.Event()
        timeout_observed = threading.Event()
        outcome: dict[str, object] = {}

        def after_dispatch(service, registry, token, operation, payload, result):
            if (
                operation == "transition_ticket"
                and isinstance(payload, dict)
                and payload.get("operation_id") == "complete-timeout"
            ):
                committed.set()
                assert release_response.wait(timeout=timeout_seconds + 60), (
                    "transition response gate was never released"
                )

        original_run_supervised = copilot_mod.run_supervised

        def observing_run_supervised(*args, **kwargs):
            completed = original_run_supervised(*args, **kwargs)
            if completed.exit_code == 124:
                timeout_observed.set()
            return completed

        monkeypatch.setattr(copilot_mod, "run_supervised", observing_run_supervised)

        spec = resolve_job_request(
            JobRequest(
                config_path=env.store.path,
                team_key=env.team_id,
                agent_name="builder",
                trigger="manual_prompt",
                routine_id=None,
                task_input=task,
                timeout_override=timeout_seconds,
            ),
            config_store=env.store,
            library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
            cache=CompilationCache(
                snapshot.config.flowgency.compilation_cache,
                {runtime.name: REGISTRY[runtime.name].projector},
            ),
            prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
            integrations={runtime.name: REGISTRY[runtime.name]},
        )
        authority = env.job_store.create(JobRecord.from_spec(spec))

        def run_job() -> None:
            try:
                with record_ticket_tool_calls(after_dispatch=after_dispatch) as calls:
                    outcome["record"] = execute_job(authority)
                    outcome["calls"] = list(calls)
            except BaseException as error:
                outcome["error"] = error

        worker = threading.Thread(target=run_job, daemon=True)
        worker.start()
        try:
            assert committed.wait(timeout=timeout_seconds), (
                f"{runtime.name}: the committed transition was never observed"
            )
            assert timeout_observed.wait(timeout=timeout_seconds + 60), (
                f"{runtime.name}: run_supervised never reported a real timeout"
            )
        finally:
            release_response.set()

        worker.join(timeout=30)
        assert not worker.is_alive(), f"{runtime.name}: execute_job remained blocked after gate release"
        if "error" in outcome:
            raise outcome["error"]

        record = outcome["record"]
        calls = outcome["calls"]
        job = read_job(authority.path)
        assert record.status == "failed", (
            f"{runtime.name}: timeout-after-commit run did not fail as expected: "
            f"status={record.status!r}; summary={job.execution_summary!r}; "
            f"stderr_path={job.stderr_path!r}; stdout_path={job.stdout_path!r}"
        )
        assert record.exit_code == 124
        assert record.execution_summary == f"Agent timed out after {timeout_seconds} seconds."

        transition_calls = [
            call
            for call in calls
            if call["tool"] == "ticket_transition"
            and call.get("operation_id") == "complete-timeout"
        ]
        assert transition_calls, f"{runtime.name}: committed transition call was not observed"
        assert transition_calls[-1]["ok"] is True, transition_calls

        final = env.read(ref).record
        assert final.state_id == "done"
        assert final.assignee == "builder"
        assert final.active_run is None
        assert final.field_values["summary"] == "Committed before timeout"
        evidence = final.field_values["evidence"]
        retained = env.current_provider().read_artifact(ref, evidence.value)
        assert retained.content == project_before

        cleanup = job.result_metadata["ticket_cleanup"]
        assert cleanup["status"] == "cleared"
        assert cleanup["confirmed"] is True
        assert cleanup["cleared"] == [ref.model_dump(mode="json")]

    @pytest.mark.real_runtime
    @pytest.mark.parametrize(
        "runtime", TICKET_CAPABLE_RUNTIMES, ids=lambda item: item.name
    )
    def test_agent_verifies_presatisfied_project_without_rewriting_it(
        runtime, tmp_path, raw_config
    ):
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.jobs.execution import execute_job
        from flowgency.jobs.models import JobRecord, JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.jobs.store import read_job
        from flowgency.prompts import PromptStore

        raw = _ticket_agent_config(raw_config, runtime.name)
        env = make_workflow_environment(tmp_path, raw)

        env.publish_artifact_field_workflow()
        env.publish_criteria_workflow()

        workspace = Path(env.store.load().config.teams[env.team_id].workspace_path)
        project_result = workspace / "result.txt"
        project_result.write_bytes(_run_tiny_project_check(workspace))
        project_before = project_result.read_bytes()

        ticket = env.create(values={"summary": "initial"})
        env.service.assign(
            env.user,
            ticket.version,
            "builder",
            env.operation("assign", actor_name="local-user"),
        )
        ref = env.read(ticket.ref).ref

        snapshot = env.store.load()
        protected = {
            "config": env.store.path,
            "blueprint": snapshot.config.flowgency.agent_library
            / "builder-blueprint"
            / "AGENTS.md",
            "workflow": env.library.root / env.blueprint_id / "workflow.yaml",
            "project": project_result,
        }
        hashes_before = _hash_files(protected)

        task = _agent_task(json.dumps(ref.model_dump(mode="json")), project_result)

        spec = resolve_job_request(
            JobRequest(
                config_path=env.store.path,
                team_key=env.team_id,
                agent_name="builder",
                trigger="manual_prompt",
                routine_id=None,
                task_input=task,
                timeout_override=300,
            ),
            config_store=env.store,
            library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
            cache=CompilationCache(
                snapshot.config.flowgency.compilation_cache,
                {runtime.name: REGISTRY[runtime.name].projector},
            ),
            prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
            integrations={runtime.name: REGISTRY[runtime.name]},
        )
        authority = env.job_store.create(JobRecord.from_spec(spec))

        record = execute_job(authority)

        job = read_job(authority.path)
        assert record.status == "complete", (
            f"{runtime.name}: job did not complete: status={record.status!r}; "
            f"summary={job.execution_summary!r}; stderr_path={job.stderr_path!r}"
        )

        final = env.read(ref).record
        assert final.state_id == "done", (
            f"{runtime.name}: ticket not transitioned; state={final.state_id!r}; "
            f"events={[event.kind for event in final.events]}; "
            f"stdout_path={job.stdout_path!r}"
        )
        assert final.assignee == "builder"
        assert final.active_run is None
        assert final.field_values.get("summary"), final.field_values
        evidence = final.field_values["evidence"]
        assert evidence == ArtifactRef(kind="id", value=evidence.value)
        retained = env.current_provider().read_artifact(ref, evidence.value)
        assert retained.content == project_before

        reported = [event for event in final.events if event.kind == "reported"]
        assert reported and reported[-1].summary.strip(), (
            f"{runtime.name}: no retained report event; "
            f"events={[event.kind for event in final.events]}"
        )
        assert any(event.kind == "transitioned" for event in final.events)

        # The pre-satisfied project and every protected input are untouched: the
        # agent verified existing work instead of repeating it.
        assert project_result.read_bytes() == project_before
        assert _hash_files(protected) == hashes_before


    @pytest.mark.real_runtime
    @pytest.mark.parametrize(
        "runtime",
        tuple(runtime for runtime in TICKET_CAPABLE_RUNTIMES if runtime.name == "copilot"),
        ids=lambda item: item.name,
    )
    def test_agent_in_restricted_workspace_keeps_ticket_flow_and_records_denied_write(
        runtime, tmp_path, raw_config
    ):
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.jobs.execution import execute_job
        from flowgency.jobs.models import JobRecord, JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.jobs.store import read_job
        from flowgency.prompts import PromptStore

        raw = _restricted_ticket_agent_config(raw_config, runtime.name)
        env = make_workflow_environment(tmp_path, raw)
        env.publish_artifact_field_workflow()
        env.publish_criteria_workflow()

        workspace = Path(env.store.load().config.teams[env.team_id].workspace_path)
        project_result = workspace / "result.txt"
        project_result.write_bytes(_run_tiny_project_check(workspace))
        project_before = project_result.read_bytes()
        blocked_note = workspace / "blocked-note.txt"

        ticket = env.create(values={"summary": "initial", "evidence": None})
        env.service.assign(
            env.user,
            ticket.version,
            "builder",
            env.operation("assign", actor_name="local-user"),
        )
        ref = env.read(ticket.ref).ref

        snapshot = env.store.load()
        protected = {
            "config": env.store.path,
            "blueprint": snapshot.config.flowgency.agent_library
            / "builder-blueprint"
            / "AGENTS.md",
            "workflow": env.library.root / env.blueprint_id / "workflow.yaml",
            "project": project_result,
        }
        hashes_before = _hash_files(protected)

        task = _restricted_agent_task(
            json.dumps(ref.model_dump(mode="json")),
            project_result,
            blocked_note,
        )

        spec = resolve_job_request(
            JobRequest(
                config_path=env.store.path,
                team_key=env.team_id,
                agent_name="builder",
                trigger="manual_prompt",
                routine_id=None,
                task_input=task,
                timeout_override=300,
            ),
            config_store=env.store,
            library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
            cache=CompilationCache(
                snapshot.config.flowgency.compilation_cache,
                {runtime.name: REGISTRY[runtime.name].projector},
            ),
            prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
            integrations={runtime.name: REGISTRY[runtime.name]},
        )
        authority = env.job_store.create(JobRecord.from_spec(spec))

        record = execute_job(authority)
        job = read_job(authority.path)
        assert record.status == "complete", (
            f"{runtime.name}: restricted ticket flow did not complete: "
            f"status={record.status!r}; summary={job.execution_summary!r}; "
            f"stderr_path={job.stderr_path!r}; stdout_path={job.stdout_path!r}"
        )

        final = env.read(ref).record
        assert final.state_id == "done"
        assert final.assignee == "builder"
        assert final.active_run is None
        evidence = final.field_values["evidence"]
        assert evidence == ArtifactRef(kind="id", value=evidence.value)
        retained = env.current_provider().read_artifact(ref, evidence.value)
        assert retained.content == project_before
        assert read_job(authority.path).result_metadata["write_attempts"] == [blocked_note.name]
        assert not blocked_note.exists()
        # The stored write_attempts and the absent file only prove the agent
        # *tried* to write; correlate the canary write's own session events to
        # prove the sandbox *policy* refused it (success false + sandbox_denied),
        # not a malformed patch or an unrelated call. This is cooperative
        # in-process containment of a built-in edit, not an OS write guarantee.
        denial = assert_sandbox_denied_write(
            load_job_session_events(job), target_name=blocked_note.name
        )
        assert denial.target == blocked_note.name
        assert project_result.read_bytes() == project_before
        assert _hash_files(protected) == hashes_before


def _multi_ticket_task(ref_a_json: str) -> str:
    return (
        "Operate Flowgency workflow tickets using the live ticket tools exposed by "
        'the "flowgency-tickets" MCP server. Do every step, in order. Do not create, '
        "edit, or delete any file on disk at any point.\n\n"
        f'Workflow id: "board-a"\n'
        f"Ticket A reference (JSON):\n{ref_a_json}\n\n"
        "Steps:\n"
        "1. Call ticket_get on ticket A to load it and its current `version`.\n"
        "2. Call ticket_get on ticket A, then ticket_start_work on A with that "
        'version and operation_id "start-1".\n'
        "3. Call ticket_get on ticket A, then ticket_update on A with that version, "
        'operation_id "update-1", and field_values {"summary": "updated by agent"}.\n'
        '4. Create a second ticket ("ticket B") in workflow "board-a": call '
        'ticket_create with workflow_id "board-a", title "Follow-up verification", '
        'description "Track the follow-up review.", operation_id "create-1", and '
        'field_values {"summary": "draft summary"}. Keep the ticket reference and '
        "`version` that ticket_create returns for ticket B.\n"
        "5. Operate ticket B, not just create it: call ticket_start_work on B with "
        'B\'s version from ticket_create and operation_id "start-b-1".\n'
        "6. Call ticket_get on ticket B to obtain B's fresh `version`, then call "
        'ticket_update on B with that version, operation_id "update-b-1", and '
        'field_values {"summary": "operated by agent"}.\n'
        "7. Call ticket_get on ticket A, then ticket_report on A with that version, "
        'operation_id "report-1", a short message, and one assessment: criterion_id '
        '"evidence-reviewed", satisfied true, reasoning "<short>".\n'
        "8. Call ticket_get on ticket A, then ticket_transition on A with that "
        'version, operation_id "complete-1", transition_id "complete", inputs '
        '{"verdict": true}, outputs {"summary": "A verified"}, and assessments with '
        'one entry: criterion_id "evidence-reviewed", satisfied true, reasoning '
        '"<short>".\n'
        "9. Reply with the single word DONE.\n\n"
        "Always pass the exact `version` object returned by your most recent "
        "ticket_get for a ticket to the next mutating call on that same ticket. "
        "Never invent a revision number or a digest."
    )


if TICKET_CAPABLE_RUNTIMES:

    @pytest.mark.real_runtime
    @pytest.mark.parametrize(
        "runtime", TICKET_CAPABLE_RUNTIMES, ids=lambda item: item.name
    )
    def test_one_run_creates_updates_and_transitions_multiple_tickets(
        runtime, tmp_path, raw_config
    ):
        from flowgency.blueprints import CompilationCache
        from flowgency.blueprints.library import BlueprintLibrary
        from flowgency.jobs.execution import execute_job
        from flowgency.jobs.models import JobRecord, JobRequest
        from flowgency.jobs.resolution import resolve_job_request
        from flowgency.jobs.store import read_job
        from flowgency.prompts import PromptStore
        from tests._runtime_probe_helpers import record_ticket_tool_calls

        raw = _ticket_agent_config(raw_config, runtime.name)
        env = make_workflow_environment(tmp_path, raw)
        env.publish_criteria_workflow()

        ticket_a = env.create(title="Primary review", values={"summary": "initial"})
        env.service.assign(
            env.user,
            ticket_a.version,
            "builder",
            env.operation("assign-a", actor_name="local-user"),
        )
        ref_a = env.read(ticket_a.ref).ref

        snapshot = env.store.load()
        task = _multi_ticket_task(json.dumps(ref_a.model_dump(mode="json")))

        spec = resolve_job_request(
            JobRequest(
                config_path=env.store.path,
                team_key=env.team_id,
                agent_name="builder",
                trigger="manual_prompt",
                routine_id=None,
                task_input=task,
                timeout_override=300,
            ),
            config_store=env.store,
            library=BlueprintLibrary(snapshot.config.flowgency.agent_library),
            cache=CompilationCache(
                snapshot.config.flowgency.compilation_cache,
                {runtime.name: REGISTRY[runtime.name].projector},
            ),
            prompt_store=PromptStore(snapshot.config.flowgency.prompt_store),
            integrations={runtime.name: REGISTRY[runtime.name]},
        )
        authority = env.job_store.create(JobRecord.from_spec(spec))

        with record_ticket_tool_calls() as calls:
            record = execute_job(authority)
        job = read_job(authority.path)
        assert record.status == "complete", (
            f"{runtime.name}: job did not complete: status={record.status!r}; "
            f"summary={job.execution_summary!r}"
        )

        # The dispatch observer captured the live tool calls in the order the run
        # made them. Creation and update are observable strictly before the run's
        # terminal transition -- i.e. before completion, not reconstructed after.
        tools_in_order = [call["tool"] for call in calls if call["ok"]]
        assert "ticket_create" in tools_in_order, tools_in_order
        assert "ticket_update" in tools_in_order, tools_in_order
        assert "ticket_transition" in tools_in_order, tools_in_order
        last_transition = len(tools_in_order) - 1 - tools_in_order[::-1].index(
            "ticket_transition"
        )
        assert tools_in_order.index("ticket_create") < last_transition, tools_in_order
        assert tools_in_order.index("ticket_update") < last_transition, tools_in_order

        # Ticket A: the run started, updated (while owned), reported, and
        # transitioned it to done.
        final_a = env.read(ref_a).record
        assert final_a.state_id == "done", (
            f"{runtime.name}: A not transitioned; state={final_a.state_id!r}; "
            f"events={[event.kind for event in final_a.events]}; "
            f"stdout_path={job.stdout_path!r}"
        )
        assert any(event.kind == "updated" for event in final_a.events), (
            f"{runtime.name}: no live update event on A; "
            f"events={[event.kind for event in final_a.events]}"
        )
        assert any(event.kind == "reported" for event in final_a.events)
        assert any(event.kind == "transitioned" for event in final_a.events)

        # A second ticket was created AND operated in the same run: it was
        # started and updated live, not merely created.
        others = [
            view
            for view in env.service.list_tickets(env.user, "board-a")
            if view.ref.ticket_id != ref_a.ticket_id
        ]
        assert len(others) == 1, (
            f"{runtime.name}: expected exactly one created ticket; "
            f"found {[view.record.title for view in others]}"
        )
        created = others[0].record
        assert created.title == "Follow-up verification"
        assert created.assignee == "builder", (
            f"{runtime.name}: ticket B was not claimed by the operating agent; "
            f"assignee={created.assignee!r}"
        )
        created_events = [event.kind for event in created.events]
        assert "started-work" in created_events, (
            f"{runtime.name}: ticket B was created but never started; "
            f"events={created_events}"
        )
        assert "updated" in created_events, (
            f"{runtime.name}: ticket B was created but never updated; "
            f"events={created_events}"
        )
        assert created.field_values.get("summary") == "operated by agent", (
            f"{runtime.name}: ticket B update did not land; "
            f"summary={created.field_values.get('summary')!r}"
        )

