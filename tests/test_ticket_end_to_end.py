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
                request.ticket_tools.env["FLOWGENCY_TICKET_ENDPOINT"],
                request.ticket_tools.env["FLOWGENCY_TICKET_TOKEN"],
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

