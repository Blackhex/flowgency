from __future__ import annotations

from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from flowgency.jobs.authority import JobStore
from flowgency.jobs.store import TERMINAL_STATUSES
from flowgency.tickets.artifacts import iter_internal_artifact_refs
from flowgency.tickets.errors import TicketStorageError, WorkflowUnavailable
from flowgency.tickets.models import TicketEvent, TicketRef, TicketVersion, TicketView, UserTicketContext
from flowgency.tickets.service import TicketService
from flowgency.workflows.configuration import WorkflowBinding
from flowgency.workflows.models import FieldDefinition


class ViewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    history: tuple[str, ...] = ()


class WorkflowBindingView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    team_id: str
    workflow_id: str
    blueprint_id: str
    binding_id: str
    context_digest: str


class TicketEventView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    kind: str
    actor: str
    summary: str
    at: Any = None
    job_id: str | None = None


class TicketAuditEventView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    kind: str
    actor: str
    summary: str
    at: Any = None
    data: dict[str, Any] = {}


class TicketFieldProvenanceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    actor_kind: str
    actor_name: str
    job_id: str | None = None
    event_id: str
    recorded_at: Any


class TicketFieldValueView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    type: str | None = None
    artifact_format: Literal["git-change"] | None = None
    value: Any = None
    provenance: TicketFieldProvenanceView | None = None
    is_output: bool = False


class WorkflowStateView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    color: str


class WorkflowFieldDefinitionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    type: str
    artifact_format: Literal["git-change"] | None = None


class WorkflowFieldUseView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field_id: str
    required: bool


class WorkflowCriterionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    description: str


class WorkflowPreconditionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field_id: str
    field_label: str
    field_type: str
    operator: str
    comparison: Any = None


class WorkflowTransitionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    from_state: str
    to_state: str
    inputs: tuple[WorkflowFieldUseView, ...] = ()
    outputs: tuple[WorkflowFieldUseView, ...] = ()
    preconditions: tuple[WorkflowPreconditionView, ...] = ()
    criteria: tuple[WorkflowCriterionView, ...] = ()


class WorkflowDefinitionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    available: bool
    blueprint_id: str
    blueprint_name: str | None = None
    workflow_digest: str | None = None
    states: tuple[WorkflowStateView, ...] = ()
    fields: tuple[WorkflowFieldDefinitionView, ...] = ()
    transitions: tuple[WorkflowTransitionView, ...] = ()


class TicketSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: TicketRef
    version: TicketVersion | None
    number: int
    title: str
    description: str
    state_id: str
    state_name: str
    assignee: str | None
    pending_run_job_id: str | None
    pending_run_status: str | None = None
    pending_run_issue: ViewIssue | None = None
    active_run_job_id: str | None
    artifact_ids: tuple[str, ...] = ()
    issues: tuple[ViewIssue, ...] = ()
    history: tuple[TicketEventView, ...] = ()


class BoardColumnView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    kind: Literal["state", "invalid-data", "unavailable"]
    state_id: str | None
    name: str
    color: str
    count: int
    tickets: tuple[TicketSummaryView, ...] = ()


class TicketDetailView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: WorkflowBindingView
    name: str
    revision: str
    ticket: TicketSummaryView | None
    fields: tuple[TicketFieldValueView, ...] = ()
    current_definition: WorkflowDefinitionView
    history: tuple[TicketAuditEventView, ...] = ()
    issues: tuple[ViewIssue, ...] = ()


class BoardView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: WorkflowBindingView
    name: str
    revision: str
    query: str = ""
    assignee: str | None = None
    columns: tuple[BoardColumnView, ...] = ()
    ticket_count: int
    working_count: int
    selected_ticket: TicketDetailView | None = None
    issues: tuple[ViewIssue, ...] = ()


def _binding_view(binding: WorkflowBinding) -> WorkflowBindingView:
    return WorkflowBindingView(
        team_id=binding.team_id,
        workflow_id=binding.workflow_id,
        blueprint_id=binding.blueprint_id,
        binding_id=binding.storage.binding_id,
        context_digest=binding.context_digest,
    )


def _error_history(error: BaseException) -> tuple[str, ...]:
    history: list[str] = []
    current: BaseException | None = error
    while current is not None:
        text = str(current)
        if text and text not in history:
            history.append(text)
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, BaseException) else None
    return tuple(history)


def _issue_from_error(error: BaseException) -> ViewIssue:
    if isinstance(error, TicketStorageError):
        return ViewIssue(
            code=error.code,
            message=error.message,
            history=_error_history(error),
        )
    return ViewIssue(
        code="invalid-data",
        message=str(error) or "Workflow data is not readable",
        history=_error_history(error),
    )


