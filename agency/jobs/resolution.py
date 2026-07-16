from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from agency.blueprints import BlueprintLibrary, CompilationCache
from agency.configuration.models import AgentInstance, Routine
from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration, get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedToolPolicy,
)
from agency.memory.selectors import (
    resolve_memory_selector,
    select_effective_memory,
)

from .models import BlueprintRef, JobRequest, JobSpec, MemoryBinding, RuntimePolicySnapshot


class JobValidationError(ValueError):
    pass


def _build_issue(code: str, scope: str, field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        scope=scope,
        field=field,
        message=message,
        corrective_hint=hint,
    )


def _find_agent(group, agent_name: str) -> AgentInstance:
    try:
        return group.agents[agent_name]
    except KeyError as exc:
        raise JobValidationError(f"Unknown agent: {agent_name}") from exc


def _find_routine(agent: AgentInstance, routine_id: str | None) -> Routine | None:
    if routine_id is None:
        return None
    for routine in agent.routines:
        if routine.id == routine_id:
            return routine
    return None


def _bind_integration(
    integration_name: str,
    integration_config: Mapping[str, object],
    registered: Mapping[str, BaseIntegration],
) -> BaseIntegration:
    integration = registered.get(integration_name) or get_integration(integration_name)
    if hasattr(integration, "with_config") and integration_config:
        return integration.with_config(dict(integration_config))
    return integration


def _resolve_runtime_policy(
    *,
    group,
    agent: AgentInstance,
    group_key: str,
    agent_name: str,
    timeout_override: int | None,
    integration: BaseIntegration,
) -> EffectiveRuntimePolicy:
    if timeout_override is not None:
        timeout = timeout_override
    elif "timeout" in agent.runtime.model_fields_set:
        timeout = agent.runtime.timeout
    else:
        timeout = group.runtime.timeout

    if "tools" in agent.runtime.model_fields_set:
        tools = agent.runtime.tools
    else:
        tools = group.runtime.tools

    agent_sandbox = agent.runtime.sandbox
    group_sandbox = group.runtime.sandbox
    if "mode" in agent_sandbox.model_fields_set:
        sandbox_mode = agent_sandbox.mode
    else:
        sandbox_mode = group_sandbox.mode
    if sandbox_mode == "unrestricted":
        sandbox_roots = ()
    else:
        sandbox_roots = tuple(group_sandbox.roots) + tuple(
            agent_sandbox.additional_roots
        )

    policy = EffectiveRuntimePolicy(
        timeout=timeout,
        sandbox_mode=sandbox_mode,
        sandbox_roots=sandbox_roots,
        tools=ResolvedToolPolicy(mode=tools.mode, names=tuple(tools.names)),
    )
    issues = integration.validate_runtime_policy(policy)
    if issues:
        raise ValidationFailed(issues)
    return policy


def resolve_job_request(
    request: JobRequest,
    *,
    config_store: ConfigStore,
    library: BlueprintLibrary,
    cache: CompilationCache,
    integrations: Mapping[str, BaseIntegration],
) -> JobSpec:
    snapshot = config_store.load()
    try:
        group = snapshot.config.groups[request.group_key]
    except KeyError as exc:
        raise JobValidationError(f"Unknown group: {request.group_key}") from exc

    agent = _find_agent(group, request.agent_name)
    routine = _find_routine(agent, request.routine_id)

    if request.trigger in {"scheduled_prompt", "manual_prompt"} and routine is None:
        raise JobValidationError(
            "scheduled and manual jobs require an existing routine"
        )
    if request.trigger in {"decision", "decision_retry"} and request.routine_id is not None:
        raise JobValidationError(
            "decision jobs require routine_id and skill to be null"
        )

    integration = _bind_integration(
        agent.integration,
        agent.integration_config,
        integrations,
    )
    runtime_policy = _resolve_runtime_policy(
        group=group,
        agent=agent,
        group_key=request.group_key,
        agent_name=request.agent_name,
        timeout_override=request.timeout_override,
        integration=integration,
    )
    inspection = library.inspect(agent.blueprint)
    artifact = cache.ensure_compiled(agent.integration, inspection)

    selector = select_effective_memory(
        request.memory_override,
        routine.memory if routine is not None else None,
        agent.default_memory,
    )
    resolved_memory = resolve_memory_selector(
        selector,
        job_id=request.job_id,
        group_key=request.group_key,
        agent_name=request.agent_name,
        routine_id=routine.id if routine is not None else None,
        channels=snapshot.config.memory.channels,
        store_root=snapshot.config.agency.memory_store,
    )

    validation_task_file = (
        group.path.resolve()
        / "shared"
        / "jobs"
        / f"{request.job_id}.prompt"
    )
    integration.require_valid_run(
        IntegrationRunRequest(
            workspace_dir=group.path.resolve(),
            launch_dir=artifact.runtime_path.resolve(),
            task_file=validation_task_file,
            timeout=runtime_policy.timeout,
            runtime_policy=runtime_policy,
            skill=routine.skill if routine is not None else None,
            skill_arguments=(
                routine.arguments if routine is not None else ()
            ),
            enforce_validation=True,
            memory_working_dir=None,
        )
    )

    if request.trigger in {"manual_prompt", "scheduled_prompt"}:
        prompt_source = {"type": "routine", "routine_id": routine.id if routine else None}
    elif request.trigger == "decision":
        prompt_source = {"type": "decision"}
    else:
        prompt_source = {"type": "decision_retry"}

    return JobSpec(
        schema_version=2,
        job_id=request.job_id,
        config_path=str(snapshot.path),
        config_revision=snapshot.revision,
        group_key=request.group_key,
        group_path=str(group.path.resolve()),
        agent_name=request.agent_name,
        workspace_dir=str(group.path.resolve()),
        trigger=request.trigger,
        integration_name=agent.integration,
        integration_config=dict(agent.integration_config),
        blueprint=BlueprintRef(
            key=inspection.key,
            source_digest=inspection.snapshot.digest,
            integration=artifact.ref.integration,
            projector_version=artifact.ref.projector_version,
            cache_path=str(artifact.entry_path.resolve()),
        ),
        routine_id=routine.id if routine is not None else None,
        skill=routine.skill if routine is not None else None,
        skill_arguments=routine.arguments if routine is not None else (),
        task_input=request.task_input,
        runtime_policy=RuntimePolicySnapshot.from_effective_policy(runtime_policy),
        memory=MemoryBinding(
            selector=resolved_memory.selector.model_dump(mode="python"),
            canonical_json=resolved_memory.canonical_json,
            memory_hash=resolved_memory.memory_hash,
            path=str(resolved_memory.directory.resolve()),
        ),
        trigger_context=request.trigger_context,
        prompt_source=prompt_source,
        timeout_override=request.timeout_override,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
