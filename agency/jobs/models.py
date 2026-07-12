"""Versioned data models for durable agent jobs."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
VALID_TRIGGERS = {
    "scheduled_prompt",
    "manual_prompt",
    "decision",
    "decision_retry",
}
VALID_STATUSES = {"queued", "running", "complete", "failed"}


@dataclass(frozen=True)
class JobSpec:
    schema_version: int
    job_id: str
    config_path: str
    group_key: str
    agent_name: str
    trigger: str
    prompt_source: dict[str, Any]
    prompt_content: str
    timeout_override: int | None
    created_at: str
    decision_context: dict[str, Any] | None

    @classmethod
    def create(
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
    ) -> "JobSpec":
        spec = cls(
            schema_version=SCHEMA_VERSION,
            job_id=uuid4().hex,
            config_path=str(config_path.resolve()),
            group_key=group_key,
            agent_name=agent_name,
            trigger=trigger,
            prompt_source=prompt_source,
            prompt_content=prompt_content,
            timeout_override=timeout_override,
            created_at=datetime.now(timezone.utc).isoformat(),
            decision_context=decision_context,
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported job schema version: {self.schema_version}")
        string_fields = {
            "job_id": self.job_id,
            "group_key": self.group_key,
            "agent_name": self.agent_name,
            "trigger": self.trigger,
            "prompt_content": self.prompt_content,
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
        if not self.prompt_content.strip():
            raise ValueError("Prompt content must not be blank")


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

    @classmethod
    def from_spec(cls, spec: JobSpec) -> "JobRecord":
        spec.validate()
        return cls(spec=spec)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spec"] = asdict(self.spec)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        values = dict(data)
        spec = JobSpec(**values.pop("spec"))
        spec.validate()
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
