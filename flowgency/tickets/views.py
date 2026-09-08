from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from flowgency.tickets.artifacts import iter_internal_artifact_refs
from flowgency.tickets.errors import TicketStorageError, WorkflowUnavailable
from flowgency.tickets.models import TicketRef, TicketVersion, TicketView, UserTicketContext
from flowgency.tickets.service import TicketService
from flowgency.workflows.configuration import WorkflowBinding


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
    active_run_job_id: str | None
    artifact_ids: tuple[str, ...] = ()
    issues: tuple[ViewIssue, ...] = ()
    history: tuple[TicketEventView, ...] = ()


class BoardColumnView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state_id: str
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
    if not view.issues:
        return ()
    code = "unavailable-workflow" if view.version is None else "ticket-issue"
    return tuple(ViewIssue(code=code, message=message, history=(message,)) for message in view.issues)


def _state_name(view: TicketView) -> str:
    if view.definition is None:
        return view.record.state_id
    try:
        return view.definition.state(view.record.state_id).name
    except Exception:
        return view.record.state_id


def _summary_view(view: TicketView) -> TicketSummaryView:
    artifact_ids = tuple(ref.value for ref in iter_internal_artifact_refs(dict(view.record.field_values)))
    return TicketSummaryView(
        ref=view.ref,
        version=view.version,
        number=view.record.number,
        title=view.record.title,
        description=view.record.description,
        state_id=view.record.state_id,
        state_name=_state_name(view),
        assignee=view.record.assignee,
        pending_run_job_id=None if view.record.pending_run is None else view.record.pending_run.job_id,
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
            )
            for event in view.record.events
        ),
    )


def _matches_filter(view: TicketView, *, query: str, assignee: str | None) -> bool:
    if assignee is not None and view.record.assignee != assignee:
        return False
    lowered = query.strip().lower()
    if not lowered:
        return True
    return lowered in view.record.title.lower() or lowered in view.record.description.lower()


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
) -> BoardView:
    snapshot = service.config_store.load()
    binding = service._resolve_binding(snapshot, actor.team_id, workflow_id)
    workflow = snapshot.config.teams[actor.team_id].workflows[workflow_id]
    name = workflow.name
    try:
        definition, _workflow_snapshot = service._resolve_definition(binding, snapshot=snapshot)
    except WorkflowUnavailable as error:
        return _empty_board(
            binding,
            name,
            snapshot.revision,
            query=query,
            assignee=assignee,
            issues=(_issue_from_error(error),),
        )
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
    filtered = tuple(view for view in tickets if _matches_filter(view, query=query, assignee=assignee))
    columns = tuple(
        BoardColumnView(
            state_id=state.id,
            name=state.name,
            color=state.color,
            count=sum(1 for view in filtered if view.record.state_id == state.id),
            tickets=tuple(_summary_view(view) for view in filtered if view.record.state_id == state.id),
        )
        for state in definition.states
    )
    selected_ticket = None
    if selected_ticket_id is not None:
        selected_ticket = build_ticket_detail_view(
            service,
            actor,
            TicketRef.from_binding(binding.storage, selected_ticket_id),
        )
    issues = tuple(issue for view in tickets for issue in _ticket_issue_rows(view))
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
            issues=(_issue_from_error(error),),
        )
    issues = _ticket_issue_rows(view)
    return TicketDetailView(
        binding=_binding_view(binding),
        name=workflow.name,
        revision=snapshot.revision,
        ticket=_summary_view(view),
        issues=issues,
    )