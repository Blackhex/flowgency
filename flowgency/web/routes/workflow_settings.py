from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from flowgency.configuration import ValidationFailed
from flowgency.configuration.store import ConfigConflictError
from flowgency.tickets.models import StorageBinding
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.routes.workflows import _team_context, _workflow_nav
from flowgency.web.workflow_context import user_context
from flowgency.workflows.configuration import require_compatible
from flowgency.workflows.forms import WorkflowSettingsForm


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _require_workflow_configuration(services: FlowgencyServices):
    if services.workflow_configuration is None:
        raise HTTPException(status_code=503, detail="Workflow configuration unavailable")
    return services.workflow_configuration


def _require_team(snapshot, team_id: str):
    if team_id not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail="Unknown team")
    return snapshot.config.teams[team_id]


def _generated_workflow_id() -> str:
    return f"wf-{uuid4().hex}"


def _available_blueprints(configuration, snapshot) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for inspection in configuration.library_for(snapshot).list():
        if inspection.snapshot is None:
            continue
        rows.append(
            {
                "id": inspection.blueprint_id,
                "name": inspection.snapshot.definition.name,
            }
        )
    return tuple(rows)


def _issue_dicts(exc: ValidationFailed) -> list[dict[str, str]]:
    return [
        {
            "code": issue.code,
            "field": issue.field,
            "message": issue.message,
            "hint": issue.corrective_hint,
        }
        for issue in exc.issues
    ]


def _form_context(
    request: Request,
    services: FlowgencyServices,
    snapshot,
    *,
    team_id: str,
    workflow_id: str,
    form: WorkflowSettingsForm,
    create_mode: bool,
    issues: list[dict[str, str]] | None = None,
    warning: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    ticket_service = services.tickets
    actor = None
    workflow_nav = []
    if ticket_service is not None:
        actor = user_context(team_id)
        workflow_nav = _workflow_nav(ticket_service, actor, snapshot, team_id)
    context = _team_context(request, snapshot, team_id)
    context.update(
        {
            "request": request,
            "active": "workflow-board",
            "workflow_nav": workflow_nav,
            "active_workflow_id": None if create_mode else workflow_id,
            "flowgency_title": snapshot.config.flowgency.title,
            "theme_css": _theme_css(request),
            "settings_mode": "create" if create_mode else "edit",
            "workflow_settings_id": workflow_id,
            "workflow_settings_form": form,
            "workflow_settings_issues": issues or [],
            "workflow_settings_warning": warning,
            "workflow_settings_title": "New workflow" if create_mode else f"{form.name} settings",
            "workflow_settings_save_label": "Create workflow" if create_mode else "Save",
            "workflow_settings_save_action": f"/{team_id}/workflows/new" if create_mode else f"/{team_id}/workflows/{workflow_id}/settings",
            "workflow_settings_check_action": f"/{team_id}/workflows/{workflow_id}/settings/check-storage",
            "workflow_settings_back_href": None if create_mode else f"/{team_id}/workflows/{workflow_id}",
            "workflow_settings_blueprints": _available_blueprints(
                _require_workflow_configuration(services), snapshot
            ),
            "workflow_settings_blueprint_href": f"/admin/workflow-library/blueprints/{form.blueprint}",
        }
    )
    return _templates(request).TemplateResponse(
        request,
        "workflow_settings.html",
        context,
        status_code=status_code,
    )


def _existing_form(snapshot, team_id: str, workflow_id: str) -> WorkflowSettingsForm:
    workflow = snapshot.config.teams[team_id].workflows[workflow_id]
    return WorkflowSettingsForm.model_validate(
        {
            "workflow_id": workflow_id,
            "name": workflow.name,
            "blueprint": workflow.blueprint,
            "integration": workflow.integration,
            "integration_config": dict(workflow.integration_config),
            "expected_revision": snapshot.revision,
        }
    )


def _draft_form(
    *,
    workflow_id: str,
    expected_revision: str,
    name: str = "",
    blueprint: str = "delivery",
    integration: str = "local",
    root: str = "",
) -> WorkflowSettingsForm:
    return WorkflowSettingsForm.model_construct(
        workflow_id=workflow_id,
        name=name,
        blueprint=blueprint,
        integration=integration,
        integration_config={"root": root},
        expected_revision=expected_revision,
    )


def _check_candidate(configuration, snapshot, team_id: str, workflow_id: str, form: WorkflowSettingsForm):
    library = configuration.library_for(snapshot)
    source = library.inspect(form.blueprint)
    candidate = source.definition.model_copy()
    storage = StorageBinding(
        integration=form.integration,
        config={"root": form.storage_root},
        team_id=team_id,
        workflow_id=workflow_id,
    )
    provider = configuration.storage_factory(storage)
    health = provider.check()
    issues: list[dict[str, str]] = []
    ticket_count = 0
    if health.status == "ok":
        try:
            records = provider.list(team_id, workflow_id)
            ticket_count = len(records)
            require_compatible(candidate, records)
        except ValidationFailed as exc:
            issues.extend(_issue_dicts(exc))
    return health, ticket_count, issues


@router.get("/{team}/workflows/new", response_class=HTMLResponse)
async def workflow_create_page(
    request: Request,
    team: str,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    snapshot = services.config_store.load()
    _require_team(snapshot, team)
    form = _draft_form(
        workflow_id=_generated_workflow_id(),
        expected_revision=snapshot.revision,
    )
    return _form_context(
        request,
        services,
        snapshot,
        team_id=team,
        workflow_id=form.workflow_id or _generated_workflow_id(),
        form=form,
        create_mode=True,
    )


@router.post("/{team}/workflows/new", response_class=HTMLResponse)
async def workflow_create_save(
    request: Request,
    team: str,
    services: FlowgencyServices = Depends(get_services),
) -> Response:
    snapshot = services.config_store.load()
    _require_team(snapshot, team)
    try:
        form = WorkflowSettingsForm.from_form_data(await request.form())
    except (ValidationError, ValueError) as exc:
        submitted = await request.form()
        fallback = _draft_form(
            workflow_id=str(submitted.get("workflow_id", "")).strip() or _generated_workflow_id(),
            expected_revision=snapshot.revision,
            name=str(submitted.get("name", "")).strip(),
            blueprint=str(submitted.get("blueprint", "delivery")).strip() or "delivery",
            integration="local",
            root=str(submitted.get("integration_config.root", "")).strip(),
        )
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=fallback.workflow_id or _generated_workflow_id(),
            form=fallback,
            create_mode=True,
            warning=str(exc),
            status_code=422,
        )
    workflow_id = form.workflow_id or _generated_workflow_id()
    configuration = _require_workflow_configuration(services)
    try:
        await run_in_threadpool(
            configuration.save_instance,
            form.expected_revision,
            team,
            workflow_id,
            form.to_patch(),
            create=True,
        )
    except ValidationFailed as exc:
        snapshot = services.config_store.load()
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow_id,
            form=form,
            create_mode=True,
            issues=_issue_dicts(exc),
            warning="Cannot save workflow.",
            status_code=422,
        )
    except ConfigConflictError as exc:
        snapshot = services.config_store.load()
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow_id,
            form=form,
            create_mode=True,
            warning=str(exc),
            status_code=409,
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/workflows/{workflow_id}/settings", status_code=303)


