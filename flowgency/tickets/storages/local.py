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

from flowgency.fs.atomic import atomic_write_text
from flowgency.fs.locks import exclusive_lock
from flowgency.records.frontmatter import parse_frontmatter
from flowgency.tickets.errors import (
    StorageUnavailable,
    TicketConflict,
    TicketCorrupt,
    TicketForbidden,
    TicketNotFound,
)
from flowgency.tickets.models import (
    Clock,
    StorageHealth,
    TicketMutationResult,
    TicketOperation,
    TicketRecord,
    TicketRef,
    seal_operation,
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MAX_TICKET_BYTES = 512 * 1024
_SEQUENCE_NAME = ".sequence"


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

    # -- path resolution and safety ---------------------------------------

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
        walked = self.root
        for segment in (team_id, workflow_id, "tickets", f"{ticket_id}.md"):
            walked = walked / segment
            if _is_symlink_or_reparse(walked):
                raise TicketForbidden(
                    "unsafe-path", "Ticket path crosses a link or reparse point"
                )

    def _namespace_dir(self, team_id: str, workflow_id: str) -> Path:
        return self.root / team_id / workflow_id / "tickets"

    def _ticket_path(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / f"{ref.ticket_id}.md"

    def lock_path(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / f"{ref.ticket_id}.lock"

    def _namespace_lock(self, ref: TicketRef) -> Path:
        return self._namespace_dir(ref.team_id, ref.workflow_id) / ".namespace.lock"

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
        data.pop("ref", None)
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
        data["ref"] = ref.model_dump() if ref is not None else None
        try:
            return TicketRecord.model_validate(data)
        except ValidationError as error:
            raise TicketCorrupt(
                "corrupt-record", "Ticket document failed validation", ticket_id=ticket_id
            ) from error

    def write_record(self, record: TicketRecord) -> None:
        try:
            atomic_write_text(self._ticket_path(record.ref), self._serialize(record))
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
        ):
            raise TicketConflict(
                "identity-changed", "Mutation must not change ticket identity"
            )

    # -- reads ------------------------------------------------------------

    def read(self, ref: TicketRef) -> TicketRecord:
        self._require_safe_ref(ref.team_id, ref.workflow_id, ref.ticket_id)
        self._require_root()
        path = self._ticket_path(ref)
        try:
            stat_result = path.lstat()
        except FileNotFoundError as error:
            raise TicketNotFound(
                "ticket-not-found", "No such ticket", ticket_id=ref.ticket_id
            ) from error
        if stat_result.st_size > _MAX_TICKET_BYTES:
            raise TicketCorrupt(
                "oversized-record",
                "Ticket document exceeds the maximum size",
                ticket_id=ref.ticket_id,
            )
        return self._deserialize(path.read_text(encoding="utf-8"), ref, ref.ticket_id)

    def list(self, team_id: str, workflow_id: str) -> tuple[TicketRecord, ...]:
        self._require_safe_ref(team_id, workflow_id, "placeholder")
        self._require_root()
        namespace = self._namespace_dir(team_id, workflow_id)
        if not namespace.exists():
            return ()
        records: list[TicketRecord] = []
        for entry in sorted(namespace.glob("*.md")):
            ticket_id = entry.stem
            ref = TicketRef(
                team_id=team_id, workflow_id=workflow_id, ticket_id=ticket_id
            )
            records.append(
                self._deserialize(entry.read_text(encoding="utf-8"), ref, ticket_id)
            )
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

    def check(self) -> StorageHealth:
        if not self.root.exists():
            return StorageHealth(status="missing", detail="storage root does not exist")
        if _is_symlink_or_reparse(self.root) or not self.root.is_dir():
            return StorageHealth(
                status="unreadable", detail="storage root is not a readable directory"
            )
        return StorageHealth(status="ok")

    # -- writes -----------------------------------------------------------

    def _next_number(self, ref: TicketRef) -> int:
        sequence = self._sequence_path(ref)
        sequence.parent.mkdir(parents=True, exist_ok=True)
        try:
            last = int(sequence.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
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
        self._require_root()
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
            sealed, result = seal_operation(opened, operation, now)
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
        self._require_root()
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
