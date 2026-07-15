"""Versioned data models for durable agent jobs."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from agency.blueprints.cache import CacheRef, CompiledArtifact
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedToolPolicy


SCHEMA_VERSION = 2
VALID_TRIGGERS = {
    "scheduled_prompt",
    "manual_prompt",
    "decision",
    "decision_retry",
}
VALID_STATUSES = {
    "queued",
    "waiting_for_memory",
    "running",
    "complete",
    "failed",
    "cancelled",
}


@dataclass(frozen=True)
class RuntimePolicySnapshot:
    timeout: int
    sandbox_mode: str
    sandbox_roots: tuple[str, ...]
    tool_mode: str
    tool_names: tuple[str, ...] = ()

    @classmethod
    def from_effective_policy(
        cls,
        policy: EffectiveRuntimePolicy,
    ) -> "RuntimePolicySnapshot":
        return cls(
            timeout=policy.timeout,
            sandbox_mode=policy.sandbox_mode,
            sandbox_roots=tuple(
                str(Path(root).resolve(strict=False))
                for root in policy.sandbox_roots
            ),
            tool_mode=policy.tools.mode,
            tool_names=tuple(policy.tools.names),
        )

    def to_effective_policy(self) -> EffectiveRuntimePolicy:
        return EffectiveRuntimePolicy(
            timeout=self.timeout,
            sandbox_mode=self.sandbox_mode,
            sandbox_roots=tuple(Path(root) for root in self.sandbox_roots),
            tools=ResolvedToolPolicy(self.tool_mode, self.tool_names),
        )


@dataclass(frozen=True)
class BlueprintRef:
    key: str
    source_digest: str
    integration: str
    projector_version: str
    cache_path: str

    @property
    def cache_ref(self) -> CacheRef:
        return CacheRef(
            self.integration,
            self.projector_version,
            self.source_digest,
        )

    @property
    def cache_entry_path(self) -> Path:
        return Path(self.cache_path)

    @property
    def cache_root(self) -> Path:
        return self.cache_entry_path.parent.parent.parent

    def to_artifact(self) -> CompiledArtifact:
        entry_path = self.cache_entry_path
        return CompiledArtifact(
            ref=self.cache_ref,
            entry_path=entry_path,
            runtime_path=entry_path / "runtime",
            manifest_path=entry_path / "manifest.json",
        )


@dataclass(frozen=True)
class MemoryBinding:
    selector: dict[str, object]
    canonical_json: str
    memory_hash: str
    path: str


@dataclass(frozen=True)
class JobRequest:
    config_path: Path
    group_key: str
    agent_name: str
    trigger: str
    task_input: str
    job_id: str = field(default_factory=lambda: uuid4().hex)
    routine_id: str | None = None
    memory_override: Any | None = None
    timeout_override: int | None = None
    trigger_context: dict[str, Any] | None = None
    superseded_prompt_source: dict[str, Any] | None = None

    @classmethod
    def from_superseded_prompt(
        cls,
        *,
        config_path: Path,
        group_key: str,
        agent_name: str,
        trigger: str,
        prompt_source: dict[str, Any],
        prompt_content: str,
        timeout_override: int | None = None,
        decision_context: dict[str, Any] | None = None,
    ) -> "JobRequest":
        return cls(
            config_path=config_path,
            group_key=group_key,
            agent_name=agent_name,
            trigger=trigger,
            task_input=prompt_content,
            timeout_override=timeout_override,
            trigger_context=decision_context,
            superseded_prompt_source=prompt_source,
        )

    @property
    def prompt_content(self) -> str:
        return self.task_input

    @property
    def decision_context(self) -> dict[str, Any] | None:
        return self.trigger_context


@dataclass(frozen=True)
class JobSpec:
    schema_version: int
    job_id: str
    config_path: str
    config_revision: str
    group_key: str
    group_path: str
    agent_name: str
    workspace_dir: str
    trigger: str
    integration_name: str
    integration_config: dict[str, Any]
    blueprint: BlueprintRef
    routine_id: str | None
    skill: str | None
    skill_arguments: tuple[str, ...]
    task_input: str
    runtime_policy: RuntimePolicySnapshot
    memory: MemoryBinding
    trigger_context: dict[str, Any] | None
    prompt_source: dict[str, Any] | None
    timeout_override: int | None
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        config_path: Path,
        group_key: str,
        agent_name: str,
        trigger: str,
        integration_name: str | None = None,
        integration_config: dict[str, Any] | None = None,
        config_revision: str | None = None,
        blueprint: BlueprintRef | dict[str, Any] | None = None,
        runtime_policy: RuntimePolicySnapshot | dict[str, Any] | None = None,
        memory: MemoryBinding | dict[str, Any] | None = None,
        routine_id: str | None = None,
        skill: str | None = None,
        skill_arguments: tuple[str, ...] = (),
        task_input: str | None = None,
        trigger_context: dict[str, Any] | None = None,
        group_path: Path | str | None = None,
        prompt_source: dict[str, Any] | None = None,
        prompt_content: str | None = None,
        timeout_override: int | None = None,
        decision_context: dict[str, Any] | None = None,
    ) -> "JobSpec":
        effective_task_input = task_input if task_input is not None else prompt_content
        effective_trigger_context = (
            trigger_context if trigger_context is not None else decision_context
        )
        effective_routine_id, effective_skill = cls._compat_routine_binding(
            trigger=trigger,
            routine_id=routine_id,
            skill=skill,
            prompt_source=prompt_source,
        )
        resolved_group_path = cls._infer_group_path(
            config_path=config_path,
            group_path=group_path,
            prompt_source=prompt_source,
            decision_context=decision_context,
        )
        resolved_workspace_dir = resolved_group_path.resolve(strict=False)
        spec = cls(
            schema_version=SCHEMA_VERSION,
            job_id=uuid4().hex,
            config_revision=config_revision or "compat-unresolved",
            config_path=str(config_path.resolve()),
            group_key=group_key,
            group_path=str(resolved_workspace_dir),
            agent_name=agent_name,
            workspace_dir=str(resolved_workspace_dir),
            trigger=trigger,
            integration_name=integration_name or "script",
            integration_config=dict(integration_config or {}),
            blueprint=cls._coerce_blueprint(blueprint, config_path),
            routine_id=effective_routine_id,
            skill=effective_skill,
            skill_arguments=tuple(skill_arguments),
            task_input=effective_task_input or "",
            runtime_policy=cls._coerce_runtime_policy(runtime_policy, timeout_override),
            memory=cls._coerce_memory(memory, config_path),
            trigger_context=effective_trigger_context,
            prompt_source=prompt_source,
            timeout_override=timeout_override,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        spec.validate()
        return spec

    @staticmethod
    def _compat_routine_binding(
        *,
        trigger: str,
        routine_id: str | None,
        skill: str | None,
        prompt_source: dict[str, Any] | None,
    ) -> tuple[str | None, str | None]:
        if trigger not in {"manual_prompt", "scheduled_prompt"}:
            return routine_id, skill
        if routine_id and skill:
            return routine_id, skill
        if not isinstance(prompt_source, dict):
            return routine_id, skill
        prompt_path = prompt_source.get("path")
        if not isinstance(prompt_path, str) or not prompt_path.strip():
            return routine_id, skill
        stem = Path(prompt_path).stem.strip()
        if not stem:
            return routine_id, skill
        return routine_id or stem, skill or stem

    @staticmethod
    def _infer_group_path(
        *,
        config_path: Path,
        group_path: Path | str | None,
        prompt_source: dict[str, Any] | None,
        decision_context: dict[str, Any] | None,
    ) -> Path:
        if group_path is not None:
            return Path(group_path)
        if decision_context and decision_context.get("decision_path"):
            return Path(str(decision_context["decision_path"])).resolve().parents[2]
        prompt_path = (prompt_source or {}).get("path")
        if isinstance(prompt_path, str):
            prompt_candidate = Path(prompt_path)
            if prompt_candidate.is_absolute() and prompt_candidate.exists():
                return prompt_candidate.resolve().parents[2]
        return config_path.resolve().parent / "group"

    @staticmethod
    def _coerce_blueprint(
        blueprint: BlueprintRef | dict[str, Any] | None,
        config_path: Path,
    ) -> BlueprintRef:
        if isinstance(blueprint, BlueprintRef):
            return blueprint
        if isinstance(blueprint, dict):
            return BlueprintRef(**blueprint)
        cache_path = config_path.resolve().parent / ".compat-cache" / "script" / "v1" / "unresolved"
        return BlueprintRef(
            key="compat-unresolved",
            source_digest="compat-unresolved",
            integration="script",
            projector_version="v1",
            cache_path=str(cache_path),
        )

    @staticmethod
    def _coerce_runtime_policy(
        runtime_policy: RuntimePolicySnapshot | dict[str, Any] | None,
        timeout_override: int | None,
    ) -> RuntimePolicySnapshot:
        if isinstance(runtime_policy, RuntimePolicySnapshot):
            return runtime_policy
        if isinstance(runtime_policy, dict):
            return RuntimePolicySnapshot(**runtime_policy)
        return RuntimePolicySnapshot(
            timeout=timeout_override if timeout_override is not None else 1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        )

    @staticmethod
    def _coerce_memory(
        memory: MemoryBinding | dict[str, Any] | None,
        config_path: Path,
    ) -> MemoryBinding:
        if isinstance(memory, MemoryBinding):
            return memory
        if isinstance(memory, dict):
            return MemoryBinding(**memory)
        selector = {"job": "compat-unresolved", "scope": "run", "version": 1}
        canonical_json = json.dumps(selector, sort_keys=True, separators=(",", ":"))
        return MemoryBinding(
            selector=selector,
            canonical_json=canonical_json,
            memory_hash="compat-unresolved",
            path=str((config_path.resolve().parent / ".compat-memory").resolve(strict=False)),
        )

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported job schema version: {self.schema_version}")
        string_fields = {
            "job_id": self.job_id,
            "config_revision": self.config_revision,
            "group_key": self.group_key,
            "group_path": self.group_path,
            "agent_name": self.agent_name,
            "workspace_dir": self.workspace_dir,
            "trigger": self.trigger,
            "integration_name": self.integration_name,
            "task_input": self.task_input,
        }
        for field_name, value in string_fields.items():
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
        if self.trigger not in VALID_TRIGGERS:
            raise ValueError(f"Invalid job trigger: {self.trigger}")
        if not self.job_id.strip():
            raise ValueError("Job ID is required")
        if not self.group_key.strip():
            raise ValueError("Group key is required")
        if not self.agent_name.strip():
            raise ValueError("Agent name is required")
        if not self.task_input.strip():
            raise ValueError("Prompt content must not be blank")
        if self.trigger in {"scheduled_prompt", "manual_prompt"} and not self._is_compat_prompt_spec():
            if not self.routine_id or not self.skill:
                raise ValueError(
                    "scheduled and manual jobs require routine_id and skill"
                )
        if self.trigger in {"decision", "decision_retry"}:
            if self.routine_id is not None or self.skill is not None:
                raise ValueError(
                    "decision jobs require routine_id and skill to be null"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "config_path": self.config_path,
            "config_revision": self.config_revision,
            "group_key": self.group_key,
            "group_path": self.group_path,
            "agent_name": self.agent_name,
            "workspace_dir": self.workspace_dir,
            "trigger": self.trigger,
            "integration_name": self.integration_name,
            "integration_config": dict(self.integration_config),
            "blueprint": asdict(self.blueprint),
            "routine_id": self.routine_id,
            "skill": self.skill,
            "skill_arguments": list(self.skill_arguments),
            "task_input": self.task_input,
            "runtime_policy": asdict(self.runtime_policy),
            "memory": asdict(self.memory),
            "trigger_context": self.trigger_context,
            "prompt_source": self.prompt_source,
            "timeout_override": self.timeout_override,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobSpec":
        values = dict(data)
        superseded_agent_dir = values.pop("agent_dir", None)
        workspace_dir = values.get("workspace_dir")
        if superseded_agent_dir is not None and superseded_agent_dir != workspace_dir:
            raise ValueError("agent_dir is deprecated and must match workspace_dir")
        values["integration_config"] = dict(values.get("integration_config") or {})
        values["blueprint"] = BlueprintRef(**values["blueprint"])
        runtime_policy = dict(values["runtime_policy"])
        runtime_policy["sandbox_roots"] = tuple(runtime_policy.get("sandbox_roots") or ())
        runtime_policy["tool_names"] = tuple(runtime_policy.get("tool_names") or ())
        values["runtime_policy"] = RuntimePolicySnapshot(**runtime_policy)
        values["memory"] = MemoryBinding(**values["memory"])
        values["skill_arguments"] = tuple(values.get("skill_arguments") or ())
        spec = cls(**values)
        spec.validate()
        return spec

    @property
    def prompt_content(self) -> str:
        return self.task_input

    @property
    def decision_context(self) -> dict[str, Any] | None:
        return self.trigger_context

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_dir)

    @property
    def agent_dir(self) -> Path:
        return self.workspace_path

    def _is_compat_prompt_spec(self) -> bool:
        if self.schema_version != SCHEMA_VERSION:
            return False
        prompt_source = self.prompt_source or {}
        prompt_type = prompt_source.get("type")
        return (
            self.config_revision == "compat-unresolved"
            and prompt_type in {"saved_prompt", "prompt", "test"}
        )


@dataclass
class JobRecord:
    spec: JobSpec
    status: str = "queued"
    worker_pid: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    changed_files: list[dict[str, Any]] = field(default_factory=list)
    execution_summary: str | None = None
    base_sha: str | None = None
    memory_publication: dict[str, Any] | None = None

    @classmethod
    def from_spec(cls, spec: JobSpec) -> "JobRecord":
        spec.validate()
        return cls(spec=spec)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spec"] = self.spec.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        values = dict(data)
        spec = JobSpec.from_dict(dict(values.pop("spec")))
        record = cls(spec=spec, **values)
        if record.status not in VALID_STATUSES:
            raise ValueError(f"Invalid job status: {record.status}")
        return record


@dataclass(frozen=True)
class JobHandle:
    job_id: str
    status: str
    path: Path
    worker_pid: int | None
