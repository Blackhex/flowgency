"""Resolve a storage binding to its provider. Local is the only kind now."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flowgency.tickets.errors import StorageUnavailable
from flowgency.tickets.models import Clock, StorageBinding
from flowgency.tickets.storages.base import TicketStorage
from flowgency.tickets.storages.local import LocalTicketStorage


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def resolve_storage(
    binding: StorageBinding, *, clock: Clock | None = None
) -> TicketStorage:
    if binding.kind == "local":
        return LocalTicketStorage(Path(binding.root), clock=clock or _default_clock)
    raise StorageUnavailable(
        "unknown-provider", "No storage provider for this binding"
    )
