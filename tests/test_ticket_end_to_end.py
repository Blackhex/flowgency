"""Deterministic cross-layer ticket orchestration coverage.

This module exercises the whole ticket stack — the registered configuration and
workflow library, the local provider, the live in-process ticket broker with its
access registry, and the durable-job execution lifecycle — without any installed
assistant CLI. A stand-in integration drives the broker tools the same way a real
runtime would, so the flow is real end to end while remaining hermetic.

It deliberately does NOT establish installed-runtime capability. It manufactures
no green "runtime supported" signal from mocks: the fake integration is a test
harness, not evidence that any packaged CLI can operate the broker. That
measured guarantee is the job of the upcoming ``tests/test_ticket_runtime_live.py``
suite (``real_runtime``), which probes actually installed adapters.
"""
from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowgency.integrations import RunResult
from flowgency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest
from flowgency.tickets.errors import TicketForbidden
from tests._ticket_helpers import (
    TicketRuntimeIntegration,
    make_ticket_job_environment,
)


REPORT_BYTES = b"Automated test report: 42 checks, all green.\n"


def _verification_workflow(env) -> None:
    """Require a retained artifact output plus a positive criterion assessment."""
    env.publish_artifact_field_workflow()
    env.publish_criteria_workflow()


def _assigned_review_ticket(env):
    ticket = env.create(
        values={"verdict": True, "summary": "Initial summary", "evidence": None}
    )
    env.service.assign(
        env.user,
        ticket.version,
        "builder",
        env.operation("assign", actor_name="local-user"),
    )
    return env.read(ticket.ref)


def test_agent_job_verifies_presatisfied_project_without_rewriting_it(
    tmp_path, raw_config, monkeypatch
):
    from flowgency.blueprints import CompilationCache
    from flowgency.blueprints.library import BlueprintLibrary
    from flowgency.jobs.execution import execute_job
    from flowgency.jobs.models import JobRecord, JobRequest
    from flowgency.jobs.processes import ProcessStopEvidence
    from flowgency.jobs.resolution import resolve_job_request
    from flowgency.jobs.store import read_job
    from flowgency.prompts import PromptStore
    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    _verification_workflow(env)
    ticket = _assigned_review_ticket(env)

    project_result = Path(env.store.load().config.teams[env.team_id].workspace_path) / "result.txt"
    project_result.write_text("already-correct output\n", encoding="utf-8")
    before = project_result.read_bytes()

    class VerifyingIntegration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            assert request.ticket_tools is not None
            client = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
            )

            looked_up = client.call(
                "get_ticket", {"ref": ticket.ref.model_dump(mode="json")}
            )
            assert looked_up["ok"] is True, looked_up

            started = client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-e2e",
                },
            )
            assert started["ok"] is True, started

            published = client.call(
                "publish_artifact",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "filename": "report.txt",
                    "media_type": "text/plain",
                    "content_b64": base64.b64encode(REPORT_BYTES).decode("ascii"),
                },
            )
            assert published["ok"] is True, published
            evidence_ref = published["result"]

            transitioned = client.call(
                "transition_ticket",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "transition_id": "complete",
                    "inputs": {"verdict": True},
                    "outputs": {
                        "summary": "Verified existing work",
                        "evidence": evidence_ref,
                    },
                    "assessments": [
                        {
                            "criterion_id": "evidence-reviewed",
                            "satisfied": True,
                            "reasoning": "The retained report already satisfies the rule.",
                            "supporting_fields": ["summary", "evidence"],
                        }
                    ],
                    "operation_id": "complete-e2e",
                },
            )
            assert transitioned["ok"] is True, transitioned

            # The external project is already correct; the agent must not rewrite it.
            assert project_result.read_bytes() == before

            return RunResult(
                0,
                "verified",
                "",
                0.1,
                changed_files=[],
                session_id="deterministic-e2e-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=request.ticket_tools.lifecycle.generation,
                    confirmed=True,
                    reason="exited",
                ),
            )

    spec = resolve_job_request(
        JobRequest(
            config_path=env.store.path,
            team_key=env.team_id,
            agent_name="builder",
            trigger="manual_prompt",
            routine_id=None,
            task_input="Verify the existing project against the ticket.",
        ),
        config_store=env.store,
        library=BlueprintLibrary(env.store.load().config.flowgency.agent_library),
        cache=CompilationCache(
            env.store.load().config.flowgency.compilation_cache,
            {"claude-code": VerifyingIntegration.projector},
        ),
        prompt_store=PromptStore(env.store.load().config.flowgency.prompt_store),
        integrations={"claude-code": VerifyingIntegration()},
    )
    authority = env.job_store.create(JobRecord.from_spec(spec))
    record = env.job_store.read(authority)

    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=Path(spec.workspace_root),
            integration=VerifyingIntegration(),
            timeout=30,
            sandbox_root=None,
            team_root=Path(spec.team_root),
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )

    result = execute_job(authority)

    assert result.status == "complete", read_job(authority.path).execution_summary

    final = env.read(ticket.ref).record
    assert final.state_id == "done"
    assert final.assignee == "builder"
    assert final.active_run is None
    assert final.field_values["summary"] == "Verified existing work"

    evidence = final.field_values["evidence"]
    retained = env.provider.read_artifact(ticket.ref, evidence.value)
    assert retained.content == REPORT_BYTES

    assert project_result.read_bytes() == before

    cleanup = read_job(authority.path).result_metadata["ticket_cleanup"]
    assert cleanup["status"] == "cleared"
    assert cleanup["cleared"] == [ticket.ref.model_dump(mode="json")]
    assert cleanup["pending_cleanup"] == []


