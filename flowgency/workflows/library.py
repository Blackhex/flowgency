"""Current-source blueprint inspection and ordered publication.

The library reads ``<root>/<blueprint_id>/workflow.yaml`` on demand. There is no
persisted last-good runtime definition, adoption record, or native-file
conversion: every read hashes and validates the current bytes, and publication
proves the bytes have not changed under it before overwriting them. One invalid
blueprint never erases the rest of the listing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from flowgency.configuration.paths import is_symlink_or_reparse
from flowgency.configuration.store import ConfigConflictError
from flowgency.fs.atomic import atomic_write_text
from flowgency.workflows.models import (
    MAX_BLUEPRINT_SOURCE_BYTES,
    ContractError,
    WorkflowDefinition,
    check_source_size,
)

SOURCE_NAME = "workflow.yaml"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class WorkflowSnapshot:
    definition: WorkflowDefinition
    digest: str
    source_path: Path


@dataclass(frozen=True)
class WorkflowInspection:
    blueprint_id: str
    snapshot: WorkflowSnapshot | None
    issues: tuple[str, ...]


def _source_digest(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


class WorkflowLibrary:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- path safety ------------------------------------------------------

    def _blueprint_dir(self, blueprint_id: str) -> Path:
        if not _SLUG.match(blueprint_id):
            raise ContractError(
                "unsafe-blueprint", f"Blueprint id is not a permitted slug: {blueprint_id!r}"
            )
        root = self.root.resolve(strict=False)
        candidate = (self.root / blueprint_id).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ContractError(
                "unsafe-blueprint", "Blueprint path escapes the library root"
            ) from error
        return self.root / blueprint_id

    def source_path(self, blueprint_id: str) -> Path:
        return self._blueprint_dir(blueprint_id) / SOURCE_NAME

    def _guard_no_reparse(self, blueprint_id: str) -> Path:
        walked = self.root
        for segment in (blueprint_id, SOURCE_NAME):
            walked = walked / segment
            if is_symlink_or_reparse(walked):
                raise ContractError(
                    "unsafe-blueprint",
                    "Blueprint path crosses a link or reparse point",
                )
        return self.source_path(blueprint_id)

    # -- reads ------------------------------------------------------------

    def _read_source(self, blueprint_id: str) -> tuple[bytes, Path]:
        path = self._guard_no_reparse(blueprint_id)
        try:
            stat_result = path.lstat()
        except FileNotFoundError as error:
            raise ContractError(
                "missing-blueprint", f"No blueprint source for {blueprint_id!r}"
            ) from error
        if stat_result.st_size > MAX_BLUEPRINT_SOURCE_BYTES:
            raise ContractError(
                "source-too-large",
                f"Blueprint source for {blueprint_id!r} exceeds the maximum size",
            )
        source = path.read_bytes()
        check_source_size(source)
        return source, path

    def _validate_source(self, blueprint_id: str, source: bytes, path: Path) -> WorkflowSnapshot:
        loaded = yaml.safe_load(source)
        if not isinstance(loaded, dict):
            raise ContractError(
                "corrupt-blueprint", f"Blueprint source for {blueprint_id!r} is not a mapping"
            )
        definition = WorkflowDefinition.model_validate(loaded)
        return WorkflowSnapshot(
            definition=definition, digest=_source_digest(source), source_path=path
        )

    def inspect(self, blueprint_id: str) -> WorkflowSnapshot:
        """Return the current validated snapshot; never a silently older one."""
        source, path = self._read_source(blueprint_id)
        return self._validate_source(blueprint_id, source, path)

    def list(self) -> tuple[WorkflowInspection, ...]:
        """Inspect every blueprint dir, isolating per-blueprint validation errors."""
        try:
            entries = sorted(p for p in self.root.iterdir() if p.is_dir())
        except OSError as error:
            raise ContractError(
                "unreadable-library", "Workflow library root could not be read"
            ) from error
        inspections: list[WorkflowInspection] = []
        for entry in entries:
            blueprint_id = entry.name
            if not _SLUG.match(blueprint_id):
                continue
            try:
                has_source = (entry / SOURCE_NAME).exists()
            except OSError:
                inspections.append(
                    WorkflowInspection(
                        blueprint_id,
                        None,
                        (f"Blueprint {blueprint_id!r} could not be read.",),
                    )
                )
                continue
            if not has_source:
                continue
            try:
                snapshot = self.inspect(blueprint_id)
            except (ContractError, yaml.YAMLError, ValueError) as error:
                inspections.append(
                    WorkflowInspection(blueprint_id, None, (str(error),))
                )
            except OSError:
                # A neutral message: never leak the private filesystem path.
                inspections.append(
                    WorkflowInspection(
                        blueprint_id,
                        None,
                        (f"Blueprint {blueprint_id!r} could not be read.",),
                    )
                )
            else:
                inspections.append(WorkflowInspection(blueprint_id, snapshot, ()))
        return tuple(inspections)

    # -- writes -----------------------------------------------------------

    def write_candidate(
        self,
        blueprint_id: str,
        expected_digest: str,
        definition: WorkflowDefinition,
    ) -> WorkflowSnapshot:
        """Publish a validated definition after a final current-byte check.

        The candidate is revalidated (a ``model_copy`` skips validators), its
        identity is pinned to ``blueprint_id``, and the serialized bytes are size
        checked before anything is written, so an invalid or oversized candidate
        can never destroy the prior valid file. External editors cannot be forced
        into Flowgency locks, so the current bytes are re-hashed immediately before
        the overwrite and a known conflict is rejected. The returned snapshot
        describes the exact bytes committed here, not whatever a post-write inspect
        might read back.
        """
        candidate = WorkflowDefinition.model_validate(definition.model_dump())
        self._blueprint_dir(blueprint_id)
        if candidate.id != blueprint_id:
            raise ContractError(
                "identity-mismatch",
                f"Candidate id {candidate.id!r} does not match blueprint "
                f"{blueprint_id!r}",
            )
        payload = yaml.safe_dump(
            candidate.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        )
        payload_bytes = payload.encode("utf-8")
        check_source_size(payload_bytes)
        source, path = self._read_source(blueprint_id)
        if _source_digest(source) != expected_digest:
            raise ConfigConflictError(
                "Blueprint source changed on disk; reload before saving"
            )
        atomic_write_text(path, payload)
        return WorkflowSnapshot(
            definition=candidate,
            digest=_source_digest(payload_bytes),
            source_path=path,
        )
