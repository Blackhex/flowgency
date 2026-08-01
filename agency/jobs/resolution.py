from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from agency.blueprints import BlueprintLibrary, CompilationCache
from agency.configuration.effective import resolve_effective_policy
from agency.configuration.models import AgentInstance, Routine
from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.paths import validate_resolved_paths
from agency.configuration.group_paths import resolve_group_paths
from agency.configuration.store import ConfigSnapshot, ConfigStore
from agency.integrations import BaseIntegration, get_integration
from agency.integrations.models import IntegrationRunRequest
from agency.memory.selectors import (
    resolve_memory_selector,
    select_effective_memory,
)
from agency.prompts import PromptNotFoundError, PromptStore, build_prompt_task_input, resolve_catalog_prompt
from agency.records.protocol import append_reporting_protocol
from agency.records.validation import writable_agent_names

from .models import BlueprintRef, JobRequest, JobSpec, MemoryBinding, PromptSnapshot, RuntimePolicySnapshot


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


def _missing_prompt_error(
    *,
    group_key: str,
    agent_name: str,
    scope: str,
    name: str,
    action: str,
) -> JobValidationError:
    return JobValidationError(
        f"Configured {scope} prompt '{name}' for {group_key}/{agent_name} is no longer available; {action}."
    )


def _snapshot_private_prompt(
    prompt_store: PromptStore,
    *,
    group_key: str,
    agent_name: str,
    name: str,
) -> PromptSnapshot:
    try:
        stored = prompt_store.read(group_key, agent_name, name)
    except PromptNotFoundError as exc:
        raise _missing_prompt_error(
            group_key=group_key,
            agent_name=agent_name,
            scope="instance",
            name=name,
            action="restore it in the prompt store or remove it from the agent prompt registration",
        ) from exc
    return PromptSnapshot(
        name=name,
        content=stored.document.source.decode("utf-8"),
        source_digest=stored.document.digest,
    )


def _resolve_saved_prompt(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    prompt_store: PromptStore,
    *,
    group_key: str,
    agent_name: str,
    scope: str,
    name: str,
):
    try:
        return resolve_catalog_prompt(
            snapshot,
            library,
            prompt_store,
            group_key,
            agent_name,
            scope=scope,
            name=name,
        )
    except (KeyError, PromptNotFoundError) as exc:
        action = "restore the prompt source or update the saved prompt selector"
        raise _missing_prompt_error(
            group_key=group_key,
            agent_name=agent_name,
            scope=scope,
            name=name,
            action=action,
        ) from exc


def resolve_job_request(
    request: JobRequest,
    *,
    config_store: ConfigStore,
    library: BlueprintLibrary,
    cache: CompilationCache,
    prompt_store: PromptStore,
    integrations: Mapping[str, BaseIntegration],
    snapshot: ConfigSnapshot | None = None,
) -> JobSpec:
    snapshot = snapshot or config_store.load()
    issues = validate_resolved_paths(snapshot.config)
    if issues:
        raise ValidationFailed(issues)
    try:
        group = snapshot.config.groups[request.group_key]
    except KeyError as exc:
        raise JobValidationError(f"Unknown group: {request.group_key}") from exc
    paths = resolve_group_paths(group)

    agent = _find_agent(group, request.agent_name)
    routine = _find_routine(agent, request.routine_id)

    if request.routine_id is not None and routine is None:
        raise JobValidationError("scheduled and manual jobs require an existing routine")

    if request.trigger == "scheduled_prompt" and routine is None:
        raise JobValidationError(
            "scheduled jobs require an existing routine"
        )
    if request.trigger in {"scheduled_prompt", "manual_prompt"} and routine is not None and not routine.enabled:
        raise JobValidationError(
            f"Routine '{routine.id}' is disabled; enable it before running"
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
    runtime_policy = resolve_effective_policy(
        snapshot.config,
        request.group_key,
        request.agent_name,
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

    validation_task_file = paths.logs / f"{request.job_id}.prompt"
    integration.require_valid_run(
        IntegrationRunRequest(
            workspace_root=paths.workspace_root,
            launch_dir=artifact.runtime_path.resolve(),
            task_file=validation_task_file,
            timeout=runtime_policy.timeout,
            runtime_policy=runtime_policy,
            skill=None,
            skill_arguments=(),
            enforce_validation=True,
            memory_working_dir=None,
        )
    )

    private_prompts = tuple(
        _snapshot_private_prompt(
            prompt_store,
            group_key=request.group_key,
            agent_name=request.agent_name,
            name=name,
        )
        for name in agent.prompts
    )

    if request.trigger in {"manual_prompt", "scheduled_prompt"}:
        selector = routine.prompt if routine is not None else request.prompt
        if selector is not None:
            catalog_prompt = _resolve_saved_prompt(
                snapshot,
                library,
                prompt_store,
                group_key=request.group_key,
                agent_name=request.agent_name,
                scope=selector.scope,
                name=selector.name,
            )
            task_input = build_prompt_task_input(
                catalog_prompt.document.body,
                arguments=routine.arguments if routine is not None else (),
                invocation_input=request.invocation_input,
            )
            prompt_source = {
                "type": "blueprint_prompt" if catalog_prompt.scope == "blueprint" else "instance_prompt",
                "scope": catalog_prompt.scope,
                "name": catalog_prompt.document.name,
                "source_path": catalog_prompt.source_path,
                "source_digest": catalog_prompt.document.digest,
            }
        elif request.task_input.strip():
            task_input = request.task_input
            prompt_source = {"type": "ad_hoc"}
        else:
            raise JobValidationError("manual prompt jobs require a routine, saved prompt, or nonblank task_input")
    elif request.trigger == "decision":
        task_input = request.task_input
        prompt_source = {"type": "decision"}
    else:
        task_input = request.task_input
        prompt_source = {"type": "decision_retry"}

    return JobSpec(
        schema_version=4,
        job_id=request.job_id,
        config_path=str(snapshot.path),
        config_revision=snapshot.revision,
        group_key=request.group_key,
        workspace_root=str(paths.workspace_root),
        group_root=str(paths.group_root),
        agent_name=request.agent_name,
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
        skill=None,
        skill_arguments=(),
        task_input=append_reporting_protocol(
            task_input,
            tool_mode=runtime_policy.tools.mode,
            tool_names=tuple(runtime_policy.tools.names),
        ),
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
        private_prompts=private_prompts,
        writable_agents=tuple(
            sorted(writable_agent_names(snapshot.config, request.group_key))
        ),
    )
