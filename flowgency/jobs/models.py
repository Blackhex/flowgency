"""Versioned data models for durable agent jobs."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from flowgency.blueprints.cache import CacheRef, CompiledArtifact
from flowgency.configuration.models import MemorySelector, PromptSelector
from flowgency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule
from flowgency.tickets.models import StorageBinding, TicketRef


SCHEMA_VERSION = 6
SUPPORTED_SCHEMA_VERSIONS = frozenset({5, 6})
VALID_TRIGGERS = {
    "scheduled_prompt",
    "manual_prompt",
    "decision",
    "decision_retry",
    "ticket",
}
VALID_STATUSES = {
    "queued",
    "waiting_for_memory",
    "running",
    "complete",
    "failed",
    "cancelled",
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


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
            self.instance_digest,
        )

    @property
    def cache_entry_path(self) -> Path:
        return Path(self.cache_path)

    @property
    def cache_root(self) -> Path:
        return self.cache_entry_path.parent.parent.parent.parent

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
class TicketJobTarget:
    binding: StorageBinding
    ref: TicketRef
    assigned_agent: str
    assignment_event_id: str
    context_digest: str

    def __post_init__(self) -> None:
        if self.ref.binding_id != self.binding.binding_id:
            raise ValueError("ticket target ref binding must match its binding")
        if self.ref.team_id != self.binding.team_id:
            raise ValueError("ticket target ref team must match its binding")
        if self.ref.workflow_id != self.binding.workflow_id:
            raise ValueError("ticket target ref workflow must match its binding")
        if not self.assigned_agent.strip():
            raise ValueError("ticket target assigned_agent must not be blank")
        if not self.assignment_event_id.strip():
            raise ValueError("ticket target assignment_event_id must not be blank")
        if not _DIGEST.fullmatch(self.context_digest):
            raise ValueError("ticket target context_digest must be a SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding": self.binding.model_dump(mode="json", exclude={"binding_id"}),
            "ref": self.ref.model_dump(mode="json"),
            "assigned_agent": self.assigned_agent,
            "assignment_event_id": self.assignment_event_id,
            "context_digest": self.context_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TicketJobTarget":
        return cls(
            binding=StorageBinding.model_validate(data["binding"]),
            ref=TicketRef.model_validate(data["ref"]),
            assigned_agent=data["assigned_agent"],
            assignment_event_id=data["assignment_event_id"],
            context_digest=data["context_digest"],
        )


@dataclass(frozen=True)
class JobRequest:
    config_path: Path
    team_key: str
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
    ticket_target: TicketJobTarget | None = None

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
    team_key: str
    workspace_root: str
    team_root: str
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
    ticket_target: TicketJobTarget | None = None

    def validate(self) -> None:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported job schema version: {self.schema_version}")
        string_fields = {
            "job_id": self.job_id,
            "config_revision": self.config_revision,
            "team_key": self.team_key,
            "workspace_root": self.workspace_root,
            "team_root": self.team_root,
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
        if not self.team_key.strip():
            raise ValueError("Team key is required")
        if not self.agent_name.strip():
            raise ValueError("Agent name is required")
        if not self.task_input.strip():
            raise ValueError("Prompt content must not be blank")
        self._validate_trigger_contract()

    def _validate_trigger_contract(self) -> None:
        if self.skill is not None:
            raise ValueError("durable jobs must not set skill")
        if self.skill_arguments != ():
            raise ValueError("durable jobs must keep skill_arguments empty")
        if self.schema_version == 5 and self.ticket_target is not None:
            raise ValueError("schema v5 jobs must not set ticket_target")
        if self.trigger != "ticket" and self.ticket_target is not None:
            raise ValueError("non-ticket jobs must not set ticket_target")
        if self.trigger in {"scheduled_prompt", "manual_prompt"}:
            if self.prompt_source is None:
                raise ValueError("prompt-backed jobs require a prompt_source")
            if self.trigger == "scheduled_prompt" and not self.routine_id:
                raise ValueError("scheduled prompt jobs require routine_id")
        if self.trigger in {"decision", "decision_retry"} and self.routine_id is not None:
            raise ValueError("decision jobs require routine_id to be null")
        if self.trigger == "ticket":
            if self.schema_version < 6:
                raise ValueError("ticket jobs require schema version 6")
            if self.ticket_target is None:
                raise ValueError("ticket jobs require ticket_target")
            if self.prompt_source is None or self.prompt_source.get("type") != "ticket":
                raise ValueError("ticket jobs require a ticket prompt_source")
            if self.ticket_target.ref.team_id != self.team_key:
                raise ValueError("ticket jobs must target the same team as the enclosing job")
            if self.ticket_target.assigned_agent != self.agent_name:
                raise ValueError("ticket jobs must target the assigned agent of the enclosing job")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "config_path": self.config_path,
            "config_revision": self.config_revision,
            "team_key": self.team_key,
            "workspace_root": self.workspace_root,
            "team_root": self.team_root,
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
            "private_prompts": [asdict(item) for item in self.private_prompts],
        }
        if self.writable_agents is not None:
            payload["writable_agents"] = list(self.writable_agents)
        if self.schema_version >= 6:
            payload["ticket_target"] = (
                None if self.ticket_target is None else self.ticket_target.to_dict()
            )
        return payload

    def immutable_digest(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(b"flowgency-job-authority:v1\0" + payload).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobSpec":
        values = dict(data)
        values["integration_config"] = dict(values.get("integration_config") or {})
        values["blueprint"] = BlueprintRef(**values["blueprint"])
        runtime_policy_data = dict(values["runtime_policy"])
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
        ticket_target = values.get("ticket_target")
        values["ticket_target"] = (
            None if ticket_target is None else TicketJobTarget.from_dict(ticket_target)
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
    def resolved_team_root(self) -> Path:
        return Path(self.team_root).resolve(strict=False)


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
    copilot_home: str | None = None
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
