from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from fastapi import Request

from flowgency.blueprints import BlueprintLibrary, CompilationCache
from flowgency.configuration import ConfigStore, ValidationFailed
from flowgency.configuration.issues import ValidationIssue
from flowgency.configuration.paths import (
    initialize_storage_directories,
    validate_resolved_paths,
)
from flowgency.integrations import REGISTRY, BaseIntegration
from flowgency.instances import InstanceService
from flowgency.jobs.authority import JobStore
from flowgency.jobs.tickets import TicketJobCoordinator
from flowgency.jobs.submission import submit_job_request
from flowgency.jobs.submission import _projector_registry
from flowgency.memory import MemoryStore
from flowgency.prompts import PromptService, PromptStore, validate_prompt_catalogs
from flowgency.tickets.access import TicketAccessRegistry
from flowgency.tickets.service import TicketService
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.configuration import WorkflowConfigurationService
from flowgency.workflows.library import WorkflowLibrary
from flowgency.clock import now as clock_now


@dataclass(frozen=True)
class FlowgencyServices:
    config_path: Path
    config_store: ConfigStore
    blueprint_library: BlueprintLibrary | None
    compilation_cache: CompilationCache | None
    memory_store: MemoryStore | None
    prompt_store: PromptStore | None
    job_store: JobStore | None
    instances: InstanceService | None
    integrations: Mapping[str, BaseIntegration]
    startup_error: Exception | None = None
    prompt_service: PromptService | None = None
    prompt_issues: tuple[ValidationIssue, ...] = ()
    workflow_library: WorkflowLibrary | None = None
    workflow_configuration: WorkflowConfigurationService | None = None
    tickets: TicketService | None = None
    ticket_jobs: TicketJobCoordinator | None = None


def build_services(config_path: Path | None = None) -> FlowgencyServices:
    resolved = Path(
        config_path
        or os.environ.get("FLOWGENCY_CONFIG")
        or Path.cwd() / "config.yaml"
    ).expanduser().resolve()
    config_store = ConfigStore(resolved)
    try:
        snapshot = config_store.load()
        issues = validate_resolved_paths(snapshot.config)
        if issues:
            raise ValidationFailed(issues)
        initialize_storage_directories(snapshot.config)
        flowgency = snapshot.config.flowgency
        library_root = flowgency.agent_library
        cache_root = flowgency.compilation_cache
        memory_root = flowgency.memory_store
        prompt_root = flowgency.prompt_store
        if library_root is None or cache_root is None or memory_root is None or prompt_root is None:
            raise ValueError("Flowgency services require agent_library, compilation_cache, memory_store, and prompt_store.")
        blueprint_library = BlueprintLibrary(Path(library_root))
        compilation_cache = CompilationCache(Path(cache_root), _projector_registry())
        memory_store = MemoryStore(Path(memory_root))
        prompt_store = PromptStore(Path(prompt_root))
        prompt_service = PromptService(
            config_store=config_store,
            library=blueprint_library,
            store=prompt_store,
        )
        catalog_issues = validate_prompt_catalogs(snapshot, blueprint_library, prompt_store)
        job_store = JobStore(Path(memory_root))
        instances = InstanceService(
            config_store=config_store,
            library=blueprint_library,
            memory_store=memory_store,
            prompt_store=prompt_store,
        )
        workflow_library = None
        workflow_configuration = None
        tickets = None
        ticket_jobs = None
        workflow_root = flowgency.workflow_library
        if workflow_root is not None:
            try:
                workflow_library = WorkflowLibrary(Path(workflow_root))
                workflow_configuration = WorkflowConfigurationService(
                    config_store,
                    workflow_library,
                    lambda binding: resolve_storage(binding, clock=clock_now),
                )
                access_registry = TicketAccessRegistry(job_store)
                tickets = TicketService(
                    config_store,
                    workflow_library,
                    lambda binding: resolve_storage(binding, clock=clock_now),
                    access_registry.validate_context,
                    clock=clock_now,
                )
                ticket_jobs = TicketJobCoordinator(
                    service=tickets,
                    job_store=job_store,
                    config_store=config_store,
                    submitter=submit_job_request,
                )
            except Exception:
                workflow_library = None
                workflow_configuration = None
                tickets = None
                ticket_jobs = None
        return FlowgencyServices(
            config_path=resolved,
            config_store=config_store,
            blueprint_library=blueprint_library,
            compilation_cache=compilation_cache,
            memory_store=memory_store,
            prompt_store=prompt_store,
            prompt_service=prompt_service,
            job_store=job_store,
            instances=instances,
            integrations=REGISTRY,
            startup_error=None,
            prompt_issues=catalog_issues,
            workflow_library=workflow_library,
            workflow_configuration=workflow_configuration,
            tickets=tickets,
            ticket_jobs=ticket_jobs,
        )
    except Exception as exc:
        return FlowgencyServices(
            config_path=resolved,
            config_store=config_store,
            blueprint_library=None,
            compilation_cache=None,
            memory_store=None,
            prompt_store=None,
            prompt_service=None,
            job_store=None,
            instances=None,
            integrations=REGISTRY,
            startup_error=exc,
            workflow_library=None,
            workflow_configuration=None,
            tickets=None,
            ticket_jobs=None,
        )


def get_services(request: Request) -> FlowgencyServices:
    services = getattr(request.app.state, "services", None)
    config_path_getter = getattr(request.app.state, "get_config_path", None)
    current_path = (
        Path(config_path_getter()).expanduser().resolve()
        if callable(config_path_getter)
        else Path(
            os.environ.get("FLOWGENCY_CONFIG") or Path.cwd() / "config.yaml"
        ).expanduser().resolve()
    )
    if (
        services is None
        or not hasattr(services, "config_path")
        or services.config_path != current_path
    ):
        services = build_services(current_path)
        request.app.state.services = services
    return services