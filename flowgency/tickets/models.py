"""Ticket storage domain models – pure data, consuming Task 1 field contracts.

These types validate every provider read and copy at the write boundary; a
provider persists only what these models can round-trip. The receipt ledger
records an original-result snapshot for idempotent replay, and that snapshot
deliberately excludes the ledger itself so history cannot grow recursively.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    computed_field,
    model_validator,
)

from flowgency.tickets.errors import OperationConflict, TicketConflict
from flowgency.workflows.models import CriterionAssessment, FieldValue, WorkflowDefinition


def _canonical_config(integration: str, config: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical, comparable config for a storage integration.

    Local roots are resolved and case-normalized so two spellings of the same
    directory yield the same binding identity.
    """
    data = dict(config)
    if integration == "local":
        root = data.get("root")
        if root is None:
            raise ValueError("Local storage config requires a root")
        data["root"] = os.path.normcase(str(Path(root).resolve(strict=False)))
    return data


def _binding_digest(
    integration: str, config: dict[str, Any], team_id: str, workflow_id: str
) -> str:
    payload = json.dumps(
        {
            "integration": integration,
            "config": _canonical_config(integration, config),
            "team_id": team_id,
            "workflow_id": workflow_id,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TicketRef(BaseModel):
    """Fully scoped reference: physical binding, team, board, and ticket id."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    binding_id: StrictStr
    team_id: StrictStr
    workflow_id: StrictStr
    ticket_id: StrictStr

    @classmethod
    def from_binding(cls, binding: "StorageBinding", ticket_id: str) -> "TicketRef":
        return cls(
            binding_id=binding.binding_id,
            team_id=binding.team_id,
            workflow_id=binding.workflow_id,
            ticket_id=ticket_id,
        )


class StorageBinding(BaseModel):
    """Provider envelope: integration, canonical config, namespace, binding id.

    The binding carries the board namespace and its physical binding identity,
    never a blueprint pin.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    integration: StrictStr
    config: dict[str, Any]
    team_id: StrictStr
    workflow_id: StrictStr

    @model_validator(mode="before")
    @classmethod
    def _canonicalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "integration" in data and "config" in data:
            data = dict(data)
            data["config"] = _canonical_config(data["integration"], data["config"])
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def binding_id(self) -> str:
        return _binding_digest(
            self.integration, self.config, self.team_id, self.workflow_id
        )

    @staticmethod
    def compute_binding_id(
        integration: str, config: dict[str, Any], team_id: str, workflow_id: str
    ) -> str:
        return _binding_digest(integration, config, team_id, workflow_id)


class TicketEvent(BaseModel):
    """A single trusted audit event supplied by the service mutation callback."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: StrictStr = ""
    kind: StrictStr
    actor: StrictStr
    summary: StrictStr
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime | None = None


class ActiveTicketRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: StrictStr
    session_id: StrictStr
    started_at: datetime

    @property
    def generation(self) -> str:
        return self.session_id


class FieldProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    actor_kind: Literal["user", "agent"]
    actor_name: StrictStr
    job_id: StrictStr | None = None
    event_id: StrictStr
    recorded_at: datetime


class AgentTicketContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: StrictStr
    team_id: StrictStr
    agent_name: StrictStr
    session_id: StrictStr


class TicketAccessGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: StrictStr
    token: StrictStr
    context: AgentTicketContext


class LiveTicketEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: StrictStr
    grant: TicketAccessGrant


class TicketToolError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: StrictStr
    message: StrictStr
    details: dict[str, Any] = Field(default_factory=dict)


class TicketToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ok: bool
    result: Any = None
    error: TicketToolError | None = None


class UserTicketContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    team_id: StrictStr
    actor_name: StrictStr = "local-user"


TicketActor = AgentTicketContext | UserTicketContext


class TicketVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: TicketRef
    revision: StrictInt
    workflow_digest: StrictStr
    context_digest: StrictStr


class TicketPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: StrictStr | None = None
    description: StrictStr | None = None
    field_values: dict[str, FieldValue] | None = None

    @model_validator(mode="after")
    def _validate_non_empty(self) -> "TicketPatch":
        if (
            self.title is None
            and self.description is None
            and self.field_values is None
        ):
            raise ValueError("Ticket patch must change at least one field")
        return self


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    transition_id: StrictStr
    inputs: dict[str, FieldValue] = Field(default_factory=dict)
    outputs: dict[str, FieldValue] = Field(default_factory=dict)
    assessments: tuple[CriterionAssessment, ...] = ()


class TicketReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message: StrictStr
    assessments: tuple[CriterionAssessment, ...] = ()

    @model_validator(mode="after")
    def _validate_message(self) -> "TicketReport":
        if not self.message.strip():
            raise ValueError("Ticket report message must not be blank")
        return self


class TicketView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record: "TicketRecord"
    version: TicketVersion | None
    definition: WorkflowDefinition | None
    issues: tuple[StrictStr, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ref(self) -> TicketRef:
        if self.record.ref is None:
            raise ValueError("Ticket view requires a scoped record")
        return self.record.ref

    def patch(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        field_values: dict[str, FieldValue] | None = None,
    ) -> TicketPatch:
        return TicketPatch(
            title=title,
            description=description,
            field_values=field_values,
        )


class TicketMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ticket: "TicketRecord"
    replayed: bool = False
    event_id: StrictStr = ""


class TicketReceipt(BaseModel):
    """Ledger entry proving an operation already ran, with its original result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: StrictStr
    request_digest: StrictStr
    at: datetime
    event_id: StrictStr = ""
    snapshot: dict[str, Any]

    def require_digest(self, request_digest: str) -> None:
        if self.request_digest != request_digest:
            raise OperationConflict(
                "operation-digest-mismatch",
                "Reused operation id with different content",
                operation_id=self.operation_id,
            )

    def result(self, replayed: bool) -> "TicketMutationResult":
        return TicketMutationResult(
            ticket=TicketRecord.model_validate(self.snapshot),
            replayed=replayed,
            event_id=self.event_id,
        )


class StorageHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["ok", "missing", "unreadable"]
    detail: StrictStr = ""


@dataclass(frozen=True)
class TicketOperation:
    """Actor-scoped operation identity. Digest covers actor, kind, target, payload."""

    operation_id: str
    request_digest: str


class TicketRecord(BaseModel):
    """The durable ticket. The same record persists throughout its workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: StrictStr
    number: StrictInt
    title: StrictStr
    description: StrictStr
    state_id: StrictStr
    assignee: StrictStr | None = None
    active_run: ActiveTicketRun | None = None
    field_values: dict[str, FieldValue] = Field(default_factory=dict)
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    revision: StrictInt
    events: tuple[TicketEvent, ...] = ()
    receipts: tuple[TicketReceipt, ...] = ()
    created_at: datetime
    updated_at: datetime
    ref: TicketRef | None = None

    def with_ref(self, ref: TicketRef) -> "TicketRecord":
        """Return a revalidated, independent copy scoped to ref.

        The copy round-trips through validation so it is a genuine standalone
        record, not a shared-structure ``model_copy``.
        """
        if ref.ticket_id != self.id:
            raise ValueError("ref ticket_id must match record id")
        data = self.model_dump()
        data["ref"] = ref.model_dump()
        return TicketRecord.model_validate(data)

    def find_receipt(self, operation_id: str) -> TicketReceipt | None:
        for receipt in self.receipts:
            if receipt.operation_id == operation_id:
                return receipt
        return None

    def commit_creation(
        self, operation: TicketOperation, now: datetime
    ) -> tuple["TicketRecord", TicketMutationResult]:
        """Finalize the opening event supplied on creation and seal the receipt."""
        events, event_id = _finalize_events((), self.events, now)
        opened = self.model_copy(update={"events": events})
        return seal_operation(opened, operation, now, event_id)

    def commit_operation(
        self,
        current: "TicketRecord",
        operation: TicketOperation,
        now: datetime,
    ) -> tuple["TicketRecord", TicketMutationResult]:
        """Advance revision, stamp time, finalize the supplied event, seal receipt.

        This does not append a second domain event: it only finalizes the one the
        service callback already placed on the mutated record, and it rejects any
        mutation that drops or rewrites prior event history.
        """
        events, event_id = _finalize_events(current.events, self.events, now)
        updated = self.model_copy(
            update={
                "revision": current.revision + 1,
                "updated_at": now,
                "events": events,
            }
        )
        return seal_operation(updated, operation, now, event_id)


def _finalize_events(
    current_events: tuple[TicketEvent, ...],
    candidate_events: tuple[TicketEvent, ...],
    now: datetime,
) -> tuple[tuple[TicketEvent, ...], str]:
    """Preserve prior events and finalize newly supplied ones.

    Returns the finalized event tuple and the accepted event id (the last event
    after finalization). Raises if the mutation dropped or altered prior events.
    """
    prefix = candidate_events[: len(current_events)]
    if prefix != current_events:
        raise TicketConflict(
            "event-history-changed",
            "Prior ticket events must not be dropped or altered",
        )
    finalized = list(current_events)
    for event in candidate_events[len(current_events) :]:
        finalized.append(
            event.model_copy(
                update={
                    "id": event.id or uuid.uuid4().hex,
                    "at": event.at or now,
                }
            )
        )
    accepted_event_id = finalized[-1].id if finalized else ""
    return tuple(finalized), accepted_event_id


def seal_operation(
    record: TicketRecord, operation: TicketOperation, now: datetime, event_id: str = ""
) -> tuple[TicketRecord, TicketMutationResult]:
    """Attach the operation receipt and return the original-result snapshot."""
    snapshot = record.model_dump(mode="json")
    snapshot["receipts"] = []
    receipt = TicketReceipt(
        operation_id=operation.operation_id,
        request_digest=operation.request_digest,
        at=now,
        event_id=event_id,
        snapshot=snapshot,
    )
    sealed = record.model_copy(update={"receipts": record.receipts + (receipt,)})
    result = TicketMutationResult(
        ticket=TicketRecord.model_validate(snapshot),
        replayed=False,
        event_id=event_id,
    )
    return sealed, result


TicketMutationResult.model_rebuild()
TicketReceipt.model_rebuild()
TicketRecord.model_rebuild()
TicketView.model_rebuild()
TransitionRequest.model_rebuild()
TicketReport.model_rebuild()


Clock = Callable[[], datetime]