def test_ordinary_user_context_cannot_transition_a_ticket(workflow_env):
    """Only a registered job (agent) context may move a ticket between states."""
    env = workflow_env
    ticket = env.create(values={"verdict": True, "summary": "hello"})

    with pytest.raises(TicketForbidden):
        env.service.transition(
            env.user,
            env.read(ticket.ref).version,
            env.transition_request(outputs={"summary": "Done"}),
            env.operation("user-transition"),
        )

    assert env.read(ticket.ref).record.state_id == "review"


def test_execute_job_records_denied_write_attempts(tmp_path, raw_config, monkeypatch):
    """A write the agent tried but the boundary refused is recorded on the job.

    The parsed change set only carries writes that landed, so a read-only run
    that attempted (and was denied) a workspace edit would otherwise leave no
    trace. The live read/search-only Copilot probe reads this same telemetry to
    prove an attempt was actually made, not merely that bytes stayed unchanged.
    """
    from flowgency.blueprints import CompilationCache
    from flowgency.blueprints.library import BlueprintLibrary
    from flowgency.jobs.execution import execute_job
    from flowgency.jobs.models import JobRecord, JobRequest
    from flowgency.jobs.processes import ProcessStopEvidence
    from flowgency.jobs.resolution import resolve_job_request
    from flowgency.jobs.store import read_job
    from flowgency.prompts import PromptStore

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)

    class DeniedWriteIntegration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            return RunResult(
                0,
                "attempted a denied write",
                "",
                0.1,
                changed_files=[],
                write_attempts=["blocked-note.txt"],
                session_id="denied-write-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=request.ticket_tools.lifecycle.generation,
                    confirmed=True,
                    reason="exited",
                ),
            )

    spec = resolve_job_request(
        JobRequest(
            config_path=env.store.path,
            team_key=env.team_id,
            agent_name="builder",
            trigger="manual_prompt",
            routine_id=None,
            task_input="Attempt a workspace write that the boundary refuses.",
        ),
        config_store=env.store,
        library=BlueprintLibrary(env.store.load().config.flowgency.agent_library),
        cache=CompilationCache(
            env.store.load().config.flowgency.compilation_cache,
            {"claude-code": DeniedWriteIntegration.projector},
        ),
        prompt_store=PromptStore(env.store.load().config.flowgency.prompt_store),
        integrations={"claude-code": DeniedWriteIntegration()},
    )
    authority = env.job_store.create(JobRecord.from_spec(spec))
    record = env.job_store.read(authority)

    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=Path(spec.workspace_root),
            integration=DeniedWriteIntegration(),
            timeout=30,
            sandbox_root=None,
            team_root=Path(spec.team_root),
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )

    result = execute_job(authority)

    assert result.status == "complete", read_job(authority.path).execution_summary
    metadata = read_job(authority.path).result_metadata
    assert metadata["write_attempts"] == ["blocked-note.txt"]
    assert read_job(authority.path).changed_files == []


def _broker_client(request):
    """A ticket client bound to the worker-owned endpoint the run was handed."""
    from flowgency.tickets.broker import TicketToolClient

    return TicketToolClient(
        request.ticket_tools.url.removesuffix("/mcp"),
        request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
    )


def _confirmed_stop(request, *, reason="exited"):
    from flowgency.jobs.processes import ProcessStopEvidence

    lifecycle = request.ticket_tools.lifecycle
    return ProcessStopEvidence(
        job_id=lifecycle.job_id,
        generation=lifecycle.generation,
        confirmed=True,
        reason=reason,
    )


