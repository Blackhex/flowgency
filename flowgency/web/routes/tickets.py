from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool

from flowgency.tickets.errors import TicketConflict, TicketForbidden, TicketNotFound, TicketStorageError, WorkflowUnavailable
from flowgency.tickets.models import TicketOperation, TicketPatch, TicketRef, TicketVersion
from flowgency.tickets.views import build_board_view, build_ticket_detail_view
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.workflow_context import require_team_and_workflow, require_ticket_jobs, require_ticket_services, user_context


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _team_context(request: Request, snapshot, team_id: str) -> dict[str, Any]:
    team_cfg = snapshot.config.teams[team_id]
    return {
        "team": team_id,
        "team_name": team_cfg.name,
        "teams": {key: value.name for key, value in snapshot.config.teams.items()},
        "flowgency_title": snapshot.config.flowgency.title,
        "admin_active": False,
        "workspaces": [workspace.model_dump(mode="json") for workspace in team_cfg.workspaces],
        "workspaces_available": bool(team_cfg.workspaces),
        "nav_open_observations": 0,
        "nav_actionable": 0,
        "nav_actionable_proposals": 0,
        "nav_agent_count": len(team_cfg.agents),
        "nav_running_decisions": 0,
        "show_tips": False,
        "tips_dismissed": [],
        "theme_css": _theme_css(request),
    }


def _workflow_nav(ticket_service, actor, snapshot, team_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    workflows = snapshot.config.teams[team_id].workflows
    for workflow_id, workflow in workflows.items():
        try:
            count = len(ticket_service.list_tickets(actor, workflow_id))
        except Exception:
            count = 0
        rows.append({"id": workflow_id, "name": workflow.name, "count": count})
    return rows


def _etag(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f'W/"{digest}"'


def _json_with_etag(request: Request, payload: dict[str, Any]) -> Response:
    etag = _etag(payload)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


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
    return f"/{team}/workflows/{workflow}/tickets/{ticket}"


def _draft(decoded: Any) -> dict[str, Any] | None:
    return decoded if isinstance(decoded, dict) else None


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _ticket_form_issues(payload: dict[str, Any]) -> list[dict[str, str]]:
    issues = payload.get("issues")
    if not isinstance(issues, list):
        return []
    rows: list[dict[str, str]] = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "code": str(item.get("code", "invalid-request")),
                "field": str(item.get("field", "payload")),
                "message": str(item.get("message", "Invalid value.")),
                "hint": str(item.get("hint", "Refresh and retry the ticket action.")),
            }
        )
    return rows


