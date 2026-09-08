from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable
from uuid import uuid4

from flowgency.configuration.store import ConfigStore
from flowgency.jobs.authority import JobAuthorityRef, JobStore
from flowgency.tickets.access import OriginalTicketTarget, TicketAccessRegistry
from flowgency.tickets.errors import TicketConflict, TicketForbidden, WorkflowUnavailable
from flowgency.tickets.models import (
    TicketOperation,
    TicketRecord,
    TicketRef,
    TicketRunReservation,
    TicketVersion,
    UserTicketContext,
)
from flowgency.tickets.service import TicketService
from flowgency.workflows.configuration import resolve_workflow_binding
from flowgency.workflows.locking import workflow_operation

from .models import JobHandle, JobRecord, JobRequest, TicketJobTarget
from .processes import ProcessStopEvidence
from .store import read_job


@dataclass(frozen=True)
class CleanupResult:
    cleared: tuple[TicketRef, ...] = ()
    pending_cleanup: tuple[TicketRef, ...] = ()


@dataclass(frozen=True)
class _ReservationPlan:
    handle: JobHandle | None
    request: JobRequest | None
    ref: TicketRef
    assignment_event_id: str
    job_id: str


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
        plan = self._reserve(actor, version, operation_id)
        if plan.handle is not None:
            return plan.handle
        assert plan.request is not None
        try:
            handle = self.submitter(plan.request)
        except Exception:
            self._reconcile_pending(
                plan.ref,
                plan.job_id,
                plan.assignment_event_id,
                clear_missing_job=True,
            )
            raise
        self._reconcile_pending(
            plan.ref,
            plan.job_id,
            plan.assignment_event_id,
            clear_missing_job=False,
        )
        return handle

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
            assignment_event_id = ""
            if record.spec.ticket_target is not None and target.ref == record.spec.ticket_target.ref:
                assignment_event_id = record.spec.ticket_target.assignment_event_id
            try:
                updated = self.service._cleanup_job_target(
                    target.binding,
                    target.ref,
                    job_id=record.spec.job_id,
                    assignment_event_id=assignment_event_id,
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
        initial = self.service._resolve_current_binding(version.ref.team_id, version.ref.workflow_id)
        with workflow_operation(
            self.config_store,
            (version.ref.team_id,),
            (initial.blueprint_id,),
        ) as snapshot:
            binding = resolve_workflow_binding(snapshot, version.ref.team_id, version.ref.workflow_id)
            if binding.blueprint_id != initial.blueprint_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            self.service._require_current_version(binding, version)
            _, workflow_snapshot = self.service._resolve_definition(binding, snapshot=snapshot)
            if workflow_snapshot.digest != version.workflow_digest:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            provider = self.service.storage_factory(binding.storage)
            current = provider.read(version.ref)
            if current.revision != version.revision:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            if current.assignee is None:
                raise TicketConflict("unassigned-ticket", "Assign the ticket before running it")
            if current.active_run is not None:
                raise TicketConflict("already-working", "Ticket is currently active")
            assignment_event_id = _latest_assignment_event_id(current)
            if not assignment_event_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            pending = current.pending_run
            if pending is not None and _reservation_matches_current(current, pending, assignment_event_id):
                existing = self._existing_handle(version.ref.team_id, pending.job_id)
                if existing is not None:
                    if pending.request_id == operation_id:
                        return _ReservationPlan(
                            handle=existing,
                            request=None,
                            ref=version.ref,
                            assignment_event_id=assignment_event_id,
                            job_id=pending.job_id,
                        )
                    raise TicketConflict("already-queued", "Ticket already has a queued run")
                if pending.request_id != operation_id:
                    raise TicketConflict("already-queued", "Ticket already has a queued run")
                job_id = pending.job_id
            else:
                job_id = uuid4().hex
            reservation = TicketRunReservation(
                job_id=job_id,
                request_id=operation_id,
                assignee=current.assignee,
                assignment_event_id=assignment_event_id,
            )
            operation = _reservation_operation(actor, version, operation_id, job_id)
            provider.apply(
                version.ref,
                version.revision,
                operation,
                lambda record: record.model_copy(update={"pending_run": reservation}),
            )
            target = TicketJobTarget(
                binding=binding.storage,
                ref=version.ref,
                assigned_agent=current.assignee,
                assignment_event_id=assignment_event_id,
                context_digest=binding.context_digest,
            )
            request = JobRequest(
                config_path=self.config_store.path,
                team_key=version.ref.team_id,
                agent_name=current.assignee,
                trigger="ticket",
                task_input=_ticket_task_input(current),
                job_id=job_id,
                ticket_target=target,
            )
            return _ReservationPlan(
                handle=None,
                request=request,
                ref=version.ref,
                assignment_event_id=assignment_event_id,
                job_id=job_id,
            )

    def _reconcile_pending(
        self,
        ref: TicketRef,
        job_id: str,
        assignment_event_id: str,
        *,
        clear_missing_job: bool,
    ) -> None:
        initial = self.service._resolve_current_binding(ref.team_id, ref.workflow_id)
        with workflow_operation(
            self.config_store,
            (ref.team_id,),
            (initial.blueprint_id,),
        ) as snapshot:
            binding = resolve_workflow_binding(snapshot, ref.team_id, ref.workflow_id)
            provider = self.service.storage_factory(binding.storage)
            current = provider.read(ref)
            pending = current.pending_run
            if pending is None or pending.job_id != job_id or pending.assignment_event_id != assignment_event_id:
                return
            current_assignment_event_id = _latest_assignment_event_id(current)
            job_exists = self.job_store.path(ref.team_id, job_id).exists()
            should_clear = (
                (clear_missing_job and not job_exists)
                or current.assignee != pending.assignee
                or current_assignment_event_id != pending.assignment_event_id
            )
            if not should_clear:
                return
            operation = TicketOperation(
                operation_id=f"reconcile-{job_id}",
                request_digest=hashlib.sha256(
                    json.dumps(
                        {
                            "binding_id": ref.binding_id,
                            "job_id": job_id,
                            "assignment_event_id": assignment_event_id,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            )
            provider.apply(
                ref,
                current.revision,
                operation,
                lambda record: record.model_copy(update={"pending_run": None}),
            )

    def _existing_handle(self, team_id: str, job_id: str) -> JobHandle | None:
        path = self.job_store.path(team_id, job_id)
        if not path.exists():
            return None
        record = read_job(path)
        return JobHandle(job_id, record.status, path, record.worker_pid)


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


def _ticket_task_input(record: TicketRecord) -> str:
    return (
        "Use the live ticket tools to inspect the currently assigned ticket and continue the work.\n\n"
        f"Ticket: {record.id}\n"
        f"Title: {record.title}\n"
        f"State: {record.state_id}\n"
    )