def _run_controlled_ticket_job(env, integration, monkeypatch, *, task_input, timeout=30):
    """Run a controlled integration through the real durable-job lifecycle.

    The integration drives the live in-process broker exactly as a runtime does;
    this only wires the job resolution, context, and execution around it so each
    lifecycle test asserts on real broker/service/provider state, not a mock.
    """
    from flowgency.blueprints import CompilationCache
    from flowgency.blueprints.library import BlueprintLibrary
    from flowgency.jobs.execution import execute_job
    from flowgency.jobs.models import JobRecord, JobRequest
    from flowgency.jobs.resolution import resolve_job_request
    from flowgency.prompts import PromptStore

    config = env.store.load().config
    spec = resolve_job_request(
        JobRequest(
            config_path=env.store.path,
            team_key=env.team_id,
            agent_name="builder",
            trigger="manual_prompt",
            routine_id=None,
            task_input=task_input,
        ),
        config_store=env.store,
        library=BlueprintLibrary(config.flowgency.agent_library),
        cache=CompilationCache(
            config.flowgency.compilation_cache,
            {"claude-code": type(integration).projector},
        ),
        prompt_store=PromptStore(config.flowgency.prompt_store),
        integrations={"claude-code": integration},
    )
    authority = env.job_store.create(JobRecord.from_spec(spec))
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=Path(spec.workspace_root),
            integration=integration,
            timeout=timeout,
            sandbox_root=None,
            team_root=Path(spec.team_root),
            runtime_policy=EffectiveRuntimePolicy(timeout=timeout),
        ),
    )
    return authority, execute_job(authority)


def test_transition_completes_without_a_mandatory_sign_off(tmp_path, raw_config, monkeypatch):
    """Sign-off is an optional release step, not a completion precondition.

    The run transitions the ticket to ``done`` with no sign-off; the ticket is
    already complete and still owned before any sign-off. When the agent then
    signs off, the assignment is released. Nothing in the transition required it.
    """
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    env.publish_criteria_workflow()
    ticket = env.create_assigned("builder", title="Sign-off optional")
    observed: dict = {}

    class OptionalSignOffIntegration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = _broker_client(request)
            assert client.call("get_ticket", {"ref": ticket.ref.model_dump(mode="json")})["ok"]
            assert client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-signoff",
                },
            )["ok"]
            transitioned = client.call(
                "transition_ticket",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "transition_id": "complete",
                    "inputs": {"verdict": True},
                    "outputs": {"summary": "Completed without sign-off"},
                    "assessments": [
                        {
                            "criterion_id": "evidence-reviewed",
                            "satisfied": True,
                            "reasoning": "Reviewed the existing result.",
                            "supporting_fields": ["summary"],
                        }
                    ],
                    "operation_id": "complete-signoff",
                },
            )
            assert transitioned["ok"] is True, transitioned
            # The ticket is complete and still owned before any sign-off exists.
            mid = env.read(ticket.ref).record
            observed["done_before_signoff"] = mid.state_id == "done"
            observed["owned_before_signoff"] = mid.assignee == "builder"
            observed["no_signoff_event"] = all(
                event.kind != "signed-off" for event in mid.events
            )
            # Signing off is available as an optional step and releases the ticket.
            signed = client.call(
                "sign_off",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "signoff-1",
                },
            )
            assert signed["ok"] is True, signed
            return RunResult(
                0,
                "optional-signoff",
                "",
                0.1,
                changed_files=[],
                session_id="optional-signoff-session",
                process_stop_evidence=_confirmed_stop(request),
            )

    authority, result = _run_controlled_ticket_job(
        env,
        OptionalSignOffIntegration(),
        monkeypatch,
        task_input="Complete the ticket; sign-off is optional.",
    )

    assert result.status == "complete"
    assert observed == {
        "done_before_signoff": True,
        "owned_before_signoff": True,
        "no_signoff_event": True,
    }
    final = env.read(ticket.ref).record
    assert final.state_id == "done"
    assert final.assignee is None
    assert final.active_run is None
    assert any(event.kind == "signed-off" for event in final.events)