def _ticket_issue_rows(view: TicketView) -> tuple[ViewIssue, ...]:
    issues: list[ViewIssue] = []
    if view.issues:
        code = "unavailable-workflow" if view.version is None else "ticket-issue"
        issues.extend(
            ViewIssue(code=code, message=message, history=(message,))
            for message in view.issues
        )
    if view.definition is not None:
        try:
            view.definition.state(view.record.state_id)
        except Exception:
            issues.append(
                ViewIssue(
                    code="invalid-data",
                    message="Ticket state is not present in the current workflow definition",
                    history=(view.record.state_id,),
                )
            )
    return tuple(issues)


def _state_name(view: TicketView) -> str:
    if view.definition is None:
        return view.record.state_id
    try:
        return view.definition.state(view.record.state_id).name
    except Exception:
        return view.record.state_id


def _latest_assignment_event_id(view: TicketView) -> str:
    for event in reversed(view.record.events):
        if event.kind == "assigned":
            return event.id
    return ""


def _reservation_issue(code: str, message: str, *history: str) -> ViewIssue:
    return ViewIssue(code=code, message=message, history=tuple(item for item in history if item))


def event_job_id(event: TicketEvent) -> str | None:
    job_id = event.data.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return None
    try:
        return JobStore._job_id(job_id)
    except ValueError:
        return None


def _reservation_rows(ticket_jobs, team_id: str) -> dict[tuple[str, str, str], tuple[Any, ...]]:
    if ticket_jobs is None:
        return {}
    rows: dict[tuple[str, str, str], list[Any]] = {}
    for reservation in ticket_jobs.iter_reservations(team_id):
        key = (
            reservation.target.ref.binding_id,
            reservation.target.ref.workflow_id,
            reservation.target.ref.ticket_id,
        )
        rows.setdefault(key, []).append(reservation)
    return {key: tuple(value) for key, value in rows.items()}


def _queued_run_projection(view: TicketView, reservations: tuple[Any, ...], ticket_jobs) -> tuple[str | None, str | None, ViewIssue | None]:
    pending = view.record.pending_run
    latest_assignment_event_id = _latest_assignment_event_id(view)
    if pending is not None and (
        view.record.assignee != pending.assignee
        or latest_assignment_event_id != pending.assignment_event_id
    ):
        return None, None, _reservation_issue(
            "pending-run-stale",
            "Queued run no longer matches the current assignment.",
            pending.job_id,
        )
    current_reservation = None
    if pending is not None:
        current_reservation = next(
            (
                reservation
                for reservation in reservations
                if reservation.operation_id == pending.request_id and reservation.job_id == pending.job_id
            ),
            None,
        )
    elif reservations:
        current_reservation = reservations[-1]
    if current_reservation is None:
        return None, None, None
    if pending is None:
        if current_reservation.status == "recovery-pending" and current_reservation.recovery is not None:
            return None, None, _reservation_issue(
                "pending-run-recovery",
                current_reservation.recovery.message,
                current_reservation.recovery.kind,
                current_reservation.job_id,
            )
        if current_reservation.status in {"retryable", "reserved", "linked"}:
            return None, None, _reservation_issue(
                "pending-run-retryable",
                "Queued run is pending retry or reconciliation.",
                current_reservation.status,
                current_reservation.job_id,
            )
        if current_reservation.status == "stale":
            return None, None, _reservation_issue(
                "pending-run-stale",
                "Queued run no longer matches the current ticket state.",
                current_reservation.job_id,
            )
        return None, None, None
    if view.version is not None and current_reservation.target.context_digest != view.version.context_digest:
        return None, None, _reservation_issue(
            "pending-run-stale",
            "Queued run no longer matches the current ticket state.",
            current_reservation.job_id,
        )
    try:
        authority = ticket_jobs._verify_existing_job(current_reservation)
    except Exception:
        return None, None, _reservation_issue(
            "pending-run-invalid",
            "Queued run metadata could not be verified.",
            current_reservation.job_id,
        )
    if authority is None:
        if current_reservation.status == "recovery-pending" and current_reservation.recovery is not None:
            return None, None, _reservation_issue(
                "pending-run-recovery",
                current_reservation.recovery.message,
                current_reservation.recovery.kind,
                current_reservation.job_id,
            )
        return None, None, _reservation_issue(
            "pending-run-retryable",
            "Queued run is pending retry or reconciliation.",
            current_reservation.status,
            current_reservation.job_id,
        )
    job = ticket_jobs.job_store.read(authority)
    if job.status in TERMINAL_STATUSES:
        return None, None, _reservation_issue(
            "pending-run-terminal",
            "Queued run has already reached a terminal durable job state.",
            job.status,
            job.spec.job_id,
        )
    return job.spec.job_id, job.status, None


