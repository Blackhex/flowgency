from __future__ import annotations

from datetime import datetime, timezone

import pytest

from flowgency.tickets.errors import OperationConflict, TicketConflict
from flowgency.tickets.models import TicketOperation, TicketRef
from flowgency.tickets.storages import local
from tests._ticket_helpers import storage_binding, system_event, ticket_record

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


@pytest.fixture(params=["local"])
def provider_env(request, tmp_path):
    """Provider factory. Local is the only factory initially."""
    if request.param == "local":
        return local.LocalTicketStorage(tmp_path, clock=lambda: NOW), tmp_path
    raise AssertionError(f"unknown provider {request.param!r}")


@pytest.fixture
def provider(provider_env):
    return provider_env[0]


@pytest.fixture
def root(provider_env):
    return provider_env[1]


@pytest.fixture
def ref(provider, root):
    binding = storage_binding(root)
    original = ticket_record()
    scoped = TicketRef.from_binding(binding, original.id)
    provider.create(original.with_ref(scoped), TicketOperation("seed-op", "seed-digest"))
    return scoped


def test_receipt_replay_precedes_revision_check(provider, ref):
    before = provider.read(ref)
    operation = TicketOperation("rename-a", "rename-digest")
    first = provider.apply(
        ref,
        before.revision,
        operation,
        lambda ticket: ticket.model_copy(update={"title": "Renamed"}),
    )
    replay = provider.apply(
        ref,
        before.revision,
        operation,
        lambda ticket: pytest.fail("must not execute twice"),
    )
    assert replay.replayed is True
    assert replay.ticket == first.ticket
    assert len(provider.read(ref).events) == len(first.ticket.events)


def test_stale_revision_raises_conflict(provider, ref):
    before = provider.read(ref)
    with pytest.raises(TicketConflict):
        provider.apply(
            ref,
            before.revision + 5,
            TicketOperation("op-stale", "digest-stale"),
            lambda ticket: ticket.model_copy(update={"title": "Nope"}),
        )


def test_conflicting_receipt_digest_raises(provider, ref):
    before = provider.read(ref)
    provider.apply(
        ref,
        before.revision,
        TicketOperation("op-x", "digest-x"),
        lambda ticket: ticket.model_copy(update={"title": "X"}),
    )
    with pytest.raises(OperationConflict):
        provider.apply(
            ref,
            before.revision,
            TicketOperation("op-x", "digest-y"),
            lambda ticket: ticket.model_copy(update={"title": "Y"}),
        )


def test_receipt_lookup_returns_prior_result(provider, ref):
    before = provider.read(ref)
    operation = TicketOperation("op-r", "digest-r")
    assert provider.receipt(ref, operation) is None
    first = provider.apply(
        ref,
        before.revision,
        operation,
        lambda ticket: ticket.model_copy(update={"title": "Looked up"}),
    )
    found = provider.receipt(ref, operation)
    assert found is not None
    assert found.replayed is True
    assert found.ticket == first.ticket


def test_duplicate_create_returns_replay(provider, root):
    binding = storage_binding(root)
    record = ticket_record(ticket_id="ticket-dup")
    scoped = TicketRef.from_binding(binding, record.id)
    operation = TicketOperation("create-dup", "digest-dup")
    first = provider.create(record.with_ref(scoped), operation)
    replay = provider.create(record.with_ref(scoped), operation)
    assert replay.replayed is True
    assert replay.ticket == first.ticket
    assert len(provider.list("team-a", "board-a")) == 1


def test_duplicate_create_conflicting_digest_raises(provider, root):
    binding = storage_binding(root)
    record = ticket_record(ticket_id="ticket-dup")
    scoped = TicketRef.from_binding(binding, record.id)
    provider.create(record.with_ref(scoped), TicketOperation("create-dup", "digest-a"))
    with pytest.raises(OperationConflict):
        provider.create(
            record.with_ref(scoped), TicketOperation("create-dup", "digest-b")
        )


def test_list_orders_by_number(provider, root):
    binding = storage_binding(root)
    for ticket_id in ("ticket-b", "ticket-a", "ticket-c"):
        record = ticket_record(ticket_id=ticket_id)
        scoped = TicketRef.from_binding(binding, record.id)
        provider.create(
            record.with_ref(scoped), TicketOperation(f"c-{ticket_id}", f"d-{ticket_id}")
        )
    numbers = [record.number for record in provider.list("team-a", "board-a")]
    assert numbers == [1, 2, 3]


def test_same_local_number_across_namespaces(provider, root):
    board_a = storage_binding(root, workflow_id="board-a")
    board_b = storage_binding(root, workflow_id="board-b")
    ref_a = TicketRef.from_binding(board_a, "ticket-a")
    ref_b = TicketRef.from_binding(board_b, "ticket-a")
    first_a = provider.create(
        ticket_record().with_ref(ref_a), TicketOperation("ca", "da")
    )
    first_b = provider.create(
        ticket_record().with_ref(ref_b), TicketOperation("cb", "db")
    )
    assert first_a.ticket.number == 1
    assert first_b.ticket.number == 1


def test_create_finalizes_opening_event_and_reports_id(provider, root):
    binding = storage_binding(root)
    record = ticket_record(ticket_id="ticket-evt")
    scoped = TicketRef.from_binding(binding, record.id)
    result = provider.create(record.with_ref(scoped), TicketOperation("c-evt", "d-evt"))
    assert result.event_id
    stored = provider.read(scoped)
    assert stored.events[-1].id == result.event_id
    assert stored.events[-1].at is not None


def test_apply_appends_single_event_and_preserves_history(provider, ref):
    before = provider.read(ref)
    prior_ids = [event.id for event in before.events]
    assert prior_ids and all(prior_ids)

    def mutate(ticket):
        return ticket.model_copy(
            update={"events": ticket.events + (system_event(kind="moved", summary="Moved"),)}
        )

    result = provider.apply(ref, before.revision, TicketOperation("op-move", "d-move"), mutate)
    after = provider.read(ref)
    assert [event.id for event in after.events[: len(prior_ids)]] == prior_ids
    assert len(after.events) == len(prior_ids) + 1
    assert after.events[-1].id == result.event_id

    replay = provider.apply(
        ref,
        before.revision,
        TicketOperation("op-move", "d-move"),
        lambda ticket: pytest.fail("must not execute twice"),
    )
    assert replay.replayed is True
    assert replay.event_id == result.event_id


def test_apply_dropping_prior_events_is_rejected(provider, ref):
    before = provider.read(ref)
    with pytest.raises(TicketConflict):
        provider.apply(
            ref,
            before.revision,
            TicketOperation("op-drop", "d-drop"),
            lambda ticket: ticket.model_copy(update={"events": ()}),
        )
    assert provider.read(ref).events == before.events