@router.get("/{team}/workflows/{workflow}/settings", response_class=HTMLResponse)
async def workflow_settings_page(
    request: Request,
    team: str,
    workflow: str,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    snapshot = services.config_store.load()
    _require_team(snapshot, team)
    if workflow not in snapshot.config.teams[team].workflows:
        raise HTTPException(status_code=404, detail="Unknown workflow")
    return _form_context(
        request,
        services,
        snapshot,
        team_id=team,
        workflow_id=workflow,
        form=_existing_form(snapshot, team, workflow),
        create_mode=False,
    )


@router.post("/{team}/workflows/{workflow}/settings", response_class=HTMLResponse)
async def workflow_settings_save(
    request: Request,
    team: str,
    workflow: str,
    services: FlowgencyServices = Depends(get_services),
) -> Response:
    snapshot = services.config_store.load()
    _require_team(snapshot, team)
    if workflow not in snapshot.config.teams[team].workflows:
        raise HTTPException(status_code=404, detail="Unknown workflow")
    try:
        form = WorkflowSettingsForm.from_form_data(await request.form())
    except (ValidationError, ValueError) as exc:
        current = _existing_form(snapshot, team, workflow)
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow,
            form=current,
            create_mode=False,
            warning=str(exc),
            status_code=422,
        )
    configuration = _require_workflow_configuration(services)
    try:
        await run_in_threadpool(
            configuration.save_instance,
            form.expected_revision,
            team,
            workflow,
            form.to_patch(),
        )
    except ValidationFailed as exc:
        snapshot = services.config_store.load()
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow,
            form=form,
            create_mode=False,
            issues=_issue_dicts(exc),
            warning="Cannot save workflow.",
            status_code=422,
        )
    except ConfigConflictError as exc:
        snapshot = services.config_store.load()
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow,
            form=form,
            create_mode=False,
            warning=str(exc),
            status_code=409,
        )
    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/workflows/{workflow}/settings", status_code=303)


@router.post("/{team}/workflows/{workflow}/settings/check-storage")
async def workflow_settings_check_storage(
    request: Request,
    team: str,
    workflow: str,
    services: FlowgencyServices = Depends(get_services),
) -> JSONResponse:
    snapshot = services.config_store.load()
    _require_team(snapshot, team)
    try:
        form = WorkflowSettingsForm.from_form_data(await request.form())
    except (ValidationError, ValueError) as exc:
        return JSONResponse(
            {
                "status": "invalid",
                "label": "Invalid settings",
                "detail": str(exc),
                "ticket_count": 0,
                "issues": [],
            },
            status_code=422,
        )
    health, ticket_count, issues = await run_in_threadpool(
        _check_candidate,
        _require_workflow_configuration(services),
        snapshot,
        team,
        workflow,
        form,
    )
    status = "ok" if health.status == "ok" and not issues else "unavailable" if health.status != "ok" else "incompatible"
    label = {
        "ok": "Available",
        "unavailable": "Storage unavailable",
        "incompatible": "Incompatible",
    }[status]
    return JSONResponse(
        {
            "status": status,
            "label": label,
            "detail": health.detail,
            "ticket_count": ticket_count,
            "issues": issues,
        }
    )