async def _render_ticket_page(
    request: Request,
    services: FlowgencyServices,
    *,
    team: str,
    workflow: str,
    ticket: str,
    query: str = "",
    assignee: str | None = None,
    ticket_form_errors: list[dict[str, str]] | None = None,
    ticket_form_draft: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    board = await run_in_threadpool(
        build_board_view,
        ticket_service,
        context.actor,
        workflow,
        query=query,
        assignee=assignee,
        selected_ticket_id=ticket,
        ticket_jobs=services.ticket_jobs,
    )
    snapshot = services.config_store.load()
    template_context = _team_context(request, snapshot, team)
    template_context.update(
        {
            "active": "workflow-board",
            "workflow_nav": _workflow_nav(ticket_service, context.actor, snapshot, team),
            "active_workflow_id": workflow,
            "board": board,
            "selected_ticket_id": ticket,
            "ticket_form_errors": ticket_form_errors or [],
            "ticket_form_draft": ticket_form_draft or {},
            "workflow_initial": {
                "board": board.model_dump(mode="json"),
                "urls": {
                    "board": f"/{team}/workflows/{workflow}",
                    "snapshot": f"/{team}/workflows/{workflow}/snapshot",
                    "detail": f"/{team}/workflows/{workflow}/tickets/{ticket}",
                    "detail_snapshot": f"/{team}/workflows/{workflow}/tickets/{ticket}/snapshot",
                    "detailSnapshot": f"/{team}/workflows/{workflow}/tickets/{ticket}/snapshot",
                    "assignee": f"/{team}/workflows/{workflow}/tickets/{ticket}/assignee",
                    "update": f"/{team}/workflows/{workflow}/tickets/{ticket}/update",
                    "run": f"/{team}/workflows/{workflow}/tickets/{ticket}/run",
                    "create": f"/{team}/workflows/{workflow}/tickets",
                },
            },
        }
    )
    return _templates(request).TemplateResponse(
        request,
        "ticket_detail.html",
        template_context,
        status_code=status_code,
    )


async def _render_board_page(
    request: Request,
    services: FlowgencyServices,
    *,
    team: str,
    workflow: str,
    query: str = "",
    assignee: str | None = None,
    selected_ticket_id: str | None = None,
    new_ticket_errors: list[dict[str, str]] | None = None,
    new_ticket_draft: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    board = await run_in_threadpool(
        build_board_view,
        ticket_service,
        context.actor,
        workflow,
        query=query,
        assignee=assignee,
        selected_ticket_id=selected_ticket_id,
        ticket_jobs=services.ticket_jobs,
    )
    snapshot = services.config_store.load()
    template_context = _team_context(request, snapshot, team)
    template_context.update(
        {
            "active": "workflow-board",
            "workflow_nav": _workflow_nav(ticket_service, context.actor, snapshot, team),
            "active_workflow_id": workflow,
            "board": board,
            "selected_ticket_id": selected_ticket_id,
            "new_ticket_errors": new_ticket_errors or [],
            "new_ticket_draft": new_ticket_draft or {},
            "workflow_initial": {
                "board": board.model_dump(mode="json"),
                "urls": {
                    "board": f"/{team}/workflows/{workflow}",
                    "snapshot": f"/{team}/workflows/{workflow}/snapshot",
                    "detail": f"/{team}/workflows/{workflow}/tickets/__ticket__",
                    "detailSnapshot": f"/{team}/workflows/{workflow}/tickets/__ticket__/snapshot",
                    "assignee": f"/{team}/workflows/{workflow}/tickets/__ticket__/assignee",
                    "update": f"/{team}/workflows/{workflow}/tickets/__ticket__/update",
                    "run": f"/{team}/workflows/{workflow}/tickets/__ticket__/run",
                    "create": f"/{team}/workflows/{workflow}/tickets",
                },
            },
        }
    )
    return _templates(request).TemplateResponse(
        request,
        "workflow_board.html",
        template_context,
        status_code=status_code,
    )


def _artifact_namespace(binding) -> TicketRef:
    return TicketRef.from_binding(binding.storage, "artifact-namespace")


def _resolve_current_binding_id(ticket_service, team: str, workflow: str) -> str:
    return ticket_service._resolve_current_binding(team, workflow).storage.binding_id


def _build_ticket_detail_snapshot(ticket_service, actor, team: str, workflow: str, ticket: str, ticket_jobs):
    binding = ticket_service._resolve_current_binding(team, workflow)
    return build_ticket_detail_view(
        ticket_service,
        actor,
        TicketRef.from_binding(binding.storage, ticket),
        ticket_jobs=ticket_jobs,
    )


def _read_artifact_for_route(ticket_service, actor, team: str, workflow: str, artifact: str):
    binding = ticket_service._resolve_current_binding(team, workflow)
    provider = ticket_service.storage_factory(binding.storage)
    return provider.read_artifact(_artifact_namespace(binding), artifact)


@router.get("/{team}/workflows/{workflow}/tickets/{ticket}")
async def ticket_detail_page(
    request: Request,
    team: str,
    workflow: str,
    ticket: str,
    query: str = "",
    assignee: str | None = None,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    board = await run_in_threadpool(
        build_board_view,
        ticket_service,
        context.actor,
        workflow,
        query=query,
        assignee=assignee,
        selected_ticket_id=ticket,
        ticket_jobs=services.ticket_jobs,
    )
    snapshot = services.config_store.load()
    template_context = _team_context(request, snapshot, team)
    template_context.update(
        {
            "active": "workflow-board",
            "workflow_nav": _workflow_nav(ticket_service, context.actor, snapshot, team),
            "active_workflow_id": workflow,
            "board": board,
            "selected_ticket_id": ticket,
            "workflow_initial": {
                "board": board.model_dump(mode="json"),
                "urls": {
                    "board": f"/{team}/workflows/{workflow}",
                    "snapshot": f"/{team}/workflows/{workflow}/snapshot",
                    "detail": f"/{team}/workflows/{workflow}/tickets/{ticket}",
                    "detail_snapshot": f"/{team}/workflows/{workflow}/tickets/{ticket}/snapshot",
                    "detailSnapshot": f"/{team}/workflows/{workflow}/tickets/{ticket}/snapshot",
                    "assignee": f"/{team}/workflows/{workflow}/tickets/{ticket}/assignee",
                    "run": f"/{team}/workflows/{workflow}/tickets/{ticket}/run",
                    "update": f"/{team}/workflows/{workflow}/tickets/{ticket}/update",
                    "create": f"/{team}/workflows/{workflow}/tickets",
                },
            },
        }
    )
    return _templates(request).TemplateResponse(request, "ticket_detail.html", template_context)


@router.get("/{team}/workflows/{workflow}/tickets/{ticket}/snapshot")
async def ticket_detail_snapshot(
    request: Request,
    team: str,
    workflow: str,
    ticket: str,
    services: FlowgencyServices = Depends(get_services),
) -> Response:
    context = await run_in_threadpool(require_team_and_workflow, services, team, workflow)
    ticket_service = require_ticket_services(services)
    detail = await run_in_threadpool(
        _build_ticket_detail_snapshot,
        ticket_service,
        context.actor,
        team,
        workflow,
        ticket,
        services.ticket_jobs,
    )
    if detail.ticket is None and detail.issues and detail.issues[0].code == "ticket-not-found":
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _json_with_etag(request, detail.model_dump(mode="json"))


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
        payload_dict = {"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(json.loads(payload_text) if payload_text else None)}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_board_page(
            request,
            services,
            team=team,
            workflow=workflow,
            new_ticket_errors=_ticket_form_issues(payload_dict),
            new_ticket_draft=payload_dict.get("draft"),
            status_code=422,
        )
    except json.JSONDecodeError:
        payload_dict = {"code": "invalid-request", "issues": [{"code": "invalid-request", "field": "payload", "message": "Payload must be valid JSON.", "hint": "Correct the submitted ticket payload and try again."}], "draft": None}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_board_page(
            request,
            services,
            team=team,
            workflow=workflow,
            new_ticket_errors=_ticket_form_issues(payload_dict),
            new_ticket_draft=None,
            status_code=422,
        )
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
        payload_dict = {
            "code": error.code,
            "issues": [{"code": error.code, "field": "payload", "message": error.message, "hint": "Refresh and retry the ticket action."}],
            "draft": payload.model_dump(mode="json"),
        }
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=error.http_status)
        return await _render_board_page(
            request,
            services,
            team=team,
            workflow=workflow,
            new_ticket_errors=_ticket_form_issues(payload_dict),
            new_ticket_draft=payload_dict["draft"],
            status_code=error.http_status,
        )
    if _wants_json(request):
        return RedirectResponse(f"{ticket_return_url(team, workflow, result.ticket.id)}/snapshot", status_code=303)
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
        payload_dict = {"code": "invalid-request", "issues": [{"code": "invalid-request", "field": "payload", "message": "Payload must be valid JSON.", "hint": "Correct the submitted ticket payload and try again."}], "draft": None}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft={}, status_code=422)
    except ValidationError as exc:
        payload_dict = {"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(decoded)}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft=payload_dict.get("draft") or {}, status_code=422)
    actor = user_context(team)
    operation = operation_for_user(actor, {"team": team, "workflow": workflow, "ticket": ticket}, payload)
    try:
        await run_in_threadpool(ticket_service.update, actor, payload.version, payload.patch, operation)
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        payload_dict = {
            "code": error.code,
            "issues": [{"code": error.code, "field": "payload", "message": error.message, "hint": "Refresh and retry the ticket action."}],
            "draft": payload.model_dump(mode="json"),
        }
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=error.http_status)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft=payload_dict["draft"], status_code=error.http_status)
    if _wants_json(request):
        return RedirectResponse(f"{ticket_return_url(team, workflow, ticket)}/snapshot", status_code=303)
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
        payload_dict = {"code": "invalid-request", "issues": [{"code": "invalid-request", "field": "payload", "message": "Payload must be valid JSON.", "hint": "Correct the submitted ticket payload and try again."}], "draft": None}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft={}, status_code=422)
    except ValidationError as exc:
        payload_dict = {"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(decoded)}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft=payload_dict.get("draft") or {}, status_code=422)
    actor = user_context(team)
    operation = operation_for_user(actor, {"team": team, "workflow": workflow, "ticket": ticket}, payload)
    try:
        await run_in_threadpool(ticket_service.assign, actor, payload.version, payload.assignee, operation)
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        payload_dict = {
            "code": error.code,
            "issues": [{"code": error.code, "field": "payload", "message": error.message, "hint": "Refresh and retry the ticket action."}],
            "draft": payload.model_dump(mode="json"),
        }
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=error.http_status)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft=payload_dict["draft"], status_code=error.http_status)
    if _wants_json(request):
        return RedirectResponse(f"{ticket_return_url(team, workflow, ticket)}/snapshot", status_code=303)
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
        payload_dict = {"code": "invalid-request", "issues": [{"code": "invalid-request", "field": "payload", "message": "Payload must be valid JSON.", "hint": "Correct the submitted ticket payload and try again."}], "draft": None}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft={}, status_code=422)
    except ValidationError as exc:
        payload_dict = {"code": "invalid-request", "issues": _pydantic_issue_dicts(exc), "draft": _draft(decoded)}
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=422)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft=payload_dict.get("draft") or {}, status_code=422)
    actor = user_context(team)
    try:
        await run_in_threadpool(ticket_jobs.submit, actor, payload.version, payload.operation_id)
    except (TicketConflict, TicketForbidden, WorkflowUnavailable) as error:
        payload_dict = {
            "code": error.code,
            "issues": [{"code": error.code, "field": "payload", "message": error.message, "hint": "Refresh and retry the ticket action."}],
            "draft": payload.model_dump(mode="json"),
        }
        if _wants_json(request):
            return JSONResponse(payload_dict, status_code=error.http_status)
        return await _render_ticket_page(request, services, team=team, workflow=workflow, ticket=ticket, ticket_form_errors=_ticket_form_issues(payload_dict), ticket_form_draft=payload_dict["draft"], status_code=error.http_status)
    if _wants_json(request):
        return RedirectResponse(f"{ticket_return_url(team, workflow, ticket)}/snapshot", status_code=303)
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