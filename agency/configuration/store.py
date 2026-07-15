from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

import yaml

from agency.fs.atomic import atomic_write_bytes
from agency.fs.locks import exclusive_lock

from .models import AgencyConfigcanonical, parse_config_canonical


ABSENT_REVISION = "absent"


def config_revision(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ConfigSnapshot:
    path: Path
    revision: str
    raw: dict[str, Any]
    config: AgencyConfigcanonical


@dataclass(frozen=True)
class ConfigFileSnapshot:
    path: Path
    exists: bool
    revision: str
    payload: bytes | None


class ConfigConflictError(RuntimeError):
    pass


def _load_raw_mapping(payload: bytes) -> dict[str, Any]:
    loaded = yaml.safe_load(payload.decode("utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise TypeError("config.yaml must decode to a mapping")
    return loaded


def load_config_snapshot(
    path: Path, *, wait_for_lock: bool = True
) -> ConfigSnapshot:
    return ConfigStore(path).load(wait_for_lock=wait_for_lock)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    def load(self, *, wait_for_lock: bool = True) -> ConfigSnapshot:
        with exclusive_lock(self.lock_path, wait=wait_for_lock):
            payload = self.path.read_bytes()
        return self._snapshot(payload)

    def inspect(self, *, wait_for_lock: bool = True) -> ConfigFileSnapshot:
        with exclusive_lock(self.lock_path, wait=wait_for_lock):
            if not self.path.exists():
                return ConfigFileSnapshot(
                    path=self.path,
                    exists=False,
                    revision=ABSENT_REVISION,
                    payload=None,
                )
            payload = self.path.read_bytes()
        return ConfigFileSnapshot(
            path=self.path,
            exists=True,
            revision=config_revision(payload),
            payload=payload,
        )

    def create(self, raw: dict[str, Any]) -> ConfigSnapshot:
        with exclusive_lock(self.lock_path, wait=True):
            if self.path.exists():
                raise FileExistsError(self.path)
            payload = self._encode(raw)
            atomic_write_bytes(self.path, payload)
        return self._snapshot(payload)

    def replace(
        self,
        expected_revision: str,
        raw: dict[str, Any],
    ) -> ConfigSnapshot:
        with exclusive_lock(self.lock_path, wait=True):
            original = self.path.read_bytes() if self.path.exists() else None
            current_revision = (
                config_revision(original)
                if original is not None
                else ABSENT_REVISION
            )
            if current_revision != expected_revision:
                raise ConfigConflictError(
                    "config.yaml changed; reload before saving"
                )
            updated = self._encode(raw)
            current = self.path.read_bytes() if self.path.exists() else None
            if current != original:
                raise ConfigConflictError(
                    "config.yaml changed outside the Agency lock"
                )
            atomic_write_bytes(self.path, updated)
        return self._snapshot(updated)

    def patch(
        self,
        expected_revision: str,
        patcher: Callable[[dict[str, Any]], None],
    ) -> ConfigSnapshot:
        with exclusive_lock(self.lock_path, wait=True):
            original = self.path.read_bytes()
            if config_revision(original) != expected_revision:
                raise ConfigConflictError(
                    "config.yaml changed; reload before saving"
                )
            raw = deepcopy(_load_raw_mapping(original))
            patcher(raw)
            updated = self._encode(raw)
            if self.path.read_bytes() != original:
                raise ConfigConflictError(
                    "config.yaml changed outside the Agency lock"
                )
            atomic_write_bytes(self.path, updated)
        return self._snapshot(updated)

    def _snapshot(self, payload: bytes) -> ConfigSnapshot:
        raw = _load_raw_mapping(payload)
        parsed = parse_config_canonical(raw, self.path)
        return ConfigSnapshot(
            path=self.path,
            revision=config_revision(payload),
            raw=raw,
            config=parsed.resolved,
        )

    def _encode(self, raw: dict[str, Any]) -> bytes:
        parse_config_canonical(raw, self.path)
        return yaml.safe_dump(
            raw, sort_keys=False, allow_unicode=True
        ).encode("utf-8")
