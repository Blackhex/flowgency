from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError
from starlette.datastructures import FormData
import yaml

from flowgency.configuration import ValidationFailed
from flowgency.configuration.store import ConfigConflictError
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.workflows.forms import editor_payload, new_editor_snapshot, parse_editor_draft
from flowgency.workflows.library import WorkflowSnapshot
from flowgency.workflows.models import ContractError


router = APIRouter()

_NEW_BLUEPRINT_SENTINEL = "__new__"


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _base_admin_context(request: Request, snapshot) -> dict[str, Any]:
    return {
        "request": request,
        "flowgency_title": snapshot.config.flowgency.title,
        "admin_active": True,
        "active": "admin",
        "admin_page": "workflow-library",
        "theme_css": _theme_css(request),
        "teams": {key: team.name for key, team in snapshot.config.teams.items()},
    }


def _require_workflow_configuration(services: FlowgencyServices):
    if services.workflow_configuration is None:
        raise HTTPException(status_code=409, detail="Workflow library unavailable")
    return services.workflow_configuration


class WorkflowEditorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: StrictStr
    expected_digest: StrictStr | None = None
    draft_version: StrictInt
    draft: dict[str, Any]


def _safe_pydantic_message(error_type: str) -> str:
    return {
        "missing": "Required field is missing.",
        "extra_forbidden": "Unexpected field.",
        "json_invalid": "Payload must be valid JSON.",
        "string_type": "Value must be a string.",
        "dict_type": "Value must be an object.",
        "int_type": "Value must be an integer.",
        "literal_error": "Invalid value.",
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
                "hint": "Correct the submitted workflow draft and try again.",
            }
        )
    return rows


def _safe_draft(decoded: object) -> dict[str, Any]:
    if not isinstance(decoded, Mapping):
        return {}
    draft = decoded.get("draft")
    return dict(draft) if isinstance(draft, dict) else {}


def _safe_draft_version(decoded: object) -> int:
    if not isinstance(decoded, Mapping):
        return 0
    value = decoded.get("draft_version")
    return value if type(value) is int else 0


def _safe_expected_revision(decoded: object, fallback: str) -> str:
    if not isinstance(decoded, Mapping):
        return fallback
    value = decoded.get("expected_revision")
    return value if isinstance(value, str) else fallback


def _safe_expected_digest(decoded: object, fallback: str | None) -> str | None:
    if not isinstance(decoded, Mapping):
        return fallback
    value = decoded.get("expected_digest")
    return value if isinstance(value, str) or value is None else fallback


def _issue(code: str, field: str, message: str, hint: str) -> dict[str, str]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "hint": hint,
    }


def _invalid_request_issues() -> list[dict[str, str]]:
    return [
        _issue(
            "invalid-request",
            "payload",
            "Payload must be valid JSON.",
            "Correct the submitted workflow draft and try again.",
        )
    ]


def _invalid_draft_issues() -> list[dict[str, str]]:
    return [
        _issue(
            "invalid-draft",
            "draft",
            "Workflow draft has invalid structure or references.",
            "Correct the submitted workflow draft and try again.",
        )
    ]


def _source_unavailable_issues() -> list[dict[str, str]]:
    return [
        _issue(
            "source-unavailable",
            "payload",
            "Current workflow source is unavailable.",
            "Reload the workflow library and repair the source before saving again.",
        )
    ]


def _validation_failed_issues(exc: ValidationFailed) -> list[dict[str, str]]:
    return [
        {
            "code": issue.code,
            "field": issue.field,
            "message": issue.message,
            "hint": issue.corrective_hint,
        }
        for issue in exc.issues
    ]


def _form_text(form: FormData, key: str) -> str | None:
    value = form.get(key)
    if value is None:
        return None
    return str(value)


async def _request_payload(request: Request) -> dict[str, Any]:
    form = await request.form()
    raw = _form_text(form, "payload") or ""
    if not raw:
        raise ValidationError.from_exception_data(
            "WorkflowEditorRequest",
            [{"type": "missing", "loc": ("payload",), "input": None}],
        )
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValidationError.from_exception_data(
            "WorkflowEditorRequest",
            [{"type": "dict_type", "loc": ("payload",), "input": decoded}],
        )
    return decoded


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _new_snapshot() -> WorkflowSnapshot:
    return new_editor_snapshot("New workflow")