def _summary_view(view: TicketView, *, reservations: tuple[Any, ...] = (), ticket_jobs=None) -> TicketSummaryView:
    artifact_ids = tuple(ref.value for ref in iter_internal_artifact_refs(dict(view.record.field_values)))
    pending_run_job_id, pending_run_status, pending_run_issue = _queued_run_projection(
        view,
        reservations,
        ticket_jobs,
    )
    return TicketSummaryView(
        ref=view.ref,
        version=view.version,
        number=view.record.number,
        title=view.record.title,
        description=view.record.description,
        state_id=view.record.state_id,
        state_name=_state_name(view),
        assignee=view.record.assignee,
        pending_run_job_id=pending_run_job_id,
        pending_run_status=pending_run_status,
        pending_run_issue=pending_run_issue,
        active_run_job_id=None if view.record.active_run is None else view.record.active_run.job_id,
        artifact_ids=artifact_ids,
        issues=_ticket_issue_rows(view),
        history=tuple(
            TicketEventView(
                id=event.id,
                kind=event.kind,
                actor=event.actor,
                summary=event.summary,
                at=event.at,
                job_id=event_job_id(event),
            )
            for event in view.record.events
        ),
    )


def _provenance_view(provenance) -> TicketFieldProvenanceView | None:
    if provenance is None:
        return None
    return TicketFieldProvenanceView(
        actor_kind=provenance.actor_kind,
        actor_name=provenance.actor_name,
        job_id=provenance.job_id,
        event_id=provenance.event_id,
        recorded_at=provenance.recorded_at,
    )


def output_field_definitions(view: TicketView) -> dict[str, FieldDefinition | None]:
    output_fields: dict[str, FieldDefinition | None] = {}
    for event in view.record.events:
        if event.kind != "transitioned" or not isinstance(event.data, dict):
            continue
        values = event.data.get("effective_outputs")
        if not isinstance(values, dict):
            continue
        snapshot = event.data.get("transition_snapshot")
        definitions = snapshot.get("field_defs") if isinstance(snapshot, dict) else None
        for field_id in values:
            if not isinstance(field_id, str) or not field_id:
                continue
            output_fields[field_id] = None
            raw = definitions.get(field_id) if isinstance(definitions, dict) else None
            if not isinstance(raw, dict):
                continue
            try:
                definition = FieldDefinition.model_validate(raw)
            except ValidationError:
                continue
            if definition.id == field_id:
                output_fields[field_id] = definition
    if view.definition is not None:
        for transition in view.definition.transitions:
            for use in transition.outputs:
                output_fields[use.field_id] = view.definition.field(use.field_id)
    return output_fields


def _field_rows(view: TicketView) -> tuple[TicketFieldValueView, ...]:
    definition_fields = () if view.definition is None else view.definition.fields
    ordered_ids = [field.id for field in definition_fields]
    for field_id in view.record.field_values:
        if field_id not in ordered_ids:
            ordered_ids.append(field_id)
    output_fields = output_field_definitions(view)
    for field_id in output_fields:
        if field_id not in ordered_ids:
            ordered_ids.append(field_id)
    definition_index = {field.id: field for field in definition_fields}
    rows = []
    for field_id in ordered_ids:
        current_definition = definition_index.get(field_id)
        fallback_definition = output_fields.get(field_id)
        label = field_id
        field_type: str | None = None
        artifact_format: str | None = None
        if current_definition is not None:
            label = current_definition.label
            field_type = current_definition.type
            artifact_format = current_definition.artifact_format
        elif fallback_definition is not None:
            label = fallback_definition.label
            field_type = fallback_definition.type
            artifact_format = fallback_definition.artifact_format
        rows.append(
            TicketFieldValueView(
                id=field_id,
                label=label,
                type=field_type,
                artifact_format=artifact_format,
                value=view.record.field_values.get(field_id),
                provenance=_provenance_view(view.record.field_provenance.get(field_id)),
                is_output=field_id in output_fields,
            )
        )
    return tuple(rows)


