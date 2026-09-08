from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from flowgency.fs.atomic import atomic_write_text
from flowgency.fs.locks import exclusive_lock
from flowgency.jobs.authority import JobAuthorityRef, JobStore
from flowgency.jobs.store import job_lock_path
from flowgency.tickets.errors import TicketForbidden
from flowgency.tickets.models import AgentTicketContext, TicketAccessGrant, TicketRef
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


class _StoredRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    sessions: dict[str, _StoredSession] = Field(default_factory=dict)
    original_targets: dict[str, tuple[TicketRef, ...]] = Field(default_factory=dict)


class TicketAccessRegistry:
    def __init__(self, job_store: JobStore) -> None:
        self.job_store = job_store

    def open(self, authority: JobAuthorityRef) -> TicketAccessGrant:
        session_id = f"{authority.team_id}:{authority.job_id}:{uuid.uuid4().hex}"
        token = f"{session_id}:{secrets.token_urlsafe(32)}"
        with exclusive_lock(job_lock_path(authority.path), wait=True):
            record = self._require_running(authority)
            path = self._access_path(authority.team_id, authority.job_id)
            state = self._load(path)
            context = AgentTicketContext(
                job_id=authority.job_id,
                team_id=authority.team_id,
                agent_name=record.spec.agent_name,
                session_id=session_id,
            )
            sessions = dict(state.sessions)
            sessions[session_id] = _StoredSession(
                session_id=session_id,
                token_hash=_token_hash(token),
                context=context,
                immutable_digest=authority.immutable_digest,
            )
            targets = dict(state.original_targets)
            targets.setdefault(session_id, ())
            self._save(path, _StoredRegistry(sessions=sessions, original_targets=targets))
        return TicketAccessGrant(session_id=session_id, token=token, context=context)

    def authenticate(self, token: str) -> AgentTicketContext:
        team_id, job_id, nonce, _ = _split_token(token)
        session_id = f"{team_id}:{job_id}:{nonce}"
        with exclusive_lock(job_lock_path(self.job_store.path(team_id, job_id)), wait=True):
            state = self._load(self._access_path(team_id, job_id))
            session = state.sessions.get(session_id)
            if session is None:
                raise TicketForbidden("invalid-token", "Ticket access token is invalid")
            if not hmac.compare_digest(session.token_hash, _token_hash(token)):
                raise TicketForbidden("invalid-token", "Ticket access token is invalid")
            self._require_running(
                self.job_store.reference(team_id, job_id, session.immutable_digest),
                expected_context=session.context,
            )
            return session.context

    def validate_context(self, context: AgentTicketContext) -> None:
        team_id, job_id, _ = _split_session_id(context.session_id)
        if team_id != context.team_id or job_id != context.job_id:
            raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
        with exclusive_lock(job_lock_path(self.job_store.path(team_id, job_id)), wait=True):
            state = self._load(self._access_path(team_id, job_id))
            session = state.sessions.get(context.session_id)
            if session is None or session.context != context:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            self._require_running(
                self.job_store.reference(team_id, job_id, session.immutable_digest),
                expected_context=context,
            )

    def close(self, session_id: str) -> None:
        team_id, job_id, _ = _split_session_id(session_id)
        with exclusive_lock(job_lock_path(self.job_store.path(team_id, job_id)), wait=True):
            path = self._access_path(team_id, job_id)
            state = self._load(path)
            sessions = dict(state.sessions)
            targets = dict(state.original_targets)
            sessions.pop(session_id, None)
            targets.pop(session_id, None)
            self._save(path, _StoredRegistry(sessions=sessions, original_targets=targets))

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
        with exclusive_lock(job_lock_path(self.job_store.path(team_id, job_id)), wait=True):
            path = self._access_path(team_id, job_id)
            state = self._load(path)
            if context.session_id not in state.sessions:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized")
            current = list(state.original_targets.get(context.session_id, ()))
            if ref not in current:
                current.append(ref)
            targets = dict(state.original_targets)
            targets[context.session_id] = tuple(current)
            self._save(path, _StoredRegistry(sessions=dict(state.sessions), original_targets=targets))

    def _require_running(
        self,
        authority: JobAuthorityRef,
        *,
        expected_context: AgentTicketContext | None = None,
    ):
        record = self.job_store.read(authority)
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

    def _access_path(self, team_id: str, job_id: str) -> Path:
        root = self.job_store.artifact_root(team_id, job_id)
        root.mkdir(parents=True, exist_ok=True)
        return root / "ticket-access.json"

    def _load(self, path: Path) -> _StoredRegistry:
        if not path.exists():
            return _StoredRegistry()
        return _StoredRegistry.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, path: Path, state: _StoredRegistry) -> None:
        if not state.sessions and not state.original_targets:
            if path.exists():
                path.unlink()
            return
        atomic_write_text(path, state.model_dump_json(indent=2))