def _draft_source(snapshot: WorkflowSnapshot, draft: dict[str, Any]) -> WorkflowSnapshot:
    if draft.get("id") == _NEW_BLUEPRINT_SENTINEL:
        return snapshot
    candidate_id = draft.get("id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        return snapshot
    return WorkflowSnapshot(
        definition=snapshot.definition.model_copy(update={"id": candidate_id}),
        digest=snapshot.digest,
        source_path=snapshot.source_path,
    )


def _parseable_new_draft(source: WorkflowSnapshot, draft: dict[str, Any]) -> dict[str, Any]:
    if draft.get("id") != _NEW_BLUEPRINT_SENTINEL:
        return draft
    normalized = dict(draft)
    normalized["id"] = source.definition.id
    return normalized


def _workflow_library(configuration, snapshot):
    return configuration.library_for(snapshot)


def _inspect_snapshot(configuration, snapshot, blueprint_id: str) -> WorkflowSnapshot:
    return _workflow_library(configuration, snapshot).inspect(blueprint_id)


def _list_inspections(configuration, snapshot):
    return _workflow_library(configuration, snapshot).list()


def _preview_bindings(configuration, snapshot, blueprint_id: str) -> int:
    return len(configuration.bindings_using(snapshot, blueprint_id))


def _check_preview_fences(
    payload: WorkflowEditorRequest,
    snapshot,
    source: WorkflowSnapshot | None,
) -> None:
    if payload.expected_revision != snapshot.revision:
        raise ConfigConflictError("config.yaml changed; reload before previewing")
    expected_digest = payload.expected_digest or None
    source_digest = source.digest if source is not None else None
    if expected_digest != source_digest:
        raise ConfigConflictError("Blueprint source changed on disk; reload before previewing")


def _render_unavailable_editor(
    request: Request,
    snapshot,
    blueprint_id: str,
    *,
    status_code: int = 409,
) -> HTMLResponse:
    unavailable = {
        "schema_version": 1,
        "id": blueprint_id,
        "name": blueprint_id,
        "description": "",
        "states": [],
        "fields": [],
        "transitions": [],
    }
    editor_state = {
        "baseline": unavailable,
        "draft": unavailable,
        "expected_revision": snapshot.revision,
        "expected_digest": None,
        "draft_version": 0,
        "issues": _source_unavailable_issues(),
        "warning": "Current workflow source is unavailable until the file is repaired.",
        "create_mode": False,
        "workflow_count": 0,
    }
    return _render_editor(request, snapshot, editor_state=editor_state, status_code=status_code)


def _editor_state(
    *,
    source,
    draft: dict[str, Any],
    expected_revision: str,
    expected_digest: str | None,
    draft_version: int,
    issues: list[dict[str, str]] | None = None,
    warning: str = "",
    create_mode: bool = False,
    references: int = 0,
) -> dict[str, Any]:
    return {
        "baseline": editor_payload(source),
        "draft": draft,
        "expected_revision": expected_revision,
        "expected_digest": expected_digest,
        "draft_version": draft_version,
        "issues": issues or [],
        "warning": warning,
        "create_mode": create_mode,
        "workflow_count": references,
    }


def _render_editor(
    request: Request,
    snapshot,
    *,
    editor_state: dict[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request,
        "workflow_blueprint.html",
        {
            **_base_admin_context(request, snapshot),
            "editor": editor_state["draft"],
            "editor_state_json": json.dumps(editor_state),
            "issues": editor_state["issues"],
            "warning": editor_state["warning"],
            "workflow_count": editor_state["workflow_count"],
            "create_mode": editor_state["create_mode"],
        },
        status_code=status_code,
    )


def _request_issue_response(
    *,
    status_code: int,
    draft_version: int,
    draft: dict[str, Any],
    issues: list[dict[str, str]],
) -> JSONResponse:
    return JSONResponse(
        {
            "draft_version": draft_version,
            "draft": draft,
            "issues": issues,
        },
        status_code=status_code,
    )


@router.get("/admin/workflow-library")
def workflow_library_page(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    snapshot = services.config_store.load()
    configuration = _require_workflow_configuration(services)
    rows = []
    for inspection in _list_inspections(configuration, snapshot):
        references = configuration.bindings_using(snapshot, inspection.blueprint_id)
        rows.append(
            {
                "blueprint_id": inspection.blueprint_id,
                "title": (
                    inspection.snapshot.definition.name
                    if inspection.snapshot is not None
                    else inspection.blueprint_id
                ),
                "issues": list(inspection.issues),
                "workflow_count": len(references),
                "references": [
                    {
                        "team": snapshot.config.teams[binding.team_id].name,
                        "workflow": snapshot.config.teams[binding.team_id].workflows[
                            binding.workflow_id
                        ].name,
                    }
                    for binding in references
                ],
            }
        )
    return _templates(request).TemplateResponse(
        request,
        "workflow_library.html",
        {
            **_base_admin_context(request, snapshot),
            "blueprints": rows,
        },
    )


@router.get("/admin/workflow-library/blueprints/{blueprint_id}")
def workflow_blueprint_page(
    request: Request,
    blueprint_id: str,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    if blueprint_id == "new":
        return new_workflow_blueprint_page(request, services)
    snapshot = services.config_store.load()
    configuration = _require_workflow_configuration(services)
    try:
        source = _inspect_snapshot(configuration, snapshot, blueprint_id)
    except (ContractError, ValidationError, ValueError, yaml.YAMLError, OSError):
        return _render_unavailable_editor(request, snapshot, blueprint_id)
    payload = editor_payload(source)
    references = configuration.bindings_using(snapshot, blueprint_id)
    return _render_editor(
        request,
        snapshot,
        editor_state=_editor_state(
            source=source,
            draft=payload,
            expected_revision=snapshot.revision,
            expected_digest=source.digest,
            draft_version=0,
            references=len(references),
        ),
    )


@router.get("/admin/workflow-library/blueprints/new")
def new_workflow_blueprint_page(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
) -> HTMLResponse:
    snapshot = services.config_store.load()
    source = _new_snapshot()
    payload = editor_payload(source)
    for state in payload["states"]:
        state["existing_state_id"] = None
    return _render_editor(
        request,
        snapshot,
        editor_state=_editor_state(
            source=source,
            draft=payload,
            expected_revision=snapshot.revision,
            expected_digest=None,
            draft_version=0,
            create_mode=True,
        ),
    )


@router.post("/admin/workflow-library/blueprints/{blueprint_id}/preview")
async def preview_workflow_blueprint(
    request: Request,
    blueprint_id: str,
    services: FlowgencyServices = Depends(get_services),
):
    snapshot = await run_in_threadpool(services.config_store.load)
    configuration = _require_workflow_configuration(services)
    decoded: dict[str, Any] | None = None
    payload: WorkflowEditorRequest | None = None
    try:
        decoded = await _request_payload(request)
        payload = WorkflowEditorRequest.model_validate(decoded)
        if blueprint_id == "new":
            source = _draft_source(_new_snapshot(), payload.draft)
            references = 0
            draft_for_parse = _parseable_new_draft(source, payload.draft)
        else:
            source = await run_in_threadpool(_inspect_snapshot, configuration, snapshot, blueprint_id)
            references = await run_in_threadpool(_preview_bindings, configuration, snapshot, blueprint_id)
            draft_for_parse = payload.draft
        _check_preview_fences(payload, snapshot, None if blueprint_id == "new" else source)
        await run_in_threadpool(parse_editor_draft, source, draft_for_parse)
    except json.JSONDecodeError:
        issues = _invalid_request_issues()
        return _request_issue_response(
            status_code=422,
            draft_version=0,
            draft={},
            issues=issues,
        )
    except ValidationError as exc:
        issues = _pydantic_issue_dicts(exc)
        return _request_issue_response(
            status_code=422,
            draft_version=_safe_draft_version(decoded),
            draft=_safe_draft(decoded),
            issues=issues,
        )
    except (ContractError, yaml.YAMLError, OSError):
        return _request_issue_response(
            status_code=409,
            draft_version=payload.draft_version if payload is not None else _safe_draft_version(decoded),
            draft=payload.draft if payload is not None else _safe_draft(decoded),
            issues=_source_unavailable_issues(),
        )
    except ConfigConflictError:
        return _request_issue_response(
            status_code=409,
            draft_version=payload.draft_version if payload is not None else _safe_draft_version(decoded),
            draft=payload.draft if payload is not None else _safe_draft(decoded),
            issues=[
                _issue(
                    "config-conflict",
                    "payload",
                    "Workflow source changed before preview completed.",
                    "Reload the workflow editor and preview again.",
                )
            ],
        )
    except ValueError as error:
        issues = _invalid_draft_issues()
        return _request_issue_response(
            status_code=422,
            draft_version=payload.draft_version if payload is not None else _safe_draft_version(decoded),
            draft=payload.draft if payload is not None else _safe_draft(decoded),
            issues=issues,
        )
    return JSONResponse(
        {
            "draft_version": payload.draft_version,
            "draft": payload.draft,
            "issues": [],
            "workflow_count": references,
        }
    )


@router.post("/admin/workflow-library/blueprints/{blueprint_id}")
async def save_workflow_blueprint(
    request: Request,
    blueprint_id: str,
    services: FlowgencyServices = Depends(get_services),
):
    if blueprint_id == "new":
        return await create_workflow_blueprint(request, services)
    snapshot = await run_in_threadpool(services.config_store.load)
    configuration = _require_workflow_configuration(services)
    decoded: dict[str, Any] | None = None
    payload: WorkflowEditorRequest | None = None
    try:
        decoded = await _request_payload(request)
        payload = WorkflowEditorRequest.model_validate(decoded)
        source = await run_in_threadpool(_inspect_snapshot, configuration, snapshot, blueprint_id)
        candidate = await run_in_threadpool(parse_editor_draft, source, payload.draft)
        await run_in_threadpool(
            configuration.save_blueprint,
            payload.expected_revision,
            blueprint_id,
            payload.expected_digest or "",
            candidate,
        )
    except json.JSONDecodeError:
        issues = _invalid_request_issues()
        if _wants_json(request):
            return _request_issue_response(status_code=422, draft_version=0, draft={}, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(
                source=source,
                draft={},
                expected_revision=snapshot.revision,
                expected_digest=source.digest,
                draft_version=0,
                issues=issues,
                references=len(configuration.bindings_using(snapshot, blueprint_id)),
            ),
            status_code=422,
        )
    except ValidationError as exc:
        issues = _pydantic_issue_dicts(exc)
        draft = _safe_draft(decoded)
        if _wants_json(request):
            return _request_issue_response(status_code=422, draft_version=_safe_draft_version(decoded), draft=draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(
                source=source,
                draft=draft,
                expected_revision=_safe_expected_revision(decoded, snapshot.revision),
                expected_digest=_safe_expected_digest(decoded, source.digest),
                draft_version=_safe_draft_version(decoded),
                issues=issues,
                references=await run_in_threadpool(_preview_bindings, configuration, snapshot, blueprint_id),
            ),
            status_code=422,
        )
    except (ContractError, yaml.YAMLError, OSError):
        issues = _source_unavailable_issues()
        draft = payload.draft if payload is not None else _safe_draft(decoded)
        draft_version = payload.draft_version if payload is not None else _safe_draft_version(decoded)
        expected_digest = None
        if payload is not None:
            expected_digest = payload.expected_digest
        elif 'source' in locals():
            expected_digest = source.digest
        if _wants_json(request):
            return _request_issue_response(status_code=409, draft_version=draft_version, draft=draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(
                source=locals().get('source', _new_snapshot()),
                draft=draft,
                expected_revision=payload.expected_revision if payload is not None else _safe_expected_revision(decoded, snapshot.revision),
                expected_digest=expected_digest,
                draft_version=draft_version,
                issues=issues,
                warning="The current source is unavailable until the file is repaired.",
                references=await run_in_threadpool(_preview_bindings, configuration, snapshot, blueprint_id),
            ),
            status_code=409,
        )
    except ValidationFailed as exc:
        issues = _validation_failed_issues(exc)
        if _wants_json(request):
            return _request_issue_response(status_code=409, draft_version=payload.draft_version, draft=payload.draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(
                source=source,
                draft=payload.draft,
                expected_revision=payload.expected_revision,
                expected_digest=payload.expected_digest,
                draft_version=payload.draft_version,
                issues=issues,
                warning="Compatibility checks failed; reload or correct the draft before saving.",
                references=await run_in_threadpool(_preview_bindings, configuration, snapshot, blueprint_id),
            ),
            status_code=409,
        )
    except ValueError:
        issues = _invalid_draft_issues()
        if _wants_json(request):
            return _request_issue_response(status_code=422, draft_version=payload.draft_version, draft=payload.draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(
                source=source,
                draft=payload.draft,
                expected_revision=payload.expected_revision,
                expected_digest=payload.expected_digest,
                draft_version=payload.draft_version,
                issues=issues,
                references=await run_in_threadpool(_preview_bindings, configuration, snapshot, blueprint_id),
            ),
            status_code=422,
        )
    except ConfigConflictError:
        issues = [
            _issue(
                "config-conflict",
                "payload",
                "Workflow source changed before save completed.",
                "Reload the workflow editor and retry the save.",
            )
        ]
        if _wants_json(request):
            return _request_issue_response(status_code=409, draft_version=payload.draft_version, draft=payload.draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(
                source=source,
                draft=payload.draft,
                expected_revision=payload.expected_revision,
                expected_digest=payload.expected_digest,
                draft_version=payload.draft_version,
                issues=issues,
                warning="The source changed on disk. Reload before saving again.",
                references=await run_in_threadpool(_preview_bindings, configuration, snapshot, blueprint_id),
            ),
            status_code=409,
        )
    return RedirectResponse(f"/admin/workflow-library/blueprints/{blueprint_id}", status_code=303)


@router.post("/admin/workflow-library/blueprints/new")
async def create_workflow_blueprint(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
):
    snapshot = await run_in_threadpool(services.config_store.load)
    configuration = _require_workflow_configuration(services)
    source = _new_snapshot()
    baseline = editor_payload(source)
    decoded: dict[str, Any] | None = None
    payload: WorkflowEditorRequest | None = None
    try:
        decoded = await _request_payload(request)
        payload = WorkflowEditorRequest.model_validate(decoded)
        source = _draft_source(source, payload.draft)
        candidate = await run_in_threadpool(parse_editor_draft, source, _parseable_new_draft(source, payload.draft))
        blueprint_id = candidate.id
        await run_in_threadpool(
            configuration.create_blueprint,
            payload.expected_revision,
            blueprint_id,
            candidate,
        )
    except json.JSONDecodeError:
        issues = _invalid_request_issues()
        if _wants_json(request):
            return _request_issue_response(status_code=422, draft_version=0, draft=baseline, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(source=source, draft=baseline, expected_revision=snapshot.revision, expected_digest=None, draft_version=0, issues=issues, create_mode=True),
            status_code=422,
        )
    except ValidationError as exc:
        issues = _pydantic_issue_dicts(exc)
        draft = _safe_draft(decoded) or baseline
        if _wants_json(request):
            return _request_issue_response(status_code=422, draft_version=_safe_draft_version(decoded), draft=draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(source=source, draft=draft, expected_revision=_safe_expected_revision(decoded, snapshot.revision), expected_digest=None, draft_version=_safe_draft_version(decoded), issues=issues, create_mode=True),
            status_code=422,
        )
    except (ContractError, yaml.YAMLError, OSError):
        issues = _source_unavailable_issues()
        draft = payload.draft if payload is not None else baseline
        draft_version = payload.draft_version if payload is not None else _safe_draft_version(decoded)
        if _wants_json(request):
            return _request_issue_response(status_code=409, draft_version=draft_version, draft=draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(source=source, draft=draft, expected_revision=payload.expected_revision if payload is not None else snapshot.revision, expected_digest=None, draft_version=draft_version, issues=issues, warning="The source is unavailable until the file is repaired.", create_mode=True),
            status_code=409,
        )
    except ValueError:
        issues = _invalid_draft_issues()
        if _wants_json(request):
            return _request_issue_response(status_code=422, draft_version=payload.draft_version, draft=payload.draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(source=source, draft=payload.draft, expected_revision=payload.expected_revision, expected_digest=None, draft_version=payload.draft_version, issues=issues, create_mode=True),
            status_code=422,
        )
    except ConfigConflictError:
        issues = [
            _issue(
                "config-conflict",
                "payload",
                "Workflow source changed before save completed.",
                "Reload the workflow editor and retry the save.",
            )
        ]
        if _wants_json(request):
            return _request_issue_response(status_code=409, draft_version=payload.draft_version, draft=payload.draft, issues=issues)
        return _render_editor(
            request,
            snapshot,
            editor_state=_editor_state(source=source, draft=payload.draft, expected_revision=payload.expected_revision, expected_digest=None, draft_version=payload.draft_version, issues=issues, warning="Reload before saving again.", create_mode=True),
            status_code=409,
        )
    return RedirectResponse(f"/admin/workflow-library/blueprints/{blueprint_id}", status_code=303)
