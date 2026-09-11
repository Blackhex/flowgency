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
from flowgency.web.team_navigation import build_team_context
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


def _new_workflow_id(snapshot, team_id: str) -> str:
    workflows = snapshot.config.teams[team_id].workflows
    workflow_id = _generated_workflow_id()
    while workflow_id in workflows:
        workflow_id = _generated_workflow_id()
    return workflow_id


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


def _form_error_message(exc: ValidationError | ValueError) -> str:
    """Extract a concise user-facing message from a form parse exception."""
    if isinstance(exc, ValidationError):
        parts = []
        for e in exc.errors(include_input=False, include_url=False):
            msg = e.get("msg", "")
            # Strip Pydantic's "Value error, " type prefix
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            if msg:
                parts.append(msg)
        return " ".join(parts) if parts else "Invalid settings."
    return str(exc)


def _settings_form_payload(form: WorkflowSettingsForm) -> dict[str, str]:
    return {
        "name": form.name,
        "blueprint": form.blueprint,
        "integration": form.integration,
        "storageRoot": form.storage_root,
        "expectedRevision": form.expected_revision,
    }


def _idle_health_payload() -> dict[str, Any]:
    return {
        "status": "idle",
        "label": "Not checked",
        "detail": "",
        "issues": [],
    }


def _invalid_health_payload(message: str) -> dict[str, Any]:
    return {
        "status": "invalid",
        "label": "Invalid settings",
        "detail": message,
        "issues": [],
    }


def _issues_health_payload(issues: list[dict[str, str]], warning: str | None = None) -> dict[str, Any]:
    lowered = " ".join(issue.get("message", "").lower() for issue in issues)
    if "unavailable" in lowered:
        status = "unavailable"
        label = "Storage unavailable"
    elif issues:
        status = "incompatible"
        label = "Incompatible"
    else:
        status = "invalid"
        label = "Invalid settings"
    detail = "" if warning == "Cannot save workflow." else (warning or "")
    return {
        "status": status,
        "label": label,
        "detail": detail,
        "issues": issues,
    }


def _form_context(
    request: Request,
    services: FlowgencyServices,
    snapshot,
    *,
    team_id: str,
    workflow_id: str,
    form: WorkflowSettingsForm,
    saved_form: WorkflowSettingsForm | None = None,
    create_mode: bool,
    issues: list[dict[str, str]] | None = None,
    warning: str | None = None,
    health: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = build_team_context(
        snapshot,
        team_id,
        theme_css=_theme_css(request),
        show_tips=False,
        tips_dismissed=[],
        ticket_service=services.tickets,
    )
    context.update(
        {
            "request": request,
            "active": "workflow-board",
            "active_workflow_id": None if create_mode else workflow_id,
            "flowgency_title": snapshot.config.flowgency.title,
            "theme_css": _theme_css(request),
            "settings_mode": "create" if create_mode else "edit",
            "workflow_settings_id": workflow_id,
            "workflow_settings_form": form,
            "workflow_settings_saved_form": saved_form or form,
            "workflow_settings_issues": issues or [],
            "workflow_settings_warning": warning,
            "workflow_settings_title": "New workflow" if create_mode else f"{(saved_form or form).name} settings",
            "workflow_settings_save_label": "Create workflow" if create_mode else "Save",
            "workflow_settings_save_action": f"/{team_id}/workflows/new" if create_mode else f"/{team_id}/workflows/{workflow_id}/settings",
            "workflow_settings_check_action": f"/{team_id}/workflows/{workflow_id}/settings/check-storage",
            "workflow_settings_back_href": None if create_mode else f"/{team_id}/workflows/{workflow_id}",
            "workflow_settings_blueprints": _available_blueprints(
                _require_workflow_configuration(services), snapshot
            ),
            "workflow_settings_blueprint_href": f"/admin/workflow-library/blueprints/{form.blueprint}",
            "workflow_settings_initial_health": health or _idle_health_payload(),
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


def _submitted_draft_form(
    submitted: Any,
    *,
    workflow_id: str,
    expected_revision: str,
) -> WorkflowSettingsForm:
    return _draft_form(
        workflow_id=workflow_id,
        expected_revision=expected_revision,
        name=str(submitted.get("name", "")).strip(),
        blueprint=str(submitted.get("blueprint", "delivery")).strip() or "delivery",
        integration=str(submitted.get("integration", "local")).strip() or "local",
        root=str(submitted.get("integration_config.root", "")).strip(),
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
        workflow_id=_new_workflow_id(snapshot, team),
        expected_revision=snapshot.revision,
    )
    return _form_context(
        request,
        services,
        snapshot,
        team_id=team,
        workflow_id=form.workflow_id or _new_workflow_id(snapshot, team),
        form=form,
        saved_form=form,
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
    workflow_id = _new_workflow_id(snapshot, team)
    submitted = await request.form()
    try:
        form = WorkflowSettingsForm.from_form_data(submitted)
    except (ValidationError, ValueError) as exc:
        fallback = _submitted_draft_form(
            submitted,
            workflow_id=workflow_id,
            expected_revision=snapshot.revision,
        )
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow_id,
            form=fallback,
            saved_form=_draft_form(
                workflow_id=workflow_id,
                expected_revision=snapshot.revision,
            ),
            create_mode=True,
            warning=_form_error_message(exc),
            health=_invalid_health_payload(_form_error_message(exc)),
            status_code=422,
        )
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
            saved_form=_draft_form(
                workflow_id=workflow_id,
                expected_revision=snapshot.revision,
            ),
            create_mode=True,
            issues=_issue_dicts(exc),
            warning="Cannot save workflow.",
            health=_issues_health_payload(_issue_dicts(exc), "Cannot save workflow."),
            status_code=422,
        )
    except ConfigConflictError as exc:
        snapshot = services.config_store.load()
        saved_form = _draft_form(
            workflow_id=workflow_id,
            expected_revision=snapshot.revision,
        )
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow_id,
            form=form,
            saved_form=saved_form,
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
        saved_form=_existing_form(snapshot, team, workflow),
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
    submitted = await request.form()
    try:
        form = WorkflowSettingsForm.from_form_data(submitted)
    except (ValidationError, ValueError) as exc:
        current = _submitted_draft_form(
            submitted,
            workflow_id=workflow,
            expected_revision=snapshot.revision,
        )
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow,
            form=current,
            saved_form=_existing_form(snapshot, team, workflow),
            create_mode=False,
            warning=_form_error_message(exc),
            health=_invalid_health_payload(_form_error_message(exc)),
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
        issues = _issue_dicts(exc)
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow,
            form=form,
            saved_form=_existing_form(snapshot, team, workflow),
            create_mode=False,
            issues=issues,
            warning="Cannot save workflow.",
            health=_issues_health_payload(issues, "Cannot save workflow."),
            status_code=422,
        )
    except ConfigConflictError as exc:
        snapshot = services.config_store.load()
        conflict_form = _draft_form(
            workflow_id=workflow,
            expected_revision=snapshot.revision,
            name=form.name,
            blueprint=form.blueprint,
            integration=form.integration,
            root=form.storage_root,
        )
        return _form_context(
            request,
            services,
            snapshot,
            team_id=team,
            workflow_id=workflow,
            form=conflict_form,
            saved_form=_existing_form(snapshot, team, workflow),
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
                "detail": _form_error_message(exc),
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