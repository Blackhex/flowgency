from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flowgency.tickets.models import StorageBinding, TicketRecord
from flowgency.workflows.models import ArtifactRef

SEED_TIME = datetime(2026, 9, 8, tzinfo=timezone.utc)


def ticket_record(
    ticket_id: str = "ticket-a",
    state_id: str = "review",
    agent: str | None = None,
    active_run: str | None = None,
) -> TicketRecord:
    """Build a valid seed record with UTC timestamps and fixed test IDs."""
    return TicketRecord(
        id=ticket_id,
        number=0,
        title="Ticket A",
        description="Body text for ticket A.",
        state_id=state_id,
        assignee=agent,
        active_run=active_run,
        field_values={
            "summary": "hello",
            "spec": ArtifactRef(kind="url", value="https://example.com/spec"),
        },
        field_provenance={"summary": "seed"},
        revision=1,
        events=(),
        receipts=(),
        created_at=SEED_TIME,
        updated_at=SEED_TIME,
    )


def storage_binding(
    root: Path,
    team_id: str = "team-a",
    workflow_id: str = "board-a",
) -> StorageBinding:
    """Provider envelope carrying the board namespace, not a blueprint pin."""
    return StorageBinding(
        kind="local",
        root=Path(root),
        team_id=team_id,
        workflow_id=workflow_id,
    )
