from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable
import uuid

from pydantic import BaseModel, ConfigDict
import yaml

from flowgency.configuration.store import ConfigStore
from flowgency.fs.atomic import atomic_write_text
from flowgency.fs.locks import exclusive_lock
from flowgency.jobs.authority import JobAuthorityRef, JobStore
from flowgency.tickets.access import OriginalTicketTarget, TicketAccessRegistry
from flowgency.tickets.errors import (
    StorageUnavailable,
    TicketConflict,
    TicketForbidden,
    WorkflowUnavailable,
)
from flowgency.tickets.models import (
    TicketOperation,
    TicketEvent,
    TicketRecord,
    TicketRef,
    TicketRunReservation,
    TicketVersion,
    TicketView,
    UserTicketContext,
)
from flowgency.tickets.service import TicketService
from flowgency.workflows.configuration import resolve_workflow_binding
from flowgency.workflows.locking import workflow_operation

from .models import JobHandle, JobRecord, JobRequest, TicketJobTarget
from .processes import ProcessStopEvidence
from .store import cancel_job, job_lock_path, read_job


@dataclass(frozen=True)
class CleanupResult:
    cleared: tuple[TicketRef, ...] = ()
    pending_cleanup: tuple[TicketRef, ...] = ()


@dataclass(frozen=True)
class ReservationRecoveryResult:
    status: str
    recovery_kind: str | None = None
    handle: JobHandle | None = None


@dataclass(frozen=True)
class _ReservationPlan:
    handle: JobHandle | None
    request: JobRequest | None
    ref: TicketRef
    assignment_event_id: str
    job_id: str
    reservation: _DurableReservation


class TicketReservationError(RuntimeError):
    pass


class _ReservationRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str
    message: str


class _DurableReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    operation_id: str
    request_digest: str
    actor_team_id: str
    actor_name: str
    original_revision: int
    job_id: str
    target: TicketJobTarget
    authority_digest: str | None = None
    status: str = "reserved"
    recovery: _ReservationRecovery | None = None


