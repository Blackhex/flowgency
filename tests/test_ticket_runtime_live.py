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

Scope note (measured on Copilot 1.0.84-3): an *enabled* Copilot sandbox refuses
to spawn the stdio ticket MCP server -- its PowerShell launch of the interpreter
fails drive initialization ("The file cannot be accessed by the system"), so no
ticket tools register. A restricted read/search-only Copilot ticket run is
therefore blocked by the installed CLI itself; see the task report. This live
guarantee is established with the sandbox disabled, where "without rewriting" is
a measured *choice* by the agent rather than an inability.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from flowgency.integrations import REGISTRY
from tests._runtime_probe_helpers import (
    AI_CLI_COMMANDS,
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


# --------------------------------------------------------------------------- #
# Live probe.
# --------------------------------------------------------------------------- #


PROJECT_RESULT = (
    "test session: 42 checks executed, 42 passed, 0 failed.\n"
    "coverage: 100% of the delivery contract.\n"
)


def _hash_files(paths: dict[str, Path]) -> dict[str, str]:
    return {
        label: hashlib.sha256(path.read_bytes()).hexdigest()
        for label, path in paths.items()
    }


def _ticket_agent_config(raw_config: dict, runtime_name: str) -> dict:
    """A single-agent team bound to the installed ticket-capable runtime.

    The agent inherits the team's unrestricted, path-less policy, so Copilot's
    sandbox stays off -- the only configuration under which the installed CLI
    will spawn the stdio ticket MCP server (see the module docstring).
    """
    raw = deepcopy(raw_config)
    builder = raw["teams"]["newsletter"]["agents"][0]
    assert builder["name"] == "builder"
    builder["integration"] = runtime_name
    return raw


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
        "3. Call ticket_get again to obtain the current `version`, then call "
        'ticket_report with that version, operation_id "report-1", a message that '
        "summarizes the passing result you read from the result file, and exactly one "
        'assessment: criterion_id "evidence-reviewed", satisfied true, with reasoning '
        "that cites the passing result.\n"
        "4. Call ticket_get again to obtain the current `version`, then call "
        'ticket_transition with that version, operation_id "complete-1", transition_id '
        '"complete", inputs {"verdict": true}, outputs {"summary": "<one short line '
        'summarizing the verified result>"}, and assessments with one entry: '
        'criterion_id "evidence-reviewed", satisfied true, reasoning "<short>".\n'
        "5. Confirm the transition response `ok` is true, then stop and reply with the "
        "single word DONE.\n\n"
        "Always pass the exact `version` object returned by your most recent ticket_get "
        "call to the next mutating call. Never invent a revision number or a digest. "
        "Do not create, edit, or delete any file on disk at any point."
    )


if not TICKET_CAPABLE_RUNTIMES:

    @pytest.mark.real_runtime
    def test_no_ticket_capable_runtime_is_installed():
        pytest.skip(
            "No ticket-capable AI CLI is installed; expected one of: "
            + ", ".join(sorted(supported_ticket_adapters()))
        )

else:

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

        # The completion contract needs a positive criterion assessment.
        env.publish_criteria_workflow()

        workspace = Path(env.store.load().config.teams[env.team_id].workspace_path)
        project_result = workspace / "result.txt"
        project_result.write_text(PROJECT_RESULT, encoding="utf-8")
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
        '4. Create a second ticket in workflow "board-a": call ticket_create with '
        'workflow_id "board-a", title "Follow-up verification", description '
        '"Track the follow-up review.", operation_id "create-1", and field_values '
        '{"summary": "draft summary"}.\n'
        "5. Call ticket_get on ticket A, then ticket_report on A with that version, "
        'operation_id "report-1", a short message, and one assessment: criterion_id '
        '"evidence-reviewed", satisfied true, reasoning "<short>".\n'
        "6. Call ticket_get on ticket A, then ticket_transition on A with that "
        'version, operation_id "complete-1", transition_id "complete", inputs '
        '{"verdict": true}, outputs {"summary": "A verified"}, and assessments with '
        'one entry: criterion_id "evidence-reviewed", satisfied true, reasoning '
        '"<short>".\n'
        "7. Reply with the single word DONE.\n\n"
        "Always pass the exact `version` object returned by your most recent "
        "ticket_get for ticket A to the next mutating call on it. Never invent a "
        "revision number or a digest."
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

        record = execute_job(authority)
        job = read_job(authority.path)
        assert record.status == "complete", (
            f"{runtime.name}: job did not complete: status={record.status!r}; "
            f"summary={job.execution_summary!r}"
        )

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

        # A second ticket was created in the same run, observable before the run
        # completed: one run handled multiple tickets live.
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
        assert created.state_id == "review"

