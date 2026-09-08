from __future__ import annotations

import os
from datetime import datetime, timezone
from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest

from flowgency.fs.locks import ResourceBusyError, exclusive_lock
from flowgency.tickets.models import TicketOperation, TicketRef
from flowgency.tickets.storages import local
from tests._ticket_helpers import storage_binding, ticket_record

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _seed(root: Path, ticket_id: str = "ticket-a") -> None:
    provider = local.LocalTicketStorage(root, clock=lambda: NOW)
    binding = storage_binding(root)
    record = ticket_record(ticket_id=ticket_id)
    ref = TicketRef.from_binding(binding, ticket_id)
    provider.create(record.with_ref(ref), TicketOperation(f"seed-{ticket_id}", "seed-d"))


def _apply_worker(root_str: str, ready, go, queue: Queue) -> None:
    from flowgency.tickets.errors import TicketConflict
    from flowgency.tickets.models import TicketOperation, TicketRef
    from flowgency.tickets.storages import local
    from tests._ticket_helpers import storage_binding

    provider = local.LocalTicketStorage(Path(root_str), clock=lambda: NOW)
    binding = storage_binding(Path(root_str))
    ref = TicketRef.from_binding(binding, "ticket-a")
    before = provider.read(ref)
    operation = TicketOperation(f"op-{os.getpid()}", f"digest-{os.getpid()}")
    ready.set()
    go.wait(10)
    try:
        provider.apply(
            ref,
            before.revision,
            operation,
            lambda ticket: ticket.model_copy(update={"title": f"by-{os.getpid()}"}),
        )
        queue.put("ok")
    except TicketConflict:
        queue.put("conflict")


def _create_worker(root_str: str, ticket_id: str, ready, go, queue: Queue) -> None:
    from flowgency.tickets.models import TicketOperation, TicketRef
    from flowgency.tickets.storages import local
    from tests._ticket_helpers import storage_binding, ticket_record

    provider = local.LocalTicketStorage(Path(root_str), clock=lambda: NOW)
    binding = storage_binding(Path(root_str))
    ref = TicketRef.from_binding(binding, ticket_id)
    record = ticket_record(ticket_id=ticket_id)
    ready.set()
    go.wait(10)
    result = provider.create(
        record.with_ref(ref), TicketOperation(f"c-{ticket_id}", f"d-{ticket_id}")
    )
    queue.put(result.ticket.number)


def _hold_ticket_lock(root_str: str, ticket_id: str, acquired, release) -> None:
    from flowgency.fs.locks import exclusive_lock
    from flowgency.tickets.models import TicketRef
    from flowgency.tickets.storages import local
    from tests._ticket_helpers import storage_binding

    provider = local.LocalTicketStorage(Path(root_str), clock=lambda: NOW)
    binding = storage_binding(Path(root_str))
    ref = TicketRef.from_binding(binding, ticket_id)
    with exclusive_lock(provider.lock_path(ref), wait=True):
        acquired.set()
        release.wait(10)


def test_concurrent_apply_yields_one_success_one_conflict(tmp_path):
    _seed(tmp_path)
    ready_one, ready_two, go = Event(), Event(), Event()
    queue: Queue = Queue()
    first = Process(target=_apply_worker, args=(str(tmp_path), ready_one, go, queue))
    second = Process(target=_apply_worker, args=(str(tmp_path), ready_two, go, queue))
    first.start()
    second.start()
    assert ready_one.wait(10)
    assert ready_two.wait(10)
    go.set()
    first.join(30)
    second.join(30)
    assert first.exitcode == 0
    assert second.exitcode == 0
    outcomes = sorted([queue.get(timeout=10), queue.get(timeout=10)])
    assert outcomes == ["conflict", "ok"]


def test_concurrent_creation_yields_unique_numbers(tmp_path):
    ready_one, ready_two, go = Event(), Event(), Event()
    queue: Queue = Queue()
    first = Process(
        target=_create_worker, args=(str(tmp_path), "ticket-a", ready_one, go, queue)
    )
    second = Process(
        target=_create_worker, args=(str(tmp_path), "ticket-b", ready_two, go, queue)
    )
    first.start()
    second.start()
    assert ready_one.wait(10)
    assert ready_two.wait(10)
    go.set()
    first.join(30)
    second.join(30)
    assert first.exitcode == 0
    assert second.exitcode == 0
    numbers = sorted([queue.get(timeout=10), queue.get(timeout=10)])
    assert numbers == [1, 2]


def test_distinct_ticket_locks_are_independent(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    binding = storage_binding(tmp_path)
    held = TicketRef.from_binding(binding, "ticket-a")
    free = TicketRef.from_binding(binding, "ticket-b")
    acquired, release = Event(), Event()
    holder = Process(
        target=_hold_ticket_lock,
        args=(str(tmp_path), "ticket-a", acquired, release),
    )
    holder.start()
    try:
        assert acquired.wait(10)
        with exclusive_lock(provider.lock_path(free), wait=False):
            pass
        with pytest.raises(ResourceBusyError):
            with exclusive_lock(provider.lock_path(held), wait=False):
                pass
    finally:
        release.set()
        holder.join(30)
    assert holder.exitcode == 0
