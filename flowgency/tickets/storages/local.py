"""Atomic filesystem ticket storage.

Layout: ``<root>/<team_id>/<workflow_id>/tickets/<ticket_id>.md``. Each ticket is
one Markdown document – validated record and receipt ledger in YAML frontmatter,
description in the body – replaced atomically so a failed write leaves the prior
document untouched. A configured root is created by configuration, never here;
only a board namespace is created, and only while creating its first ticket.
"""

from __future__ import annotations

import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml
from pydantic import ValidationError

from flowgency.fs.atomic import atomic_write_bytes, atomic_write_text
from flowgency.fs.locks import exclusive_lock
from flowgency.records.frontmatter import parse_frontmatter
from flowgency.tickets.artifacts import RetainedArtifact
from flowgency.tickets.errors import (
    StorageUnavailable,
    TicketConflict,
    TicketCorrupt,
    TicketForbidden,
    TicketNotFound,
    TicketTooLarge,
)
from flowgency.tickets.models import (
    Clock,
    StorageBinding,
    StorageHealth,
    TicketMutationResult,
    TicketOperation,
    TicketRecord,
    TicketRef,
)
from flowgency.workflows.models import ArtifactRef

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_TICKET_BYTES = 512 * 1024
_SEQUENCE_NAME = ".sequence"
_NAMESPACE_LOCK_NAME = ".namespace.lock"