def test_stale_transition_is_rejected_then_succeeds_after_refresh(tmp_path, raw_config, monkeypatch):
    """A transition against a superseded version is refused, and a refresh wins.

    The agent starts work, then a real intervening update bumps the ticket's
    revision. Transitioning with the pre-update version is rejected by the
    broker's compare-and-set as ``stale-ticket``; re-reading and retrying with
    the fresh version completes. No revision or digest is invented.
    """
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    env.publish_criteria_workflow()
    ticket = env.create_assigned("builder", title="Stale refresh")
    observed: dict = {}

    class StaleThenRefreshIntegration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = _broker_client(request)
            assert client.call("get_ticket", {"ref": ticket.ref.model_dump(mode="json")})["ok"]
            assert client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-stale",
                },
            )["ok"]
            # Capture a version, then make a genuine intervening mutation so it
            # becomes stale before the transition uses it.
            stale_version = env.read(ticket.ref).version.model_dump(mode="json")
            bumped = client.call(
                "update_ticket",
                {
                    "version": stale_version,
                    "operation_id": "bump-mid-flight",
                    "field_values": {"summary": "bumped mid-flight"},
                },
            )
            assert bumped["ok"] is True, bumped

            transition_payload = {
                "transition_id": "complete",
                "inputs": {"verdict": True},
                "outputs": {"summary": "Verified after refresh"},
                "assessments": [
                    {
                        "criterion_id": "evidence-reviewed",
                        "satisfied": True,
                        "reasoning": "Reviewed the current result.",
                        "supporting_fields": ["summary"],
                    }
                ],
            }
            rejected = client.call(
                "transition_ticket",
                {**transition_payload, "version": stale_version, "operation_id": "complete-stale"},
            )
            observed["rejected_ok"] = rejected["ok"]
            observed["rejected_code"] = (rejected.get("error") or {}).get("code")
            observed["still_review"] = env.read(ticket.ref).record.state_id == "review"

            fresh_version = env.read(ticket.ref).version.model_dump(mode="json")
            accepted = client.call(
                "transition_ticket",
                {**transition_payload, "version": fresh_version, "operation_id": "complete-fresh"},
            )
            assert accepted["ok"] is True, accepted
            return RunResult(
                0,
                "stale-then-refresh",
                "",
                0.1,
                changed_files=[],
                session_id="stale-refresh-session",
                process_stop_evidence=_confirmed_stop(request),
            )

    authority, result = _run_controlled_ticket_job(
        env,
        StaleThenRefreshIntegration(),
        monkeypatch,
        task_input="Transition the ticket, refreshing on any stale conflict.",
    )

    assert result.status == "complete"
    assert observed["rejected_ok"] is False
    assert observed["rejected_code"] == "stale-ticket"
    assert observed["still_review"] is True
    final = env.read(ticket.ref).record
    assert final.state_id == "done"
    assert final.assignee == "builder"
    assert final.active_run is None
    assert final.field_values["summary"] == "Verified after refresh"


def test_committed_transition_survives_a_failed_run_after_it(tmp_path, raw_config, monkeypatch):
    """A run that times out after a committed transition keeps the ticket done.

    The agent starts work, publishes evidence, and transitions the ticket to
    ``done`` -- all durably committed through the broker. The process then times
    out (a real, confirmed stop). The job fails, but the committed state,
    assignment, and retained artifact survive, and the confirmed stop clears the
    ticket's active run so it is not left pinned to a dead job.
    """
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    _verification_workflow(env)
    ticket = _assigned_review_ticket(env)

    class FailAfterTransitionIntegration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = _broker_client(request)
            assert client.call("get_ticket", {"ref": ticket.ref.model_dump(mode="json")})["ok"]
            assert client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-fail",
                },
            )["ok"]
            published = client.call(
                "publish_artifact",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "filename": "report.txt",
                    "media_type": "text/plain",
                    "content_b64": base64.b64encode(REPORT_BYTES).decode("ascii"),
                },
            )
            assert published["ok"] is True, published
            transitioned = client.call(
                "transition_ticket",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "transition_id": "complete",
                    "inputs": {"verdict": True},
                    "outputs": {
                        "summary": "Committed before the failure",
                        "evidence": published["result"],
                    },
                    "assessments": [
                        {
                            "criterion_id": "evidence-reviewed",
                            "satisfied": True,
                            "reasoning": "The retained report satisfies the rule.",
                            "supporting_fields": ["summary", "evidence"],
                        }
                    ],
                    "operation_id": "complete-fail",
                },
            )
            assert transitioned["ok"] is True, transitioned
            # The process is confirmed stopped after the commit: a genuine
            # timeout, not a fabricated success. The job must fail, but nothing
            # already committed may be lost.
            return RunResult(
                124,
                "",
                "timed out after committing the transition",
                0.1,
                changed_files=[],
                session_id="fail-after-commit-session",
                process_stop_evidence=_confirmed_stop(request, reason="timeout"),
            )

    authority, result = _run_controlled_ticket_job(
        env,
        FailAfterTransitionIntegration(),
        monkeypatch,
        task_input="Commit the transition, then time out.",
    )

    from flowgency.jobs.store import read_job

    assert result.status == "failed", read_job(authority.path).execution_summary
    final = env.read(ticket.ref).record
    assert final.state_id == "done"
    assert final.assignee == "builder"
    assert final.active_run is None
    assert final.field_values["summary"] == "Committed before the failure"
    evidence = final.field_values["evidence"]
    retained = env.provider.read_artifact(ticket.ref, evidence.value)
    assert retained.content == REPORT_BYTES

    cleanup = read_job(authority.path).result_metadata["ticket_cleanup"]
    assert cleanup["status"] == "cleared"
    assert cleanup["cleared"] == [ticket.ref.model_dump(mode="json")]

