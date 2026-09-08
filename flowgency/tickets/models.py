"""Ticket storage domain models – pure data, consuming Task 1 field contracts.

These types validate every provider read and copy at the write boundary; a
provider persists only what these models can round-trip. The receipt ledger
records an original-result snapshot for idempotent replay, and that snapshot
deliberately excludes the ledger itself so history cannot grow recursively.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictInt,
    StrictStr,
)

from flowgency.tickets.errors import OperationConflict
from flowgency.workflows.models import FieldValue


class TicketRef(BaseModel):
    """Fully scoped reference: team namespace, board namespace, ticket id."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    team_id: StrictStr
    workflow_id: StrictStr
    ticket_id: StrictStr

    @classmethod
    def from_binding(cls, binding: "StorageBinding", ticket_id: str) -> "TicketRef":
        return cls(
            team_id=binding.team_id,
            workflow_id=binding.workflow_id,
            ticket_id=ticket_id,
        )


class StorageBinding(BaseModel):
    """Provider envelope. Carries the board namespace, never a blueprint pin."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["local"]
    root: str
    team_id: StrictStr
    workflow_id: StrictStr

    def __init__(self, **data: Any) -> None:  # accept Path for root
        if "root" in data:
            data["root"] = str(data["root"])
        super().__init__(**data)


class TicketEvent(BaseModel):
    """A single trusted audit event supplied by the service mutation callback."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: StrictStr = ""
    kind: StrictStr
    actor: StrictStr
    summary: StrictStr
    at: datetime | None = None


class TicketMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ticket: "TicketRecord"
    replayed: bool = False


class TicketReceipt(BaseModel):
    """Ledger entry proving an operation already ran, with its original result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: StrictStr
    request_digest: StrictStr
    at: datetime
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
    active_run: StrictStr | None = None
    field_values: dict[str, FieldValue] = {}
    field_provenance: dict[str, StrictStr] = {}
    revision: StrictInt
    events: tuple[TicketEvent, ...] = ()
    receipts: tuple[TicketReceipt, ...] = ()
    created_at: datetime
    updated_at: datetime
    ref: TicketRef | None = None

    def with_ref(self, ref: TicketRef) -> "TicketRecord":
        """Return a validated copy scoped to ref. Not a persistent mutation."""
        if ref.ticket_id != self.id:
            raise ValueError("ref ticket_id must match record id")
        return self.model_copy(update={"ref": ref})

    def find_receipt(self, operation_id: str) -> TicketReceipt | None:
        for receipt in self.receipts:
            if receipt.operation_id == operation_id:
                return receipt
        return None

    def commit_operation(
        self,
        current: "TicketRecord",
        operation: TicketOperation,
        now: datetime,
    ) -> tuple["TicketRecord", TicketMutationResult]:
        """Advance revision, stamp time, finalize the supplied event, seal receipt.

        This does not append a second domain event: it only finalizes the one the
        service callback already placed on the mutated record.
        """
        updated = self.model_copy(
            update={
                "revision": current.revision + 1,
                "updated_at": now,
                "events": _finalize_events(current, self, now),
            }
        )
        return seal_operation(updated, operation, now)


def _finalize_events(
    current: TicketRecord, candidate: TicketRecord, now: datetime
) -> tuple[TicketEvent, ...]:
    carried = candidate.events[: len(current.events)]
    finalized = list(carried)
    for event in candidate.events[len(current.events) :]:
        finalized.append(
            event.model_copy(
                update={
                    "id": event.id or uuid.uuid4().hex,
                    "at": event.at or now,
                }
            )
        )
    return tuple(finalized)


def seal_operation(
    record: TicketRecord, operation: TicketOperation, now: datetime
) -> tuple[TicketRecord, TicketMutationResult]:
    """Attach the operation receipt and return the original-result snapshot."""
    snapshot = record.model_dump(mode="json")
    snapshot["receipts"] = []
    receipt = TicketReceipt(
        operation_id=operation.operation_id,
        request_digest=operation.request_digest,
        at=now,
        snapshot=snapshot,
    )
    sealed = record.model_copy(update={"receipts": record.receipts + (receipt,)})
    result = TicketMutationResult(
        ticket=TicketRecord.model_validate(snapshot),
        replayed=False,
    )
    return sealed, result


TicketMutationResult.model_rebuild()
TicketReceipt.model_rebuild()
TicketRecord.model_rebuild()


Clock = Callable[[], datetime]
