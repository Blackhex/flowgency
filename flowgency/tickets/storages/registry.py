"""Resolve a storage binding to its provider. Local is the only kind now."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flowgency.tickets.errors import StorageUnavailable
from flowgency.tickets.models import Clock, StorageBinding
from flowgency.tickets.storages.base import TicketStorage
from flowgency.tickets.storages.local import LocalTicketStorage

KNOWN_STORAGE_INTEGRATIONS: tuple[str, ...] = ("local",)
_LOCAL_CONFIG_KEYS = frozenset({"root"})


def validate_storage_config(integration: str, config: Any) -> tuple[str, ...]:
    """Return provider-config error messages; empty when the config is valid.

    Local accepts only a required ``root`` key; unknown provider keys and
    unknown providers are rejected so a workflow cannot smuggle configuration a
    provider will never honour.
    """
    if integration not in KNOWN_STORAGE_INTEGRATIONS:
        return (f"Unknown storage provider: {integration}",)
    if not isinstance(config, Mapping):
        return (f"{integration} storage config must be a mapping.",)
    errors: list[str] = []
    root = config.get("root")
    if not isinstance(root, str) or not root.strip():
        errors.append("Local storage requires a non-empty root.")
    unknown = sorted(set(config) - _LOCAL_CONFIG_KEYS)
    if unknown:
        errors.append(
            "Unknown local storage config keys: " + ", ".join(unknown) + "."
        )
    return tuple(errors)


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def resolve_storage(
    binding: StorageBinding, *, clock: Clock | None = None
) -> TicketStorage:
    if binding.integration == "local":
        return LocalTicketStorage(
            Path(binding.config["root"]), clock=clock or _default_clock
        )
    raise StorageUnavailable(
        "unknown-provider", "No storage provider for this binding"
    )
