"""The ticket storage port. Local is the only adapter shipped now."""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from flowgency.tickets.models import (
    StorageHealth,
    TicketMutationResult,
    TicketOperation,
    TicketRecord,
    TicketRef,
)
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.workflows.models import ArtifactRef


@runtime_checkable
class TicketStorage(Protocol):
    def create(
        self, record: TicketRecord, operation: TicketOperation
    ) -> TicketMutationResult: ...

    def read(self, ref: TicketRef) -> TicketRecord: ...

    def list(
        self, team_id: str, workflow_id: str
    ) -> tuple[TicketRecord, ...]: ...

    def apply(
        self,
        ref: TicketRef,
        expected_revision: int,
        operation: TicketOperation,
        mutate: Callable[[TicketRecord], TicketRecord],
    ) -> TicketMutationResult: ...

    def receipt(
        self, ref: TicketRef, operation: TicketOperation
    ) -> TicketMutationResult | None: ...

    def put_artifact(
        self, namespace: TicketRef, artifact: RetainedArtifact
    ) -> ArtifactRef: ...

    def read_artifact(
        self, namespace: TicketRef, artifact_id: str
    ) -> RetainedArtifact: ...

    def check(self) -> StorageHealth: ...
