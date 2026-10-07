"""Workspace identity and read-only source metadata for the live workspace pages.

Nothing here reads file content: a live snapshot only ever stats an allowed file.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat as stat_module
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_IDENTITY_PATTERN = re.compile(r"[0-9a-f]{64}")
_MISSING_REVISION = hashlib.sha256(b"missing").hexdigest()


def workspace_identity(workspace: Mapping[str, Any]) -> str:
    """SHA-256 hex of the canonical JSON of a validated workspace configuration."""
    canonical = json.dumps(workspace, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def parse_loaded_identity(value: str | None) -> str:
    """The identity a page loaded with; anything but 64 lowercase hex digits is refused."""
    if value is None or _IDENTITY_PATTERN.fullmatch(value) is None:
        raise ValueError("A workspace identity is 64 lowercase hexadecimal digits")
    return value


def workspace_row_keys(workspaces: Sequence[Mapping[str, Any]]) -> list[str]:
    """Stable per-row keys; identical configurations are told apart by occurrence."""
    seen: dict[str, int] = {}
    keys: list[str] = []
    for workspace in workspaces:
        identity = workspace_identity(workspace)
        seen[identity] = seen.get(identity, 0) + 1
        keys.append(identity if seen[identity] == 1 else f"{identity}-{seen[identity]}")
    return keys


@dataclass(frozen=True)
class SourceMetadata:
    available: bool
    size: int | None
    modified: str | None
    revision: str


_UNAVAILABLE = SourceMetadata(available=False, size=None, modified=None, revision=_MISSING_REVISION)


def source_metadata(path: str) -> SourceMetadata:
    """Size, modification time and a revision for an allowed file, without reading it."""
    try:
        status = Path(path).stat()
    except (OSError, ValueError):
        return _UNAVAILABLE
    if not stat_module.S_ISREG(status.st_mode):
        return _UNAVAILABLE
    modified = datetime.fromtimestamp(status.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    revision = hashlib.sha256(f"{status.st_size}:{status.st_mtime_ns}".encode("ascii")).hexdigest()
    return SourceMetadata(available=True, size=status.st_size, modified=modified, revision=revision)