def _stat_is_symlink_or_reparse(stat_result: os.stat_result) -> bool:
    file_attributes = getattr(stat_result, "st_file_attributes", 0) or 0
    return bool(
        stat.S_ISLNK(stat_result.st_mode)
        or (file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    )


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        return _stat_is_symlink_or_reparse(path.lstat())
    except FileNotFoundError:
        return False


class LocalTicketStorage:
    def __init__(self, root: Path, *, clock: Clock) -> None:
        self.root = Path(root)
        self.clock = clock

    # -- binding identity -------------------------------------------------

    def _binding_id_for(self, team_id: str, workflow_id: str) -> str:
        return StorageBinding.compute_binding_id(
            "local", {"root": str(self.root)}, team_id, workflow_id
        )

    def _verify_binding(self, ref: TicketRef) -> None:
        if ref.binding_id != self._binding_id_for(ref.team_id, ref.workflow_id):
            raise TicketForbidden(
                "binding-mismatch",
                "Ticket ref does not belong to this storage binding",
            )

    def _ref_for(self, team_id: str, workflow_id: str, ticket_id: str) -> TicketRef:
        return TicketRef(
            binding_id=self._binding_id_for(team_id, workflow_id),
            team_id=team_id,
            workflow_id=workflow_id,
            ticket_id=ticket_id,
        )

    # -- path resolution and safety ---------------------------------------

    def _walk_no_reparse(self, *segments: str) -> None:
        walked = self.root
        for segment in segments:
            walked = walked / segment
            if _is_symlink_or_reparse(walked):
                raise TicketForbidden(
                    "unsafe-path", "Ticket path crosses a link or reparse point"
                )

    def _require_safe_ref(
        self, team_id: str, workflow_id: str, ticket_id: str
    ) -> None:
        for part in (team_id, workflow_id, ticket_id):
            if not _SLUG.match(part):
                raise TicketForbidden(
                    "unsafe-path", "Ticket identifier is not a permitted slug"
                )
        root = self.root.resolve(strict=False)
        candidate = (
            self.root / team_id / workflow_id / "tickets" / f"{ticket_id}.md"
        ).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise TicketForbidden(
                "unsafe-path", "Ticket path escapes the storage root"
            ) from error
        self._walk_no_reparse(team_id, workflow_id, "tickets", f"{ticket_id}.md")

    def _require_safe_metadata(
        self, team_id: str, workflow_id: str, name: str
    ) -> None:
        self._walk_no_reparse(team_id, workflow_id, "tickets", name)

    def _require_safe_artifact_id(self, artifact_id: str) -> None:
        if not _DIGEST.match(artifact_id):
            raise TicketForbidden("unsafe-path", "Artifact identifier is invalid")

    def _require_safe_artifact_metadata(
        self, team_id: str, workflow_id: str, name: str
    ) -> None:
        self._walk_no_reparse(team_id, workflow_id, "artifacts", name)

    def _namespace_dir(self, team_id: str, workflow_id: str) -> Path:
        return self.root / team_id / workflow_id / "tickets"

    def _artifact_dir(self, team_id: str, workflow_id: str) -> Path:
        return self.root / team_id / workflow_id / "artifacts"

    def _ticket_path(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / f"{ref.ticket_id}.md"

    def _artifact_path(self, ref: TicketRef, artifact_id: str) -> Path:
        return self._artifact_dir(ref.team_id, ref.workflow_id) / f"{artifact_id}.json"

    def _artifact_lock(self, ref: TicketRef, artifact_id: str) -> Path:
        return self._artifact_dir(ref.team_id, ref.workflow_id) / f"{artifact_id}.lock"

    def lock_path(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / f"{ref.ticket_id}.lock"

    def _namespace_lock(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / _NAMESPACE_LOCK_NAME

    def _sequence_path(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / _SEQUENCE_NAME

    def _require_root(self) -> None:
        if not self.root.exists():
            raise StorageUnavailable(
                "missing-root", "Ticket storage root does not exist"
            )
        if _is_symlink_or_reparse(self.root) or not self.root.is_dir():
            raise StorageUnavailable(
                "unreadable-root", "Ticket storage root is not a readable directory"
            )

    # -- serialization ----------------------------------------------------

    def _serialize(self, record: TicketRecord) -> str:
        data = record.model_dump(mode="json")
        description = data.pop("description")
        # The envelope carries the namespace/binding identity via ``ref``.
        envelope = {"schema_version": 1, **data}
        front = yaml.safe_dump(envelope, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n\n{description}\n"

    def _deserialize(self, text: str, ref: TicketRef, ticket_id: str) -> TicketRecord:
        meta, body = parse_frontmatter(text)
        if not meta or meta.get("schema_version") != 1:
            raise TicketCorrupt(
                "corrupt-record", "Ticket document is not readable", ticket_id=ticket_id
            )
        data = {key: value for key, value in meta.items() if key != "schema_version"}
        data["description"] = body
        data["ref"] = ref.model_dump()
        try:
            return TicketRecord.model_validate(data)
        except ValidationError as error:
            raise TicketCorrupt(
                "corrupt-record", "Ticket document failed validation", ticket_id=ticket_id
            ) from error

    def write_record(self, record: TicketRecord) -> None:
        text = self._serialize(record)
        if len(text.encode("utf-8")) > _MAX_TICKET_BYTES:
            raise TicketTooLarge(
                "ticket-too-large",
                "Ticket document exceeds the maximum size",
                ticket_id=record.ref.ticket_id,
            )
        try:
            atomic_write_text(self._ticket_path(record.ref), text)
        except OSError as error:
            raise StorageUnavailable(
                "write-failed", "Could not persist the ticket document"
            ) from error

    def require_same_identity(
        self, current: TicketRecord, candidate: TicketRecord
    ) -> None:
        if (
            candidate.id != current.id
            or candidate.number != current.number
            or candidate.created_at != current.created_at
            or candidate.ref != current.ref
            or candidate.revision != current.revision
            or candidate.receipts != current.receipts
        ):
            raise TicketConflict(
                "identity-changed", "Mutation must not change ticket identity"
            )

    # -- reads ------------------------------------------------------------

    def read(self, ref: TicketRef) -> TicketRecord:
        self._require_safe_ref(ref.team_id, ref.workflow_id, ref.ticket_id)
        self._verify_binding(ref)
        self._require_root()
        path = self._ticket_path(ref)
        try:
            stat_result = path.lstat()
        except FileNotFoundError as error:
            raise TicketNotFound(
                "ticket-not-found", "No such ticket", ticket_id=ref.ticket_id
            ) from error
        except PermissionError as error:
            raise StorageUnavailable(
                "unreadable-ticket", "Ticket document is not readable"
            ) from error
        if stat_result.st_size > _MAX_TICKET_BYTES:
            raise TicketCorrupt(
                "oversized-record",
                "Ticket document exceeds the maximum size",
                ticket_id=ref.ticket_id,
            )
        try:
            text = path.read_text(encoding="utf-8")
        except PermissionError as error:
            raise StorageUnavailable(
                "unreadable-ticket", "Ticket document is not readable"
            ) from error
        except UnicodeDecodeError as error:
            raise TicketCorrupt(
                "corrupt-record",
                "Ticket document is not valid UTF-8",
                ticket_id=ref.ticket_id,
            ) from error
        except OSError as error:
            raise StorageUnavailable(
                "unreadable-ticket", "Ticket document could not be read"
            ) from error
        return self._deserialize(text, ref, ref.ticket_id)

    def list(self, team_id: str, workflow_id: str) -> tuple[TicketRecord, ...]:
        self._require_safe_ref(team_id, workflow_id, "placeholder")
        self._require_root()
        namespace = self._namespace_dir(team_id, workflow_id)
        if not namespace.exists():
            return ()
        try:
            entries = sorted(namespace.glob("*.md"))
        except OSError as error:
            raise StorageUnavailable(
                "unreadable-namespace", "Ticket namespace could not be read"
            ) from error
        records: list[TicketRecord] = []
        for entry in entries:
            ref = self._ref_for(team_id, workflow_id, entry.stem)
            records.append(self.read(ref))
        return tuple(sorted(records, key=lambda record: record.number))

    def receipt(
        self, ref: TicketRef, operation: TicketOperation
    ) -> TicketMutationResult | None:
        current = self.read(ref)
        previous = current.find_receipt(operation.operation_id)
        if previous is None:
            return None
        previous.require_digest(operation.request_digest)
        return previous.result(replayed=True)

    def put_artifact(self, namespace: TicketRef, artifact: RetainedArtifact) -> ArtifactRef:
        self._require_safe_ref(
            namespace.team_id, namespace.workflow_id, namespace.ticket_id
        )
        self._verify_binding(namespace)
        self._require_root()
        self._require_safe_artifact_id(artifact.digest)
        self._require_safe_artifact_metadata(
            namespace.team_id, namespace.workflow_id, f"{artifact.digest}.lock"
        )
        self._require_safe_artifact_metadata(
            namespace.team_id, namespace.workflow_id, f"{artifact.digest}.json"
        )
        with exclusive_lock(self._artifact_lock(namespace, artifact.digest), wait=True):
            path = self._artifact_path(namespace, artifact.digest)
            if path.exists():
                existing = self.read_artifact(namespace, artifact.digest)
                if existing != artifact:
                    raise TicketConflict(
                        "artifact-digest-mismatch",
                        "Retained artifact content does not match its reference",
                    )
                return artifact.ref()
            try:
                atomic_write_bytes(path, artifact.to_storage_bytes())
            except OSError as error:
                raise StorageUnavailable(
                    "write-failed", "Could not persist the retained artifact"
                ) from error
            return artifact.ref()

    def read_artifact(self, namespace: TicketRef, artifact_id: str) -> RetainedArtifact:
        self._require_safe_ref(
            namespace.team_id, namespace.workflow_id, namespace.ticket_id
        )
        self._verify_binding(namespace)
        self._require_root()
        self._require_safe_artifact_id(artifact_id)
        self._require_safe_artifact_metadata(
            namespace.team_id, namespace.workflow_id, f"{artifact_id}.json"
        )
        path = self._artifact_path(namespace, artifact_id)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as error:
            raise TicketNotFound(
                "artifact-not-found", "No such artifact", ticket_id=namespace.ticket_id
            ) from error
        except PermissionError as error:
            raise StorageUnavailable(
                "unreadable-artifact", "Retained artifact is not readable"
            ) from error
        except OSError as error:
            raise StorageUnavailable(
                "unreadable-artifact", "Retained artifact could not be read"
            ) from error
        try:
            artifact = RetainedArtifact.from_storage_bytes(payload)
        except Exception as error:
            raise TicketCorrupt(
                "corrupt-artifact",
                "Retained artifact is not readable",
                ticket_id=namespace.ticket_id,
            ) from error
        if artifact.digest != artifact_id:
            raise TicketConflict(
                "artifact-digest-mismatch",
                "Retained artifact content does not match its reference",
            )
        return artifact

    def check(self) -> StorageHealth:
        if not self.root.exists():
            return StorageHealth(status="missing", detail="storage root does not exist")
        if _is_symlink_or_reparse(self.root) or not self.root.is_dir():
            return StorageHealth(
                status="unreadable", detail="storage root is not a readable directory"
            )
        try:
            with os.scandir(self.root) as entries:
                for _ in entries:
                    break
        except OSError:
            return StorageHealth(
                status="unreadable", detail="storage root is not readable"
            )
        return StorageHealth(status="ok")

    # -- writes -----------------------------------------------------------

    def _next_number(self, ref: TicketRef) -> int:
        self._require_safe_metadata(ref.team_id, ref.workflow_id, _SEQUENCE_NAME)
        sequence = self._sequence_path(ref)
        namespace = self._namespace_dir(ref.team_id, ref.workflow_id)
        populated = namespace.exists() and any(namespace.glob("*.md"))
        sequence.parent.mkdir(parents=True, exist_ok=True)
        try:
            last = int(sequence.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError) as error:
            if populated:
                raise TicketCorrupt(
                    "corrupt-sequence",
                    "Ticket numbering metadata is missing or unreadable",
                    ticket_id=ref.ticket_id,
                ) from error
            last = 0
        nxt = last + 1
        atomic_write_text(sequence, str(nxt))
        return nxt

    def create(
        self, record: TicketRecord, operation: TicketOperation
    ) -> TicketMutationResult:
        ref = record.ref
        if ref is None:
            raise TicketConflict(
                "unbound-record", "Ticket record must be scoped with a ref"
            )
        self._require_safe_ref(ref.team_id, ref.workflow_id, ref.ticket_id)
        self._verify_binding(ref)
        self._require_root()
        self._require_safe_metadata(ref.team_id, ref.workflow_id, _NAMESPACE_LOCK_NAME)
        with exclusive_lock(self._namespace_lock(ref), wait=True):
            if self._ticket_path(ref).exists():
                current = self.read(ref)
                previous = current.find_receipt(operation.operation_id)
                if previous is not None:
                    previous.require_digest(operation.request_digest)
                    return previous.result(replayed=True)
                raise TicketConflict(
                    "duplicate-ticket",
                    "Ticket already exists",
                    ticket_id=ref.ticket_id,
                )
            number = self._next_number(ref)
            now = self.clock()
            opened = record.model_copy(
                update={
                    "number": number,
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            sealed, result = opened.commit_creation(operation, now)
            self.write_record(sealed)
            return result

    def apply(
        self,
        ref: TicketRef,
        expected_revision: int,
        operation: TicketOperation,
        mutate: Callable[[TicketRecord], TicketRecord],
    ) -> TicketMutationResult:
        self._require_safe_ref(ref.team_id, ref.workflow_id, ref.ticket_id)
        self._verify_binding(ref)
        self._require_root()
        # Never create a namespace on apply; only create() may do that.
        if not self._ticket_path(ref).exists():
            raise TicketNotFound(
                "ticket-not-found", "No such ticket", ticket_id=ref.ticket_id
            )
        self._require_safe_metadata(ref.team_id, ref.workflow_id, f"{ref.ticket_id}.lock")
        with exclusive_lock(self.lock_path(ref), wait=True):
            current = self.read(ref)
            previous = current.find_receipt(operation.operation_id)
            if previous is not None:
                previous.require_digest(operation.request_digest)
                return previous.result(replayed=True)
            if current.revision != expected_revision:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            candidate = TicketRecord.model_validate(mutate(current).model_dump())
            self.require_same_identity(current, candidate)
            updated, result = candidate.commit_operation(
                current, operation, self.clock()
            )
            self.write_record(updated)
            return result
