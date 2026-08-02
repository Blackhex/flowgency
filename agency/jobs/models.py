"""Versioned data models for durable agent jobs."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from agency.blueprints.cache import CacheRef, CompiledArtifact
from agency.configuration.models import MemorySelector, PromptSelector
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


SCHEMA_VERSION = 4
SUPPORTED_SCHEMA_VERSIONS = frozenset({3, 4})
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
    mode: str
    rules: tuple[dict, ...] = ()

    @classmethod
    def from_effective_policy(
        cls,
        policy: EffectiveRuntimePolicy,
    ) -> "RuntimePolicySnapshot":
        return cls(
            timeout=policy.timeout,
            mode=policy.mode,
            rules=tuple(
                {
                    "path": None if rule.path is None else str(Path(rule.path).resolve(strict=False)),
                    "tools": None if rule.tools is None else list(rule.tools),
                }
                for rule in policy.rules
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"timeout": self.timeout, "mode": self.mode, "rules": list(self.rules)}

    def to_effective_policy(self) -> EffectiveRuntimePolicy:
        return EffectiveRuntimePolicy(
            timeout=self.timeout,
            mode=self.mode,
            rules=tuple(
                ResolvedPermissionRule(
                    path=None if entry["path"] is None else Path(entry["path"]),
                    tools=None if entry["tools"] is None else tuple(entry["tools"]),
                )
                for entry in self.rules
            ),
        )


@dataclass(frozen=True)
class BlueprintRef:
    key: str
    source_digest: str
    integration: str
    projector_version: str
    cache_path: str
    instance_digest: str = ""

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
class PromptSnapshot:
    name: str
    content: str
    source_digest: str


@dataclass(frozen=True)
class JobRequest:
    config_path: Path
    group_key: str
    agent_name: str
    trigger: str
    task_input: str = ""
    prompt: PromptSelector | None = None
    invocation_input: str = ""
    job_id: str = field(default_factory=lambda: uuid4().hex)
    routine_id: str | None = None
    memory_override: MemorySelector | None = None
    timeout_override: int | None = None
    trigger_context: dict[str, Any] | None = None
    due_at: str | None = None

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
    workspace_root: str
    group_root: str
    agent_name: str
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
    private_prompts: tuple[PromptSnapshot, ...] = ()
    # Instances that may be named as a proposal's execution_agent, resolved from
    # the pinned configuration at submission. None means "not snapshotted".
    writable_agents: tuple[str, ...] | None = None

    def validate(self) -> None:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported job schema version: {self.schema_version}")
        string_fields = {
            "job_id": self.job_id,
            "config_revision": self.config_revision,
            "group_key": self.group_key,
            "workspace_root": self.workspace_root,
            "group_root": self.group_root,
            "agent_name": self.agent_name,
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
        if self.schema_version == 3:
            self._validate_v3_prompt_contract()
        else:
            self._validate_v4_prompt_contract()

    def _validate_v3_prompt_contract(self) -> None:
        if self.trigger in {"scheduled_prompt", "manual_prompt"}:
            if not self.routine_id or not self.skill:
                raise ValueError(
                    "scheduled and manual jobs require routine_id and skill"
                )
        if self.trigger in {"decision", "decision_retry"}:
            if self.routine_id is not None or self.skill is not None:
                raise ValueError(
                    "decision jobs require routine_id and skill to be null"
                )

    def _validate_v4_prompt_contract(self) -> None:
        if self.skill is not None:
            raise ValueError("schema v4 jobs must not set skill")
        if self.skill_arguments != ():
            raise ValueError("schema v4 jobs must keep skill_arguments empty")
        if self.trigger in {"scheduled_prompt", "manual_prompt"}:
            if self.prompt_source is None:
                raise ValueError("prompt-backed jobs require a prompt_source")
            if self.trigger == "scheduled_prompt" and not self.routine_id:
                raise ValueError("scheduled prompt jobs require routine_id")
        if self.trigger in {"decision", "decision_retry"} and self.routine_id is not None:
            raise ValueError("decision jobs require routine_id to be null")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "config_path": self.config_path,
            "config_revision": self.config_revision,
            "group_key": self.group_key,
            "workspace_root": self.workspace_root,
            "group_root": self.group_root,
            "agent_name": self.agent_name,
            "trigger": self.trigger,
            "integration_name": self.integration_name,
            "integration_config": dict(self.integration_config),
            "blueprint": asdict(self.blueprint),
            "routine_id": self.routine_id,
            "skill": self.skill,
            "skill_arguments": list(self.skill_arguments),
            "task_input": self.task_input,
            "runtime_policy": self.runtime_policy.to_dict(),
            "memory": asdict(self.memory),
            "trigger_context": self.trigger_context,
            "prompt_source": self.prompt_source,
            "timeout_override": self.timeout_override,
            "created_at": self.created_at,
        }
        if self.schema_version >= 4:
            payload["private_prompts"] = [asdict(item) for item in self.private_prompts]
        if self.writable_agents is not None:
            payload["writable_agents"] = list(self.writable_agents)
        return payload

    def immutable_digest(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(b"agency-job-authority:v1\0" + payload).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobSpec":
        values = dict(data)
        values["integration_config"] = dict(values.get("integration_config") or {})
        values["blueprint"] = BlueprintRef(**values["blueprint"])
        runtime_policy_data = dict(values["runtime_policy"])
        if "sandbox_mode" in runtime_policy_data:
            # Old format: convert to new permission-rule shape.
            values["runtime_policy"] = RuntimePolicySnapshot(
                timeout=runtime_policy_data["timeout"],
                mode=runtime_policy_data["sandbox_mode"],
            )
        else:
            values["runtime_policy"] = RuntimePolicySnapshot(
                timeout=runtime_policy_data["timeout"],
                mode=runtime_policy_data.get("mode", "unrestricted"),
                rules=tuple(runtime_policy_data.get("rules") or ()),
            )
        values["memory"] = MemoryBinding(**values["memory"])
        values["skill_arguments"] = tuple(values.get("skill_arguments") or ())
        values["private_prompts"] = tuple(
            PromptSnapshot(**item) for item in (values.get("private_prompts") or ())
        )
        writable_agents = values.get("writable_agents")
        values["writable_agents"] = (
            None if writable_agents is None else tuple(writable_agents)
        )
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
    def resolved_workspace_root(self) -> Path:
        return Path(self.workspace_root).resolve(strict=False)

    @property
    def resolved_group_root(self) -> Path:
        return Path(self.group_root).resolve(strict=False)


@dataclass
class JobRecord:
    spec: JobSpec
    authority_digest: str = ""
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
    session_id: str | None = None
    due_at: str | None = None
    launched_at: str | None = None

    @classmethod
    def from_spec(cls, spec: JobSpec, *, due_at: str | None = None) -> "JobRecord":
        spec.validate()
        return cls(spec=spec, authority_digest=spec.immutable_digest(), due_at=due_at)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spec"] = self.spec.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        values = dict(data)
        spec = JobSpec.from_dict(dict(values.pop("spec")))
        record = cls(spec=spec, **values)
        if record.authority_digest != spec.immutable_digest():
            raise ValueError("immutable job authority digest mismatch")
        if record.status not in VALID_STATUSES:
            raise ValueError(f"Invalid job status: {record.status}")
        return record


@dataclass(frozen=True)
class JobHandle:
    job_id: str
    status: str
    path: Path
    worker_pid: int | None