class TicketJobCoordinator:
    def __init__(
        self,
        *,
        service: TicketService,
        job_store: JobStore,
        config_store: ConfigStore,
        submitter: Callable[[JobRequest], JobHandle],
    ) -> None:
        self.service = service
        self.job_store = job_store
        self.config_store = config_store
        self.submitter = submitter
        self.registry = TicketAccessRegistry(job_store)

    def submit(
        self,
        actor: UserTicketContext,
        version: TicketVersion,
        operation_id: str,
    ) -> JobHandle:
        if not isinstance(actor, UserTicketContext):
            raise TicketForbidden("forbidden", "Only a user may enqueue a ticket run")
        self.service._require_team_access(actor, version.ref.team_id)
        plan = self._reserve(actor, version, operation_id)
        if plan.handle is not None:
            return plan.handle
        assert plan.request is not None
        try:
            handle = self.submitter(plan.request)
        except Exception:
            self._reconcile_pending(
                plan.reservation,
                clear_missing_job=True,
            )
            raise
        self._reconcile_pending(
            plan.reservation,
            clear_missing_job=False,
        )
        refreshed = self._read_reservation(plan.ref.team_id, plan.ref, operation_id)
        if refreshed is not None and refreshed.authority_digest is not None:
            return self._verified_handle(refreshed)
        return handle

    def reconcile_pending_submission(
        self,
        team_id: str,
        ref: TicketRef,
        operation_id: str,
    ) -> ReservationRecoveryResult | None:
        reservation = self._read_reservation(team_id, ref, operation_id)
        if reservation is None:
            return None
        authority = self._verify_existing_job(reservation)
        self._reconcile_pending(
            reservation,
            clear_missing_job=authority is None,
        )
        refreshed = self._read_reservation(team_id, ref, operation_id)
        if refreshed is None:
            return None
        handle = None
        if refreshed.authority_digest is not None:
            handle = self._verified_handle(refreshed)
        recovery_kind = None if refreshed.recovery is None else refreshed.recovery.kind
        return ReservationRecoveryResult(
            status=refreshed.status,
            recovery_kind=recovery_kind,
            handle=handle,
        )

    def iter_reservations(self, team_id: str) -> tuple[_DurableReservation, ...]:
        root = self._reservation_root(team_id, create=False)
        if not root.exists():
            return ()
        reservations: list[_DurableReservation] = []
        for path in sorted(root.glob("*.json")):
            if path.parent != root:
                raise TicketReservationError("ticket reservation path escaped the authoritative store")
            with exclusive_lock(job_lock_path(path), wait=True):
                reservations.append(
                    _DurableReservation.model_validate_json(path.read_text(encoding="utf-8"))
                )
        return tuple(reservations)

    def preflight(self, record: JobRecord):
        target = record.spec.ticket_target
        if target is None:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        view = self.service.inspect(
            UserTicketContext(team_id=target.ref.team_id, actor_name="ticket-preflight"),
            target.ref,
        )
        if view.version is None:
            raise WorkflowUnavailable("unavailable-workflow", "Current workflow definition is unavailable")
        assignment_event_id = _latest_assignment_event_id(view.record)
        if (
            view.record.assignee != target.assigned_agent
            or assignment_event_id != target.assignment_event_id
            or view.version.context_digest != target.context_digest
            or view.version.ref.binding_id != target.binding.binding_id
            or view.record.pending_run is None
            or view.record.pending_run.job_id != record.spec.job_id
            or view.record.pending_run.assignee != target.assigned_agent
            or view.record.pending_run.assignment_event_id != target.assignment_event_id
        ):
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        return view

    def cleanup(
        self,
        authority: JobAuthorityRef,
        stopped: ProcessStopEvidence,
    ) -> CleanupResult:
        record = self.job_store.read(authority)
        targets = list(self.registry.read_original_targets(authority))
        if not targets and record.spec.ticket_target is not None:
            targets.append(
                OriginalTicketTarget(
                    generation="",
                    binding=record.spec.ticket_target.binding,
                    ref=record.spec.ticket_target.ref,
                )
            )
        if not targets:
            return CleanupResult()
        if not stopped.confirmed:
            return CleanupResult(pending_cleanup=tuple(target.ref for target in targets))
        cleared: list[TicketRef] = []
        pending: list[TicketRef] = []
        for target in targets:
            expected_generation = target.generation if target.generation == stopped.generation else None
            assignment_event_id = ""
            if record.spec.ticket_target is not None and target.ref == record.spec.ticket_target.ref:
                assignment_event_id = record.spec.ticket_target.assignment_event_id
            try:
                updated = self.service._cleanup_job_target(
                    target.binding,
                    target.ref,
                    job_id=record.spec.job_id,
                    assignment_event_id=assignment_event_id,
                    expected_generation=expected_generation,
                    stopped=stopped,
                )
            except Exception:
                pending.append(target.ref)
                continue
            if updated.active_run is None and (
                updated.pending_run is None or updated.pending_run.job_id != record.spec.job_id
            ):
                cleared.append(target.ref)
            else:
                pending.append(target.ref)
        return CleanupResult(cleared=tuple(cleared), pending_cleanup=tuple(pending))

    def _reserve(
        self,
        actor: UserTicketContext,
        version: TicketVersion,
        operation_id: str,
    ) -> _ReservationPlan:
        request_digest = _request_digest(actor, version, operation_id)
        initial = self.service._resolve_current_binding(version.ref.team_id, version.ref.workflow_id)
        with workflow_operation(
            self.config_store,
            (version.ref.team_id,),
            (initial.blueprint_id,),
        ) as snapshot:
            binding = resolve_workflow_binding(snapshot, version.ref.team_id, version.ref.workflow_id)
            if binding.blueprint_id != initial.blueprint_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            provider = self.service.storage_factory(binding.storage)
            current = provider.read(version.ref)
            if current.assignee is None:
                raise TicketConflict("unassigned-ticket", "Assign the ticket before running it")
            self._require_ticket_capable_assignee(snapshot, version.ref.team_id, current.assignee)
            if current.active_run is not None:
                raise TicketConflict("already-working", "Ticket is currently active")
            assignment_event_id = _latest_assignment_event_id(current)
            if not assignment_event_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            target = TicketJobTarget(
                binding=binding.storage,
                ref=version.ref,
                assigned_agent=current.assignee,
                assignment_event_id=assignment_event_id,
                context_digest=binding.context_digest,
            )
            existing = self._read_reservation(version.ref.team_id, version.ref, operation_id)
            replay = self._reservation_state(
                actor,
                version,
                operation_id,
                request_digest,
                target,
                existing,
            )
            replay_handle = self._replay_handle(target, current, replay)
            if replay_handle is not None:
                return _ReservationPlan(
                    handle=replay_handle,
                    request=None,
                    ref=version.ref,
                    assignment_event_id=assignment_event_id,
                    job_id=replay_handle.job_id,
                    reservation=replay,
                )
            if existing is None:
                self._require_new_request_freshness(snapshot, binding, current, version)
            pending = current.pending_run
            if pending is not None and _reservation_matches_current(current, pending, assignment_event_id):
                if pending.request_id != replay.operation_id and not self._may_replace_terminal_pending(ref=version.ref, pending=pending):
                    raise TicketConflict("already-queued", "Ticket already has a queued run")
            if not _target_matches_current(current, target):
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            reservation = TicketRunReservation(
                job_id=replay.job_id,
                request_id=replay.operation_id,
                assignee=current.assignee,
                assignment_event_id=assignment_event_id,
            )
            operation = _reservation_operation(actor, version, operation_id, replay.job_id)
            provider.apply(
                version.ref,
                version.revision,
                operation,
                lambda record: _apply_pending_mutation(
                    record,
                    actor=actor.actor_name,
                    kind="ticket-run-reserved",
                    summary="Ticket run reserved",
                    pending_run=reservation,
                ),
            )
            self._write_reservation(replay.model_copy(update={"status": "reserved", "recovery": None}))
            exact_handle = self._existing_handle(replay)
            if exact_handle is not None:
                return _ReservationPlan(
                    handle=exact_handle,
                    request=None,
                    ref=version.ref,
                    assignment_event_id=assignment_event_id,
                    job_id=replay.job_id,
                    reservation=replay,
                )
            request = JobRequest(
                config_path=self.config_store.path,
                team_key=version.ref.team_id,
                agent_name=current.assignee,
                trigger="ticket",
                task_input=_ticket_task_input(self.service.inspect(actor, version.ref)),
                job_id=replay.job_id,
                ticket_target=target,
            )
            return _ReservationPlan(
                handle=None,
                request=request,
                ref=version.ref,
                assignment_event_id=assignment_event_id,
                job_id=replay.job_id,
                reservation=replay,
            )

    def _reconcile_pending(
        self,
        reservation: _DurableReservation,
        *,
        clear_missing_job: bool,
    ) -> None:
        ref = reservation.target.ref
        authority = self._verify_existing_job(reservation)
        linked = reservation
        if authority is not None and reservation.authority_digest != authority.immutable_digest:
            linked = reservation.model_copy(
                update={
                    "authority_digest": authority.immutable_digest,
                    "status": "linked",
                    "recovery": None,
                }
            )
            self._write_reservation(linked)
        provider = self.service.storage_factory(reservation.target.binding)
        try:
            current = provider.read(ref)
        except StorageUnavailable as error:
            self._write_recovery(linked, error)
            return None
        current_assignment_event_id = _latest_assignment_event_id(current)
        try:
            current_binding = self.service._resolve_current_binding(ref.team_id, ref.workflow_id)
        except Exception:
            current_binding = None
        if authority is None and clear_missing_job:
            pending = current.pending_run
            if pending is None or pending.job_id != reservation.job_id:
                self._write_reservation(linked.model_copy(update={"status": "retryable", "recovery": None}))
                return None
            try:
                provider.apply(
                    ref,
                    current.revision,
                    _reconcile_operation(ref, reservation.job_id, reservation.target.assignment_event_id, "missing"),
                    lambda record: _apply_pending_mutation(
                        record,
                        actor="system",
                        kind="ticket-run-reconciled",
                        summary="Ticket run reservation reconciled",
                        pending_run=None,
                    ),
                )
            except StorageUnavailable as error:
                self._write_recovery(linked, error)
                return None
            self._write_reservation(linked.model_copy(update={"status": "retryable", "recovery": None}))
            return None
        if authority is None:
            return None
        record = self.job_store.read(authority)
        if record.status in _TERMINAL_STATUSES:
            if self._may_clear_terminal_pending(authority, record, current, linked):
                try:
                    provider.apply(
                        ref,
                        current.revision,
                        _reconcile_operation(ref, linked.job_id, linked.target.assignment_event_id, record.status),
                        lambda existing: _apply_pending_mutation(
                            existing,
                            actor="system",
                            kind="ticket-run-reconciled",
                            summary="Ticket run reservation reconciled",
                            pending_run=None,
                        ),
                    )
                except StorageUnavailable as error:
                    self._write_recovery(linked, error)
                    return None
            self._write_reservation(linked.model_copy(update={"status": "linked", "recovery": None}))
            return None
        stale_target = (
            current.assignee != linked.target.assigned_agent
            or current_assignment_event_id != linked.target.assignment_event_id
            or current_binding is None
            or current_binding.storage.binding_id != linked.target.binding.binding_id
            or current_binding.context_digest != linked.target.context_digest
        )
        if stale_target:
            self._cancel_if_queued(authority)
            try:
                current = provider.read(ref)
            except StorageUnavailable as error:
                self._write_recovery(linked, error)
                return None
            pending = current.pending_run
            if pending is not None and pending.job_id == linked.job_id:
                try:
                    provider.apply(
                        ref,
                        current.revision,
                        _reconcile_operation(ref, linked.job_id, linked.target.assignment_event_id, "stale"),
                        lambda record: _apply_pending_mutation(
                            record,
                            actor="system",
                            kind="ticket-run-reconciled",
                            summary="Ticket run reservation reconciled",
                            pending_run=None,
                        ),
                    )
                except StorageUnavailable as error:
                    self._write_recovery(linked, error)
                    return None
            self._write_reservation(linked.model_copy(update={"status": "stale", "recovery": None}))
            return None
        pending = current.pending_run
        if pending is None or pending.job_id != linked.job_id:
            try:
                provider.apply(
                    ref,
                    current.revision,
                    _reconcile_operation(ref, linked.job_id, linked.target.assignment_event_id, "linked"),
                    lambda record: _apply_pending_mutation(
                        record,
                        actor="system",
                        kind="ticket-run-reconciled",
                        summary="Ticket run reservation reconciled",
                        pending_run=TicketRunReservation(
                            job_id=linked.job_id,
                            request_id=linked.operation_id,
                            assignee=linked.target.assigned_agent,
                            assignment_event_id=linked.target.assignment_event_id,
                        ),
                    ),
                )
            except StorageUnavailable as error:
                self._write_recovery(linked, error)
                return None
        self._write_reservation(linked.model_copy(update={"status": "linked", "recovery": None}))
        return None

    def _existing_handle(self, reservation: _DurableReservation) -> JobHandle | None:
        if reservation.authority_digest is None:
            return None
        return self._verified_handle(reservation)

    def _verified_handle(self, reservation: _DurableReservation) -> JobHandle:
        authority = self.job_store.reference(
            reservation.target.ref.team_id,
            reservation.job_id,
            reservation.authority_digest or "",
        )
        record = self.job_store.read(authority)
        self._require_job_matches_reservation(record, reservation)
        return JobHandle(reservation.job_id, record.status, authority.path, record.worker_pid)

    def _verify_existing_job(self, reservation: _DurableReservation) -> JobAuthorityRef | None:
        path = self.job_store.path(reservation.target.ref.team_id, reservation.job_id)
        if not path.exists():
            return None
        try:
            if reservation.authority_digest is not None:
                authority = self.job_store.reference(
                    reservation.target.ref.team_id,
                    reservation.job_id,
                    reservation.authority_digest,
                )
                record = self.job_store.read(authority)
            else:
                record = read_job(path)
                authority = self.job_store.reference(
                    reservation.target.ref.team_id,
                    reservation.job_id,
                    record.authority_digest,
                )
                self.job_store.read(authority)
        except Exception as error:
            raise TicketReservationError("durable ticket reservation metadata did not verify") from error
        self._require_job_matches_reservation(record, reservation)
        return authority

    def _require_job_matches_reservation(
        self,
        record: JobRecord,
        reservation: _DurableReservation,
    ) -> None:
        target = record.spec.ticket_target
        if target is None:
            raise TicketReservationError("reserved job is missing its ticket target")
        if record.spec.job_id != reservation.job_id:
            raise TicketReservationError("reserved job identity does not match the durable reservation")
        if record.spec.team_key != reservation.target.ref.team_id:
            raise TicketReservationError("reserved job team does not match the durable reservation")
        if record.spec.agent_name != reservation.target.assigned_agent:
            raise TicketReservationError("reserved job assignee does not match the durable reservation")
        if target != reservation.target:
            raise TicketReservationError("reserved job target does not match the durable reservation")

    def _reservation_path(self, team_id: str, ref: TicketRef, operation_id: str, *, create: bool) -> Path:
        root = self._reservation_root(team_id, create=create)
        key = hashlib.sha256(
            json.dumps(
                {
                    "binding_id": ref.binding_id,
                    "workflow_id": ref.workflow_id,
                    "ticket_id": ref.ticket_id,
                    "operation_id": operation_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        path = (root / f"{key}.json").resolve(strict=False)
        if path.parent != root:
            raise TicketReservationError("ticket reservation path escaped the authoritative store")
        return path

    def _reservation_root(self, team_id: str, *, create: bool) -> Path:
        team_root = self.job_store.team_root(team_id)
        root = (team_root / "ticket-runs").resolve(strict=False)
        if root.parent != team_root.resolve(strict=False):
            raise TicketReservationError("ticket reservation root escaped the authoritative store")
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def _read_reservation(
        self,
        team_id: str,
        ref: TicketRef,
        operation_id: str,
    ) -> _DurableReservation | None:
        path = self._reservation_path(team_id, ref, operation_id, create=False)
        if not path.exists():
            return None
        with exclusive_lock(job_lock_path(path), wait=True):
            return _DurableReservation.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_reservation(self, reservation: _DurableReservation) -> None:
        path = self._reservation_path(
            reservation.target.ref.team_id,
            reservation.target.ref,
            reservation.operation_id,
            create=True,
        )
        with exclusive_lock(job_lock_path(path), wait=True):
            atomic_write_text(
                path,
                reservation.model_dump_json(
                    indent=2,
                    exclude={"target": {"binding": {"binding_id"}}},
                )
                + "\n",
            )

    def _reservation_state(
        self,
        actor: UserTicketContext,
        version: TicketVersion,
        operation_id: str,
        request_digest: str,
        target: TicketJobTarget,
        existing: _DurableReservation | None,
    ) -> _DurableReservation:
        job_id = _reserved_job_id(version.ref, operation_id)
        if existing is None:
            return _DurableReservation(
                operation_id=operation_id,
                request_digest=request_digest,
                actor_team_id=actor.team_id,
                actor_name=actor.actor_name,
                original_revision=version.revision,
                job_id=job_id,
                target=target,
            )
        if existing.request_digest != request_digest:
            raise TicketConflict("already-queued", "Ticket already has a queued run")
        if existing.actor_team_id != actor.team_id or existing.actor_name != actor.actor_name:
            raise TicketConflict("already-queued", "Ticket already has a queued run")
        return existing

    def _replay_handle(
        self,
        current_target: TicketJobTarget,
        current: TicketRecord,
        reservation: _DurableReservation,
    ) -> JobHandle | None:
        refreshed = self.reconcile_pending_submission(
            reservation.target.ref.team_id,
            reservation.target.ref,
            reservation.operation_id,
        )
        if refreshed is not None:
            reservation = self._read_reservation(
                reservation.target.ref.team_id,
                reservation.target.ref,
                reservation.operation_id,
            ) or reservation
        handle = self._existing_handle(reservation)
        if handle is None:
            return None
        if handle.status in _TERMINAL_STATUSES:
            return handle
        if current.pending_run is None:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if current.pending_run.job_id != reservation.job_id or current.pending_run.request_id != reservation.operation_id:
            raise TicketConflict("already-queued", "Ticket already has a queued run")
        if reservation.target != current_target:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        return handle

    def _may_replace_terminal_pending(
        self,
        *,
        ref: TicketRef,
        pending: TicketRunReservation,
    ) -> bool:
        reservation = self._read_reservation(ref.team_id, ref, pending.request_id)
        if reservation is None or reservation.job_id != pending.job_id:
            return False
        authority = self._verify_existing_job(reservation)
        if authority is None:
            return False
        record = self.job_store.read(authority)
        return record.status in _TERMINAL_STATUSES

    def _require_new_request_freshness(
        self,
        snapshot,
        binding,
        current: TicketRecord,
        version: TicketVersion,
    ) -> None:
        self.service._require_current_version(binding, version)
        if current.revision != version.revision:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        _, workflow_snapshot = self.service._resolve_definition(binding, snapshot=snapshot)
        if workflow_snapshot.digest != version.workflow_digest:
            raise TicketConflict("stale-ticket", "Refresh the ticket")

    def _write_recovery(
        self,
        reservation: _DurableReservation,
        error: StorageUnavailable,
    ) -> None:
        self._write_reservation(
            reservation.model_copy(
                update={
                    "status": "recovery-pending",
                    "recovery": _ReservationRecovery(
                        kind="original-storage-unavailable",
                        message=str(error),
                    ),
                }
            )
        )

    def _require_ticket_capable_assignee(self, snapshot, team_id: str, agent_name: str) -> None:
        team = snapshot.config.teams[team_id]
        try:
            agent = team.agents[agent_name]
        except KeyError as error:
            raise TicketConflict("stale-ticket", "Refresh the ticket") from error
        from . import submission as submission_module

        integration = submission_module.REGISTRY.get(agent.integration)
        if integration is None:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if not integration.supports_execution:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if integration.runtime_capabilities.live_ticket_transport is None:
            raise TicketConflict("stale-ticket", "Refresh the ticket")

    def _cancel_if_queued(self, authority: JobAuthorityRef) -> None:
        try:
            cancel_job(authority.path)
        except Exception:
            return

    def _may_clear_terminal_pending(
        self,
        authority: JobAuthorityRef,
        record: JobRecord,
        current: TicketRecord,
        reservation: _DurableReservation,
    ) -> bool:
        pending = current.pending_run
        if pending is None or pending.job_id != reservation.job_id:
            return False
        if current.active_run is not None:
            return False
        if self.registry.read_original_targets(authority):
            return False
        if record.status == "cancelled":
            return True
        return not any(
            value not in (None, "")
            for value in (
                record.worker_pid,
                record.launched_at,
                record.started_at,
                record.session_id,
            )
        )


def _latest_assignment_event_id(record: TicketRecord) -> str:
    for event in reversed(record.events):
        if event.kind == "assigned":
            return event.id
    return ""


def _reservation_matches_current(
    record: TicketRecord,
    pending: TicketRunReservation,
    assignment_event_id: str,
) -> bool:
    return (
        record.assignee == pending.assignee
        and pending.assignment_event_id == assignment_event_id
    )


def _target_matches_current(record: TicketRecord, target: TicketJobTarget) -> bool:
    return (
        record.assignee == target.assigned_agent
        and _latest_assignment_event_id(record) == target.assignment_event_id
        and record.ref == target.ref
    )


def _reservation_operation(
    actor: UserTicketContext,
    version: TicketVersion,
    operation_id: str,
    job_id: str,
) -> TicketOperation:
    payload = {
        "team_id": actor.team_id,
        "ticket": version.ref.model_dump(mode="json"),
        "operation_id": operation_id,
        "job_id": job_id,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TicketOperation(operation_id=f"ticket-run-{operation_id}", request_digest=digest)


def _reconcile_operation(
    ref: TicketRef,
    job_id: str,
    assignment_event_id: str,
    reason: str,
) -> TicketOperation:
    payload = {
        "binding_id": ref.binding_id,
        "job_id": job_id,
        "assignment_event_id": assignment_event_id,
        "reason": reason,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TicketOperation(operation_id=f"reconcile-{job_id}-{reason}", request_digest=digest)


def _request_digest(
    actor: UserTicketContext,
    version: TicketVersion,
    operation_id: str,
) -> str:
    payload = {
        "team_id": actor.team_id,
        "actor_name": actor.actor_name,
        "binding_id": version.ref.binding_id,
        "workflow_id": version.ref.workflow_id,
        "ticket_id": version.ref.ticket_id,
        "context_digest": version.context_digest,
        "operation_id": operation_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})


def _reserved_job_id(ref: TicketRef, operation_id: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"ticket-run:{ref.binding_id}:{ref.team_id}:{ref.workflow_id}:{ref.ticket_id}:{operation_id}",
    ).hex


def _apply_pending_mutation(
    record: TicketRecord,
    *,
    actor: str,
    kind: str,
    summary: str,
    pending_run: TicketRunReservation | None,
) -> TicketRecord:
    return record.model_copy(
        update={
            "pending_run": pending_run,
            "events": record.events + (TicketEvent(kind=kind, actor=actor, summary=summary),),
        }
    )


def _ticket_task_input(view: TicketView) -> str:
    if view.version is None or view.definition is None:
        raise WorkflowUnavailable(
            "unavailable-workflow",
            "Current workflow definition is unavailable",
        )
    record = view.record
    ticket_payload = {
        "ref": view.ref.model_dump(mode="json"),
        "revision": view.version.revision,
        "title": record.title,
        "description": record.description,
        "state_id": record.state_id,
        "assignee": record.assignee,
        "field_values": record.model_dump(mode="json")["field_values"],
    }
    workflow_payload = view.definition.model_dump(mode="json")
    ticket_yaml = yaml.safe_dump(ticket_payload, sort_keys=False, allow_unicode=True).strip()
    workflow_yaml = yaml.safe_dump(workflow_payload, sort_keys=False, allow_unicode=True).strip()
    return (
        "Use the live ticket tools to inspect the current ticket state from the project, "
        "re-evaluate the workflow contract below against the actual repository, and continue the work. "
        "Do not rely on any stale earlier snapshot.\n\n"
        "## Current ticket\n"
        "```yaml\n"
        f"{ticket_yaml}\n"
        "```\n\n"
        "## Current workflow definition\n"
        "```yaml\n"
        f"{workflow_yaml}\n"
        "```\n"
    )
