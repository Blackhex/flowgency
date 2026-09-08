from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool

from flowgency.tickets.errors import TicketConflict, TicketForbidden, TicketNotFound, TicketStorageError, WorkflowUnavailable
from flowgency.tickets.models import TicketOperation, TicketPatch, TicketRef, TicketVersion
from flowgency.tickets.views import build_ticket_detail_view
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.workflow_context import require_team_and_workflow, require_ticket_jobs, require_ticket_services, user_context


router = APIRouter()


class CreateTicketForm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    title: str
    description: str
    field_values: dict[str, Any] | None = None


class UpdateTicketForm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: str
    patch: TicketPatch


class AssigneeForm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    assignee: str | None = None
    operation_id: str


class RunTicketForm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: str


def _safe_pydantic_message(error_type: str) -> str:
    return {
        "missing": "Required field is missing.",
        "extra_forbidden": "Unexpected field.",
        "json_invalid": "Payload must be valid JSON.",
        "string_type": "Value must be a string.",
        "dict_type": "Value must be an object.",
        "model_type": "Value must be an object.",
        "none_required": "Value must be empty.",
    }.get(error_type, "Invalid value.")


def _pydantic_issue_dicts(exc: ValidationError) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "payload"
        rows.append(
            {
                "code": "invalid-request",
                "field": location,
                "message": _safe_pydantic_message(str(error.get("type", ""))),
                "hint": "Correct the submitted ticket payload and try again.",
            }
        )
    return rows


def _ticket_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    field: str,
    draft: dict[str, Any] | None,
) -> JSONResponse:
    return JSONResponse(
        {
            "code": code,
            "issues": [
                {
                    "code": code,
                    "field": field,
                    "message": message,
                    "hint": "Refresh and retry the ticket action.",
                }
            ],
            "draft": draft,
        },
        status_code=status_code,
    )


async def _payload_text(request: Request) -> str:
    form = await request.form()
    return str(form.get("payload", ""))


def require_route_ref(
    version: TicketVersion,
    team: str,
    workflow: str,
    ticket: str,
    expected_binding_id: str,
) -> None:
    ref = version.ref
    if (
        ref.team_id != team
        or ref.workflow_id != workflow
        or ref.ticket_id != ticket
        or ref.binding_id != expected_binding_id
    ):
        raise ValidationError.from_exception_data(
            "RouteRefMismatch",
            [
                {
                    "type": "value_error",
                    "loc": ("version", "ref"),
                    "msg": "Ticket ref does not match the current route binding.",
                    "input": version.model_dump(mode="json"),
                    "ctx": {"error": "Ticket ref does not match the current route binding."},
                }
            ],
        )