def _definition_view(view: TicketView, blueprint_id: str) -> WorkflowDefinitionView:
    if view.definition is None:
        return WorkflowDefinitionView(
            available=False,
            blueprint_id=blueprint_id,
            blueprint_name=None,
            workflow_digest=None,
        )
    field_index = {field.id: field for field in view.definition.fields}
    return WorkflowDefinitionView(
        available=True,
        blueprint_id=view.definition.id,
        blueprint_name=view.definition.name,
        workflow_digest=None if view.version is None else view.version.workflow_digest,
        states=tuple(
            WorkflowStateView(id=state.id, name=state.name, color=state.color)
            for state in view.definition.states
        ),
        fields=tuple(
            WorkflowFieldDefinitionView(
                id=field.id,
                label=field.label,
                type=field.type,
                artifact_format=field.artifact_format,
            )
            for field in view.definition.fields
        ),
        transitions=tuple(
            WorkflowTransitionView(
                id=transition.id,
                name=transition.name,
                from_state=transition.from_state,
                to_state=transition.to_state,
                inputs=tuple(
                    WorkflowFieldUseView(field_id=field_use.field_id, required=field_use.required)
                    for field_use in transition.inputs
                ),
                outputs=tuple(
                    WorkflowFieldUseView(field_id=field_use.field_id, required=field_use.required)
                    for field_use in transition.outputs
                ),
                preconditions=tuple(
                    WorkflowPreconditionView(
                        field_id=precondition.field_id,
                        field_label=field_index[precondition.field_id].label,
                        field_type=field_index[precondition.field_id].type,
                        operator=precondition.operator,
                        comparison=precondition.value,
                    )
                    for precondition in transition.preconditions
                ),
                criteria=tuple(
                    WorkflowCriterionView(id=criterion.id, description=criterion.description)
                    for criterion in transition.criteria
                ),
            )
            for transition in view.definition.transitions
        ),
    )


def _audit_history(view: TicketView) -> tuple[TicketAuditEventView, ...]:
    return tuple(
        TicketAuditEventView(
            id=event.id,
            kind=event.kind,
            actor=event.actor,
            summary=event.summary,
            at=event.at,
            data=dict(event.data),
        )
        for event in view.record.events
    )


def _matches_filter(view: TicketView, *, query: str, assignee: str | None) -> bool:
    if assignee is not None:
        if assignee == "unassigned":
            if view.record.assignee is not None:
                return False
        elif view.record.assignee != assignee:
            return False
    lowered = query.strip().lower()
    if not lowered:
        return True
    return lowered in view.record.title.lower() or lowered in view.record.description.lower()


def _has_known_state(view: TicketView) -> bool:
    if view.definition is None:
        return False
    try:
        view.definition.state(view.record.state_id)
    except Exception:
        return False
    return True


def _group_column(
    *,
    key: str,
    kind: Literal["state", "invalid-data", "unavailable"],
    state_id: str | None,
    name: str,
    color: str,
    tickets: tuple[TicketView, ...],
) -> BoardColumnView:
    return BoardColumnView(
        key=key,
        kind=kind,
        state_id=state_id,
        name=name,
        color=color,
        count=len(tickets),
        tickets=tuple(_summary_view(view) for view in tickets),
    )


def _empty_board(binding: WorkflowBinding, name: str, revision: str, *, query: str, assignee: str | None, issues: tuple[ViewIssue, ...]) -> BoardView:
    return BoardView(
        binding=_binding_view(binding),
        name=name,
        revision=revision,
        query=query,
        assignee=assignee,
        columns=(),
        ticket_count=0,
        working_count=0,
        selected_ticket=None,
        issues=issues,
    )


