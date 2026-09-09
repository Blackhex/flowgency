from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flowgency.fs.atomic import atomic_write_text
from flowgency.fs.locks import exclusive_lock
from flowgency.jobs.authority import JobAuthorityError, JobAuthorityRef, JobStore
from flowgency.jobs.models import JobRecord
from flowgency.jobs.store import job_lock_path
from flowgency.tickets.errors import TicketForbidden
from flowgency.tickets.models import (
    AgentTicketContext,
    StorageBinding,
    TicketAccessGrant,
    TicketRef,
)

if TYPE_CHECKING:
    from flowgency.workflows.configuration import WorkflowBinding


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _split_session_id(session_id: str) -> tuple[str, str, str]:
    parts = session_id.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
    return parts[0], parts[1], parts[2]


def _split_token(token: str) -> tuple[str, str, str, str]:
    parts = token.split(":", 3)
    if len(parts) != 4 or not all(parts):
        raise TicketForbidden("invalid-token", "Ticket access token is invalid")
    return parts[0], parts[1], parts[2], parts[3]


class _StoredSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    token_hash: str
    context: AgentTicketContext
    immutable_digest: str
    run_fingerprint: "_RunFingerprint"


class OriginalTicketTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    generation: str
    binding: StorageBinding
    ref: TicketRef

    @model_validator(mode="after")
    def _validate_binding_roundtrip(self) -> "OriginalTicketTarget":
        rebound = StorageBinding.model_validate(
            self.binding.model_dump(mode="json", exclude={"binding_id"})
        )
        if rebound != self.binding:
            raise ValueError("original target binding did not round-trip canonically")
        if self.ref.binding_id != self.binding.binding_id:
            raise ValueError("original target ref does not match its binding")
        if self.ref.team_id != self.binding.team_id:
            raise ValueError("original target team does not match its binding")
        if self.ref.workflow_id != self.binding.workflow_id:
            raise ValueError("original target workflow does not match its binding")
        return self


class _RunFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    worker_pid: int | None = None
    started_at: str | None = None
    launched_at: str | None = None
    observed_session_id: str | None = None


class _StoredRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 2
    sessions: dict[str, _StoredSession] = Field(default_factory=dict)
    original_targets: tuple[OriginalTicketTarget, ...] = ()


@dataclass(frozen=True)
class _LockedRegistry:
    authority: JobAuthorityRef
    path: Path
    state: _StoredRegistry