def operation_for_user(actor, route_target: dict[str, str], payload_model: BaseModel) -> TicketOperation:
    payload = {
        "actor": actor.model_dump(mode="json"),
        "target": route_target,
        "payload": payload_model.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TicketOperation(
        operation_id=str(getattr(payload_model, "operation_id")),
        request_digest=digest,
    )


def ticket_return_url(team: str, workflow: str, ticket: str) -> str:
    return f"/{team}/workflows/{workflow}/tickets/{ticket}/snapshot"


def _draft(decoded: Any) -> dict[str, Any] | None:
    return decoded if isinstance(decoded, dict) else None


def _find_artifact_namespace(ticket_service, actor, workflow: str, artifact_id: str) -> TicketRef:
    tickets = ticket_service.list_tickets(actor, workflow)
    for view in tickets:
        for artifact_ref in view.record.field_values.values():
            if getattr(artifact_ref, "kind", None) == "id" and getattr(artifact_ref, "value", None) == artifact_id:
                return view.ref
    raise TicketNotFound("artifact-not-found", "No such artifact")


def _resolve_current_binding_id(ticket_service, team: str, workflow: str) -> str:
    return ticket_service._resolve_current_binding(team, workflow).storage.binding_id


def _build_ticket_detail_snapshot(ticket_service, actor, team: str, workflow: str, ticket: str):
    binding = ticket_service._resolve_current_binding(team, workflow)
    return build_ticket_detail_view(
        ticket_service,
        actor,
        TicketRef.from_binding(binding.storage, ticket),
    )


def _read_artifact_for_route(ticket_service, actor, team: str, workflow: str, artifact: str):
    namespace = _find_artifact_namespace(ticket_service, actor, workflow, artifact)
    binding = ticket_service._resolve_current_binding(team, workflow)
    provider = ticket_service.storage_factory(binding.storage)
    return provider.read_artifact(namespace, artifact)


@router.get("/{team}/workflows/{workflow}/tickets/{ticket}")
async def ticket_detail_redirect(
    team: str,
    workflow: str,
    ticket: str,
    services: FlowgencyServices = Depends(get_services),
):
    await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    return RedirectResponse(ticket_return_url(team, workflow, ticket), status_code=303)


@router.get("/{team}/workflows/{workflow}/tickets/{ticket}/snapshot")
async def ticket_detail_snapshot(
    team: str,
    workflow: str,
    ticket: str,
    services: FlowgencyServices = Depends(get_services),
) -> JSONResponse:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    detail = await run_in_threadpool(
        _build_ticket_detail_snapshot,
        ticket_service,
        context.actor,
        team,
        workflow,
        ticket,
    )
    if detail.ticket is None and detail.issues and detail.issues[0].code == "ticket-not-found":
        raise HTTPException(status_code=404, detail="Ticket not found")
    return JSONResponse(detail.model_dump(mode="json"))


@router.post("/{team}/workflows/{workflow}/tickets")
async def create_ticket(
    request: Request,
    team: str,
    workflow: str,
    services: FlowgencyServices = Depends(get_services),
):
    await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    payload_text = await _payload_text(request)
    try:
        payload = CreateTicketForm.model_validate_json(payload_text)
    except ValidationError as exc:
        return JSONResponse({"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(json.loads(payload_text) if payload_text else None)}, status_code=422)
    except json.JSONDecodeError:
        return _ticket_error_response(status_code=422, code="invalid-request", message="Payload must be valid JSON.", field="payload", draft=None)
    actor = user_context(team)
    operation = operation_for_user(actor, {"team": team, "workflow": workflow}, payload)
    try:
        result = await run_in_threadpool(
            ticket_service.create,
            actor,
            workflow,
            payload.title,
            payload.description,
            payload.field_values,
            operation,
        )
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        return _ticket_error_response(status_code=error.http_status, code=error.code, message=error.message, field="payload", draft=payload.model_dump(mode="json"))
    return RedirectResponse(ticket_return_url(team, workflow, result.ticket.id), status_code=303)


@router.post("/{team}/workflows/{workflow}/tickets/{ticket}/update")
async def update_ticket(
    request: Request,
    team: str,
    workflow: str,
    ticket: str,
    services: FlowgencyServices = Depends(get_services),
):
    await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    payload_text = await _payload_text(request)
    decoded = None
    try:
        decoded = json.loads(payload_text)
        payload = UpdateTicketForm.model_validate(decoded)
        binding_id = await run_in_threadpool(_resolve_current_binding_id, ticket_service, team, workflow)
        require_route_ref(payload.version, team, workflow, ticket, binding_id)
    except json.JSONDecodeError:
        return _ticket_error_response(status_code=422, code="invalid-request", message="Payload must be valid JSON.", field="payload", draft=None)
    except ValidationError as exc:
        return JSONResponse({"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(decoded)}, status_code=422)
    actor = user_context(team)
    operation = operation_for_user(actor, {"team": team, "workflow": workflow, "ticket": ticket}, payload)
    try:
        await run_in_threadpool(ticket_service.update, actor, payload.version, payload.patch, operation)
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        return _ticket_error_response(status_code=error.http_status, code=error.code, message=error.message, field="payload", draft=payload.model_dump(mode="json"))
    return RedirectResponse(ticket_return_url(team, workflow, ticket), status_code=303)


@router.post("/{team}/workflows/{workflow}/tickets/{ticket}/assignee")
async def save_assignee(
    request: Request,
    team: str,
    workflow: str,
    ticket: str,
    services: FlowgencyServices = Depends(get_services),
):
    await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    payload_text = await _payload_text(request)
    decoded = None
    try:
        decoded = json.loads(payload_text)
        payload = AssigneeForm.model_validate(decoded)
        binding_id = await run_in_threadpool(_resolve_current_binding_id, ticket_service, team, workflow)
        require_route_ref(payload.version, team, workflow, ticket, binding_id)
    except json.JSONDecodeError:
        return _ticket_error_response(status_code=422, code="invalid-request", message="Payload must be valid JSON.", field="payload", draft=None)
    except ValidationError as exc:
        return JSONResponse({"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(decoded)}, status_code=422)
    actor = user_context(team)
    operation = operation_for_user(actor, {"team": team, "workflow": workflow, "ticket": ticket}, payload)
    try:
        await run_in_threadpool(ticket_service.assign, actor, payload.version, payload.assignee, operation)
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        return _ticket_error_response(status_code=error.http_status, code=error.code, message=error.message, field="payload", draft=payload.model_dump(mode="json"))
    return RedirectResponse(ticket_return_url(team, workflow, ticket), status_code=303)


@router.post("/{team}/workflows/{workflow}/tickets/{ticket}/run")
async def run_ticket(
    request: Request,
    team: str,
    workflow: str,
    ticket: str,
    services: FlowgencyServices = Depends(get_services),
):
    await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_jobs = require_ticket_jobs(services)
    payload_text = await _payload_text(request)
    decoded = None
    try:
        decoded = json.loads(payload_text)
        payload = RunTicketForm.model_validate(decoded)
        binding_id = await run_in_threadpool(_resolve_current_binding_id, ticket_jobs.service, team, workflow)
        require_route_ref(payload.version, team, workflow, ticket, binding_id)
    except json.JSONDecodeError:
        return _ticket_error_response(status_code=422, code="invalid-request", message="Payload must be valid JSON.", field="payload", draft=None)
    except ValidationError as exc:
        return JSONResponse({"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(decoded)}, status_code=422)
    actor = user_context(team)
    try:
        await run_in_threadpool(ticket_jobs.submit, actor, payload.version, payload.operation_id)
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        return _ticket_error_response(status_code=error.http_status, code=error.code, message=error.message, field="payload", draft=payload.model_dump(mode="json"))
    return RedirectResponse(ticket_return_url(team, workflow, ticket), status_code=303)


@router.get("/{team}/workflows/{workflow}/artifacts/{artifact}")
async def download_artifact(
    team: str,
    workflow: str,
    artifact: str,
    services: FlowgencyServices = Depends(get_services),
) -> Response:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    try:
        retained = await run_in_threadpool(
            _read_artifact_for_route,
            ticket_service,
            context.actor,
            team,
            workflow,
            artifact,
        )
    except TicketNotFound as error:
        raise HTTPException(status_code=404, detail=error.message) from error
    except TicketStorageError as error:
        raise HTTPException(status_code=error.http_status, detail=error.message) from error
    headers = {
        "Content-Disposition": f'attachment; filename="{retained.filename}"',
        "Content-Length": str(retained.size),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=retained.content, media_type=retained.media_type, headers=headers)