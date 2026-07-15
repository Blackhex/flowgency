from dataclasses import replace
from pathlib import Path

from agency.blueprints.cache import active_pins
from agency.config import get_agent_dir, get_sandbox_root, load_config_path, normalize_agents
from agency.integrations import detect_integration, get_integration

from .launcher import JobLauncher, default_launcher
from .models import BlueprintRef, JobHandle, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from .store import job_path, write_job


class JobSubmissionError(RuntimeError):
    def __init__(self, message: str, job_path: Path):
        super().__init__(message)
        self.job_path = job_path


def _resolve_superseded_spec(spec: JobSpec) -> JobSpec:
    if spec.config_revision != "compat-unresolved":
        return spec

    config = load_config_path(Path(spec.config_path))
    raw_group = config.get("groups", {}).get(spec.group_key)
    if raw_group is None:
        return spec
    group_path = Path(raw_group["path"]).resolve()
    agents = normalize_agents(
        raw_group.get("agents", []),
        raw_group.get("default_integration", "claude-code"),
    )
    agent_config = next(
        (agent for agent in agents if agent["name"] == spec.agent_name),
        None,
    )
    if agent_config is None:
        return spec
    group = {**raw_group, "path": group_path, "agents_full": agents}
    agent_dir = get_agent_dir(group, spec.agent_name).resolve()
    integration = detect_integration(agent_dir) or get_integration(
        agent_config.get(
            "integration", raw_group.get("default_integration", "claude-code")
        )
    )
    dispatch = raw_group.get("dispatch", {})
    configured_timeout = dispatch.get("timeout", 1800)
    agent_dispatch = dispatch.get("agents", {}).get(spec.agent_name, {})
    if isinstance(agent_dispatch, dict):
        configured_timeout = agent_dispatch.get("timeout", configured_timeout)
    timeout = spec.timeout_override if spec.timeout_override is not None else configured_timeout

    sandbox = get_sandbox_root(raw_group)
    if sandbox and sandbox.roots:
        sandbox_mode = "restricted"
        sandbox_roots = tuple(str(Path(root).resolve()) for root in sandbox.roots)
    else:
        sandbox_mode = "unrestricted"
        sandbox_roots = ()
    allowed_tools = tuple(getattr(sandbox, "allowed_tools", ()) or ()) if sandbox else ()
    tool_mode = "allowlist" if allowed_tools else "all"
    routine_id = spec.routine_id
    skill = spec.skill
    if spec.trigger in {"manual_prompt", "scheduled_prompt"}:
        if not routine_id:
            prompt_source = spec.prompt_source or {}
            prompt_path = prompt_source.get("path")
            if isinstance(prompt_path, str) and prompt_path.strip():
                routine_id = Path(prompt_path).stem.strip() or "superseded-routine"
            else:
                routine_id = "superseded-routine"
        if not skill:
            skill = routine_id

    return JobSpec(
        schema_version=spec.schema_version,
        job_id=spec.job_id,
        config_path=spec.config_path,
        config_revision="compat-submission-resolved",
        group_key=spec.group_key,
        group_path=str(group_path),
        agent_name=spec.agent_name,
        agent_dir=str(agent_dir),
        trigger=spec.trigger,
        integration_name=integration.name,
        integration_config=dict(agent_config.get("integration_config") or {}),
        blueprint=BlueprintRef(
            key="compat-direct-agent-dir",
            source_digest="compat-direct-agent-dir",
            integration=integration.name,
            projector_version="v0",
            cache_path=str(agent_dir),
        ),
        routine_id=routine_id,
        skill=skill,
        skill_arguments=spec.skill_arguments,
        task_input=spec.task_input,
        runtime_policy=RuntimePolicySnapshot(
            timeout=timeout,
            sandbox_mode=sandbox_mode,
            sandbox_roots=sandbox_roots,
            tool_mode=tool_mode,
            tool_names=allowed_tools,
        ),
        memory=MemoryBinding(
            selector=spec.memory.selector,
            canonical_json=spec.memory.canonical_json,
            memory_hash=spec.memory.memory_hash,
            path=spec.memory.path,
        ),
        trigger_context=spec.trigger_context,
        prompt_source=spec.prompt_source,
        timeout_override=spec.timeout_override,
        created_at=spec.created_at,
    )


def submit_job(spec: JobSpec, launcher: JobLauncher | None = None) -> JobHandle:
    spec = _resolve_superseded_spec(spec)
    spec.validate()
    group_path = Path(spec.group_path)
    artifact = spec.blueprint.to_artifact()
    path = job_path(group_path, spec.job_id)
    record = JobRecord.from_spec(spec)
    pin_path = artifact.ref and Path()
    try:
        pin_path = active_pins(spec.blueprint.cache_root, artifact.ref)
    except Exception:
        pin_path = ()
    from agency.blueprints.cache import pin_artifact, release_pin

    should_pin = spec.config_revision != "compat-submission-resolved"
    if should_pin:
        pin_artifact(spec.blueprint.cache_root, artifact.ref, spec.job_id)
    selected_launcher = launcher or default_launcher()
    try:
        write_job(path, record)
        result = selected_launcher.launch(path)
    except Exception as error:
        if should_pin:
            release_pin(spec.blueprint.cache_root, artifact.ref, spec.job_id)
        failed = replace(
            record,
            status="failed",
            execution_summary=f"Launch error: {error}",
        )
        write_job(path, failed)
        raise JobSubmissionError(str(error), path) from error
    return JobHandle(spec.job_id, "queued", path, result.worker_pid)
