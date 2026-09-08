"""Ticket storage errors – HTTP-safe, never carrying private absolute paths."""

from __future__ import annotations


class TicketStorageError(Exception):
    """Base error carrying a stable code, a message, and HTTP-safe details."""

    http_status = 500

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, object] = dict(details)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class TicketNotFound(TicketStorageError):
    http_status = 404


class StorageUnavailable(TicketStorageError):
    http_status = 503


class TicketCorrupt(TicketStorageError):
    http_status = 500


class TicketTooLarge(TicketStorageError):
    http_status = 413


class TicketConflict(TicketStorageError):
    http_status = 409


class OperationConflict(TicketStorageError):
    http_status = 409


class TicketForbidden(TicketStorageError):
    http_status = 403


class WorkflowUnavailable(TicketStorageError):
    http_status = 503