def build_board_view(
    service: TicketService,
    actor: UserTicketContext,
    workflow_id: str,
    *,
    query: str = "",
    assignee: str | None = None,
    selected_ticket_id: str | None = None,
    ticket_jobs=None,
) -> BoardView:
    snapshot = service.config_store.load()
    binding = service._resolve_binding(snapshot, actor.team_id, workflow_id)
    workflow = snapshot.config.teams[actor.team_id].workflows[workflow_id]
    name = workflow.name
    try:
        tickets = service.list_tickets(actor, workflow_id)
    except TicketStorageError as error:
        return _empty_board(
            binding,
            name,
            snapshot.revision,
            query=query,
            assignee=assignee,
            issues=(_issue_from_error(error),),
        )
    try:
        definition, _workflow_snapshot = service._resolve_definition(binding, snapshot=snapshot)
        definition_error: ViewIssue | None = None
    except WorkflowUnavailable as error:
        definition = None
        definition_error = _issue_from_error(error)
    except TicketStorageError as error:
        return _empty_board(
            binding,
            name,
            snapshot.revision,
            query=query,
            assignee=assignee,
            issues=(_issue_from_error(error),),
        )
    filtered = tuple(view for view in tickets if _matches_filter(view, query=query, assignee=assignee))
    reservation_rows = _reservation_rows(ticket_jobs, actor.team_id)
    if definition is None:
        columns = (
            _group_column(
                key="unavailable",
                kind="unavailable",
                state_id=None,
                name="Unavailable",
                color="#6b7280",
                tickets=filtered,
            ),
        )
    else:
        known = tuple(view for view in filtered if _has_known_state(view))
        invalid = tuple(view for view in filtered if not _has_known_state(view))
        columns = tuple(
            BoardColumnView(
                key=f"state:{state.id}",
                kind="state",
                state_id=state.id,
                name=state.name,
                color=state.color,
                count=sum(1 for view in known if view.record.state_id == state.id),
                tickets=tuple(
                    _summary_view(
                        view,
                        reservations=reservation_rows.get(
                            (view.ref.binding_id, view.ref.workflow_id, view.ref.ticket_id),
                            (),
                        ),
                        ticket_jobs=ticket_jobs,
                    )
                    for view in known
                    if view.record.state_id == state.id
                ),
            )
            for state in definition.states
        )
        if invalid:
            columns = columns + (
                BoardColumnView(
                    key="invalid-data",
                    kind="invalid-data",
                    state_id=None,
                    name="Invalid data",
                    color="#b45309",
                    count=len(invalid),
                    tickets=tuple(
                        _summary_view(
                            view,
                            reservations=reservation_rows.get(
                                (view.ref.binding_id, view.ref.workflow_id, view.ref.ticket_id),
                                (),
                            ),
                            ticket_jobs=ticket_jobs,
                        )
                        for view in invalid
                    ),
                ),
            )
    if definition is None:
        columns = (
            BoardColumnView(
                key="unavailable",
                kind="unavailable",
                state_id=None,
                name="Unavailable",
                color="#6b7280",
                count=len(filtered),
                tickets=tuple(
                    _summary_view(
                        view,
                        reservations=reservation_rows.get(
                            (view.ref.binding_id, view.ref.workflow_id, view.ref.ticket_id),
                            (),
                        ),
                        ticket_jobs=ticket_jobs,
                    )
                    for view in filtered
                ),
            ),
        )
    selected_ticket = None
    if selected_ticket_id is not None:
        selected_ticket = build_ticket_detail_view(
            service,
            actor,
            TicketRef.from_binding(binding.storage, selected_ticket_id),
            ticket_jobs=ticket_jobs,
        )
    issues = tuple(issue for view in tickets for issue in _ticket_issue_rows(view))
    if definition_error is not None:
        issues = (definition_error,) + issues
    return BoardView(
        binding=_binding_view(binding),
        name=name,
        revision=snapshot.revision,
        query=query,
        assignee=assignee,
        columns=columns,
        ticket_count=len(tickets),
        working_count=sum(1 for view in tickets if view.record.active_run is not None),
        selected_ticket=selected_ticket,
        issues=issues,
    )


def build_ticket_detail_view(
    service: TicketService,
    actor: UserTicketContext,
    ref: TicketRef,
    *,
    ticket_jobs=None,
) -> TicketDetailView:
    snapshot = service.config_store.load()
    binding = service._resolve_binding(snapshot, actor.team_id, ref.workflow_id)
    workflow = snapshot.config.teams[actor.team_id].workflows[ref.workflow_id]
    try:
        view = service.inspect(actor, ref)
    except TicketStorageError as error:
        return TicketDetailView(
            binding=_binding_view(binding),
            name=workflow.name,
            revision=snapshot.revision,
            ticket=None,
            current_definition=WorkflowDefinitionView(
                available=False,
                blueprint_id=binding.blueprint_id,
                blueprint_name=None,
                workflow_digest=None,
            ),
            issues=(_issue_from_error(error),),
        )
    issues = _ticket_issue_rows(view)
    reservations = _reservation_rows(ticket_jobs, actor.team_id).get(
        (view.ref.binding_id, view.ref.workflow_id, view.ref.ticket_id),
        (),
    )
    return TicketDetailView(
        binding=_binding_view(binding),
        name=workflow.name,
        revision=snapshot.revision,
        ticket=_summary_view(view, reservations=reservations, ticket_jobs=ticket_jobs),
        fields=_field_rows(view),
        current_definition=_definition_view(view, binding.blueprint_id),
        history=_audit_history(view),
        issues=issues,
    )