class TicketAccessRegistry:
    def __init__(self, job_store: JobStore) -> None:
        self.job_store = job_store

    def open(self, authority: JobAuthorityRef) -> TicketAccessGrant:
        authority = self._trusted_authority(authority)
        session_id = f"{authority.team_id}:{authority.job_id}:{uuid.uuid4().hex}"
        token = f"{session_id}:{secrets.token_urlsafe(32)}"
        with exclusive_lock(job_lock_path(authority.path), wait=True):
            record = self._require_running(authority)
            locked = self._load_locked(authority, create=True)
            context = AgentTicketContext(
                job_id=authority.job_id,
                team_id=authority.team_id,
                agent_name=record.spec.agent_name,
                session_id=session_id,
            )
            sessions = {}
            sessions[session_id] = _StoredSession(
                session_id=session_id,
                token_hash=_token_hash(token),
                context=context,
                immutable_digest=authority.immutable_digest,
                run_fingerprint=self._run_fingerprint(record),
            )
            self._save(
                locked.path,
                _StoredRegistry(sessions=sessions, original_targets=locked.state.original_targets),
            )
        return TicketAccessGrant(session_id=session_id, token=token, context=context)

    def authenticate(self, token: str) -> AgentTicketContext:
        team_id, job_id, nonce, _ = _split_token(token)
        session_id = f"{team_id}:{job_id}:{nonce}"
        return self._authenticate_token(team_id, job_id, session_id, token)

    def validate_context(self, context: AgentTicketContext) -> None:
        team_id, job_id, _ = _split_session_id(context.session_id)
        if team_id != context.team_id or job_id != context.job_id:
            raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
        authority_path = self.job_store.path(team_id, job_id)
        with exclusive_lock(job_lock_path(authority_path), wait=True):
            locked = self._load_locked_by_job(team_id, job_id)
            session = locked.state.sessions.get(context.session_id)
            if session is None or session.context != context:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            try:
                record = self._require_running(
                    locked.authority,
                    expected_context=context,
                )
            except TicketForbidden:
                self._drop_session(locked.path, locked.state, context.session_id)
                raise
            if session.run_fingerprint != self._run_fingerprint(record):
                self._drop_session(locked.path, locked.state, context.session_id)
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")

    def close(self, session_id: str) -> None:
        team_id, job_id, _ = _split_session_id(session_id)
        authority_path = self.job_store.path(team_id, job_id)
        with exclusive_lock(job_lock_path(authority_path), wait=True):
            locked = self._load_locked_by_job(team_id, job_id)
            self._drop_session(locked.path, locked.state, session_id)

    def revoke(self, authority: JobAuthorityRef) -> None:
        trusted = self._trusted_authority(authority)
        with exclusive_lock(job_lock_path(trusted.path), wait=True):
            locked = self._load_locked(trusted, create=False)
            self._save(
                locked.path,
                _StoredRegistry(
                    sessions={},
                    original_targets=locked.state.original_targets,
                ),
            )

    def register_target(
        self,
        context: AgentTicketContext,
        binding: WorkflowBinding,
        ref: TicketRef,
    ) -> None:
        self.validate_context(context)
        if binding.team_id != context.team_id:
            raise TicketForbidden("wrong-team", "Ticket belongs to another team")
        if ref.team_id != context.team_id or ref.workflow_id != binding.workflow_id:
            raise TicketForbidden("wrong-team", "Ticket belongs to another team")
        if ref.binding_id != binding.storage.binding_id:
            raise TicketForbidden("binding-mismatch", "Ticket ref does not belong to this storage binding")
        team_id, job_id, _ = _split_session_id(context.session_id)
        authority_path = self.job_store.path(team_id, job_id)
        with exclusive_lock(job_lock_path(authority_path), wait=True):
            locked = self._load_locked_by_job(team_id, job_id)
            if context.session_id not in locked.state.sessions:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            record = self._require_running(locked.authority, expected_context=context)
            session = locked.state.sessions[context.session_id]
            if session.run_fingerprint != self._run_fingerprint(record):
                self._drop_session(locked.path, locked.state, context.session_id)
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            entry = OriginalTicketTarget(
                generation=context.session_id,
                binding=StorageBinding.model_validate(
                    binding.storage.model_dump(mode="json", exclude={"binding_id"})
                ),
                ref=TicketRef.model_validate(ref.model_dump(mode="json")),
            )
            targets = list(locked.state.original_targets)
            if entry not in targets:
                targets.append(entry)
            self._save(
                locked.path,
                _StoredRegistry(
                    sessions=dict(locked.state.sessions),
                    original_targets=tuple(targets),
                ),
            )

    def read_original_targets(self, authority: JobAuthorityRef) -> tuple[OriginalTicketTarget, ...]:
        authority = self._trusted_authority(authority)
        with exclusive_lock(job_lock_path(authority.path), wait=True):
            locked = self._load_locked(authority, create=False)
            return tuple(
                OriginalTicketTarget.model_validate(
                    entry.model_dump(mode="json", exclude={"binding": {"binding_id"}})
                )
                for entry in locked.state.original_targets
            )

    def _require_running(
        self,
        authority: JobAuthorityRef,
        *,
        expected_context: AgentTicketContext | None = None,
    ) -> JobRecord:
        try:
            record = self.job_store.read(authority)
        except (FileNotFoundError, JobAuthorityError) as exc:
            raise TicketForbidden("invalid-agent-context", "Agent context is not authorized") from exc
        if record.status != "running":
            raise TicketForbidden("ended-session", "Ticket access session is no longer active")
        if expected_context is not None:
            if record.spec.team_key != expected_context.team_id:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            if record.spec.job_id != expected_context.job_id:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            if record.spec.agent_name != expected_context.agent_name:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
        return record

    def _access_path(self, team_id: str, job_id: str, *, create: bool = True) -> Path:
        root = self._access_root(team_id, job_id, create=create)
        return root / "ticket-access.json"

    def _access_root(self, team_id: str, job_id: str, *, create: bool) -> Path:
        team_root = self.job_store.team_root(team_id)
        registry_root = (team_root / "ticket-access").resolve(strict=False)
        if registry_root.parent != team_root.resolve(strict=False):
            raise ValueError("ticket access root escaped the authoritative store")
        if create:
            registry_root.mkdir(parents=True, exist_ok=True)
        target = (registry_root / job_id).resolve(strict=False)
        if target.parent != registry_root:
            raise ValueError("ticket access path escaped the authoritative store")
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def _load(self, path: Path) -> _StoredRegistry:
        if not path.exists():
            return _StoredRegistry()
        return _StoredRegistry.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, path: Path, state: _StoredRegistry) -> None:
        if not state.sessions and not state.original_targets:
            if path.exists():
                path.unlink()
            return
        atomic_write_text(
            path,
            state.model_dump_json(
                indent=2,
                exclude={"original_targets": {"__all__": {"binding": {"binding_id"}}}},
            ),
        )

    def _authenticate_token(
        self,
        team_id: str,
        job_id: str,
        session_id: str,
        token: str,
    ) -> AgentTicketContext:
        authority_path = self.job_store.path(team_id, job_id)
        with exclusive_lock(job_lock_path(authority_path), wait=True):
            locked = self._load_locked_by_job(team_id, job_id)
            session = locked.state.sessions.get(session_id)
            if session is None:
                raise TicketForbidden("invalid-token", "Ticket access token is invalid")
            if not hmac.compare_digest(session.token_hash, _token_hash(token)):
                raise TicketForbidden("invalid-token", "Ticket access token is invalid")
            try:
                record = self._require_running(
                    locked.authority,
                    expected_context=session.context,
                )
            except TicketForbidden as exc:
                self._drop_session(locked.path, locked.state, session_id)
                if exc.code == "ended-session":
                    raise
                raise TicketForbidden("invalid-token", "Ticket access token is invalid") from exc
            if session.run_fingerprint != self._run_fingerprint(record):
                self._drop_session(locked.path, locked.state, session_id)
                raise TicketForbidden("invalid-token", "Ticket access token is invalid")
            return session.context

    def _load_locked(self, authority: JobAuthorityRef, *, create: bool) -> _LockedRegistry:
        trusted = self._trusted_authority(authority)
        path = self._access_path(trusted.team_id, trusted.job_id, create=create)
        return _LockedRegistry(authority=trusted, path=path, state=self._load(path))

    def _load_locked_by_job(self, team_id: str, job_id: str) -> _LockedRegistry:
        path = self._access_path(team_id, job_id, create=False)
        state = self._load(path)
        session = next(iter(state.sessions.values()), None)
        digest = session.immutable_digest if session is not None else self._read_job_digest(team_id, job_id)
        authority = self.job_store.reference(team_id, job_id, digest)
        return _LockedRegistry(authority=authority, path=path, state=state)

    def _trusted_authority(self, authority: JobAuthorityRef) -> JobAuthorityRef:
        trusted = self.job_store.reference(
            authority.team_id,
            authority.job_id,
            authority.immutable_digest,
        )
        if Path(authority.store_root).resolve() != self.job_store.root:
            raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
        if authority.path != trusted.path:
            raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
        return trusted

    def _read_job_digest(self, team_id: str, job_id: str) -> str:
        path = self.job_store.path(team_id, job_id)
        with path.open(encoding="utf-8") as job_file:
            for line in job_file:
                if line.startswith("authority_digest:"):
                    return line.partition(":")[2].strip()
        raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")

    def _drop_session(self, path: Path, state: _StoredRegistry, session_id: str) -> None:
        sessions = dict(state.sessions)
        if session_id not in sessions:
            return
        sessions.pop(session_id)
        self._save(
            path,
            _StoredRegistry(sessions=sessions, original_targets=state.original_targets),
        )

    def _run_fingerprint(self, record: JobRecord) -> _RunFingerprint:
        return _RunFingerprint(
            worker_pid=record.worker_pid,
            started_at=record.started_at,
            launched_at=record.launched_at,
            observed_session_id=record.session_id,
        )