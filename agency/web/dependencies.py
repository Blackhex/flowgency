from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from fastapi import Request

from agency.blueprints import BlueprintLibrary, CompilationCache
from agency.configuration import ConfigStore, ValidationFailed
from agency.configuration.paths import initialize_control_directories, validate_resolved_paths
from agency.integrations import REGISTRY, BaseIntegration
from agency.instances import InstanceService
from agency.jobs.authority import JobStore
from agency.jobs.submission import _projector_registry
from agency.memory import MemoryStore


@dataclass(frozen=True)
class AgencyServices:
    config_path: Path
    config_store: ConfigStore
    blueprint_library: BlueprintLibrary | None
    compilation_cache: CompilationCache | None
    memory_store: MemoryStore | None
    job_store: JobStore | None
    instances: InstanceService | None
    integrations: Mapping[str, BaseIntegration]
    startup_error: Exception | None = None


def build_services(config_path: Path | None = None) -> AgencyServices:
    resolved = Path(
        config_path
        or os.environ.get("AGENCY_CONFIG")
        or Path.cwd() / "config.yaml"
    ).expanduser().resolve()
    config_store = ConfigStore(resolved)
    try:
        snapshot = config_store.load()
        initialize_control_directories(snapshot.config)
        issues = validate_resolved_paths(snapshot.config)
        if issues:
            raise ValidationFailed(issues)
        agency = snapshot.config.agency
        library_root = agency.agent_library
        cache_root = agency.compilation_cache
        memory_root = agency.memory_store
        if library_root is None or cache_root is None or memory_root is None:
            raise ValueError("Strict canonical services require agent_library, compilation_cache, and memory_store.")
        blueprint_library = BlueprintLibrary(Path(library_root))
        compilation_cache = CompilationCache(Path(cache_root), _projector_registry())
        memory_store = MemoryStore(Path(memory_root))
        job_store = JobStore(Path(memory_root))
        instances = InstanceService(
            config_store=config_store,
            library=blueprint_library,
            memory_store=memory_store,
        )
        return AgencyServices(
            config_path=resolved,
            config_store=config_store,
            blueprint_library=blueprint_library,
            compilation_cache=compilation_cache,
            memory_store=memory_store,
            job_store=job_store,
            instances=instances,
            integrations=REGISTRY,
            startup_error=None,
        )
    except Exception as exc:
        return AgencyServices(
            config_path=resolved,
            config_store=config_store,
            blueprint_library=None,
            compilation_cache=None,
            memory_store=None,
            job_store=None,
            instances=None,
            integrations=REGISTRY,
            startup_error=exc,
        )


def get_services(request: Request) -> AgencyServices:
    services = getattr(request.app.state, "services", None)
    config_path_getter = getattr(request.app.state, "get_config_path", None)
    current_path = (
        Path(config_path_getter()).expanduser().resolve()
        if callable(config_path_getter)
        else Path(
            os.environ.get("AGENCY_CONFIG") or Path.cwd() / "config.yaml"
        ).expanduser().resolve()
    )
    if services is None or services.config_path != current_path:
        services = build_services(current_path)
        request.app.state.services = services
    return services