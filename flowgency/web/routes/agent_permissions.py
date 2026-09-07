from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markupsafe import Markup
from pydantic import ValidationError

from flowgency.configuration import ConfigConflictError, ConfigSnapshot, ValidationFailed, ValidationIssue
from flowgency.integrations import get_integration
from flowgency.integrations.tool_catalog import available_names, catalog_id, get_tool_catalog
from flowgency.permissions.editor import CatalogConflictError, EditorRequest, load_editor, prepare_permissions, save_permissions
from flowgency.permissions.forms import PermissionDraft, PermissionForm
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.routes.agent_detail import _detail_context, _get_snapshot_instance


logger = logging.getLogger(__name__)

router = APIRouter()


def issue_dicts(issues: tuple[ValidationIssue, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for issue in issues:
        row: dict[str, str] = {}
        for field_name in ValidationIssue.__dataclass_fields__:
            value = getattr(issue, field_name)
            key = "hint" if field_name == "corrective_hint" else field_name
            row[key] = str(value)
        rows.append(row)
    return rows


def _safe_pydantic_message(error_type: str) -> str:
    return {
        "missing": "Required field is missing.",
        "extra_forbidden": "Unexpected field.",
        "literal_error": "Choose a supported value.",
        "dict_type": "Value must be an object.",
        "model_type": "Value must be an object.",
        "list_type": "Value must be a list.",
        "string_type": "Value must be a string.",
        "string_too_short": "Value must not be blank.",
        "greater_than_equal": "Value is below the allowed minimum.",
        "int_type": "Value must be an integer.",
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
                "hint": "Correct the permission payload and try again.",
            }
        )
    return rows


def _catalog_payload(catalog) -> dict[str, Any]:
    return {
        "integration": catalog.integration,
        "version": catalog.version,
        "complete": catalog.complete,
        "warning": catalog.warning,
        "tools": [
            {"name": tool.name, "targets": list(tool.targets)}
            for tool in catalog.tools
        ],
    }


def _editor_request(revision: str, current_catalog_id: str, draft_version: int, draft: PermissionDraft) -> EditorRequest:
    return EditorRequest(
        revision=revision,
        catalog_id=current_catalog_id,
        draft_version=draft_version,
        draft=draft.model_copy(deep=True),
    )


def _merged_choices(form: PermissionForm, draft: PermissionDraft, catalog) -> tuple[tuple[str, ...], ...]:
    merged: list[tuple[str, ...]] = []
    for index, rule in enumerate(draft.rules):
        if rule.source_index is not None and rule.source_index < len(form.choices):
            names = list(form.choices[rule.source_index])
        else:
            names = list(available_names(catalog, rule.target))
        seen = set(names)
        for name in rule.selected:
            if name not in seen:
                names.append(name)
                seen.add(name)
        merged.append(tuple(names))
    return tuple(merged)


def _merged_unbounded(form: PermissionForm, draft: PermissionDraft) -> tuple[bool, ...]:
    values: list[bool] = []
    for rule in draft.rules:
        if rule.source_index is not None and rule.source_index < len(form.unbounded):
            values.append(form.unbounded[rule.source_index])
        else:
            values.append(False)
    return tuple(values)


def _rule_rows(draft: PermissionDraft, choices: tuple[tuple[str, ...], ...], unbounded: tuple[bool, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, rule in enumerate(draft.rules):
        row_id = f"permission-rule-{index}"
        rows.append(
            {
                "row_id": row_id,
                "remove_id": f"{row_id}-remove",
                "custom_tool_id": f"{row_id}-custom-tool",
                "add_custom_tool_id": f"{row_id}-add-custom-tool",
                "index": index,
                "source_index": "" if rule.source_index is None else str(rule.source_index),
                "target": rule.target,
                "path": rule.path or "",
                "selected": set(rule.selected),
                "choices": choices[index] if index < len(choices) else (),
                "unbounded": bool(unbounded[index]) if index < len(unbounded) else False,
            }
        )
    return rows


def _render_summary_html(request: Request, summary, *, summary_label: str) -> str:
    if summary is None:
        return ""
    return request.app.state.templates.env.get_template(
        "agent_permissions_summary.html"
    ).render(summary=summary, summary_label=summary_label)


def _failure_json(
    draft_version: int,
    code: str,
    issues: list[dict[str, str]],
    *,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        {
            "draft_version": draft_version,
            "code": code,
            "issues": issues,
            "summary_html": None,
        },
        status_code=status_code,
    )


def _fallback_draft_version(decoded: Any) -> int:
    if not isinstance(decoded, dict):
        return 0
    value = decoded.get("draft_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def render_permissions_page(
    request: Request,
    services: FlowgencyServices,
    snapshot: ConfigSnapshot,
    team: str,
    agent: str,
    *,
    submitted: EditorRequest | None = None,
    issues: tuple[ValidationIssue, ...] = (),
    conflict: bool = False,
    status_code: int = 200,
):
    prepared = load_editor(snapshot, team, agent)
    current_catalog_id = catalog_id(prepared.catalog)
    baseline = _editor_request(snapshot.revision, current_catalog_id, 0, prepared.form.draft)
    current_draft = submitted or baseline
    page_issues = issues
    if prepared.summary is None:
        page_issues = (*page_issues, *prepared.issues)
    display_choices = _merged_choices(prepared.form, current_draft.draft, prepared.catalog)
    display_unbounded = _merged_unbounded(prepared.form, current_draft.draft)
    permission_rows = _rule_rows(current_draft.draft, display_choices, display_unbounded)
    saved_summary_html = _render_summary_html(request, prepared.summary, summary_label="Saved permissions")
    summary_label = "Current saved permissions" if submitted is not None or page_issues or conflict else "Saved permissions"
    summary_html = _render_summary_html(request, prepared.summary, summary_label=summary_label)
    permissions_initial = {
        "baseline": baseline.model_dump(mode="json"),
        "draft": current_draft.model_dump(mode="json"),
        "choices": [list(choice) for choice in display_choices],
        "unbounded": list(display_unbounded),
        "catalog": _catalog_payload(prepared.catalog),
        "catalog_id": current_catalog_id,
        "preview_url": f"/{team}/agents/{agent}/permissions/preview",
        "save_url": f"/{team}/agents/{agent}/permissions",
        "saved_summary_html": saved_summary_html,
        "conflict": conflict,
        "issues": issue_dicts(page_issues),
    }
    return _detail_context(
        request,
        services,
        team,
        agent,
        "permissions",
        snapshot=snapshot,
        status_code=status_code,
        issues=issue_dicts(page_issues),
        overrides={
            "permissions_initial": permissions_initial,
            "permission_rows": permission_rows,
            "permission_summary_html": Markup(summary_html),
            "permission_summary_available": prepared.summary is not None,
            "permission_workspace_write": prepared.summary.workspace_write if prepared.summary is not None else False,
            "permission_conflict": conflict,
        },
    )


@router.get("/{team}/agents/{agent}/permissions", response_class=HTMLResponse)
async def permissions_page(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    _get_snapshot_instance(snapshot, team, agent)
    return render_permissions_page(request, services, snapshot, team, agent)


@router.post("/{team}/agents/{agent}/permissions/preview")
async def permissions_preview(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
) -> JSONResponse:
    try:
        decoded = await request.json()
    except json.JSONDecodeError:
        return _failure_json(
            0,
            "malformed-transport",
            [
                {
                    "code": "malformed-transport",
                    "field": "payload",
                    "message": "Request body must be valid JSON.",
                    "hint": "Resubmit the current permissions draft.",
                }
            ],
            status_code=422,
        )

    draft_version = _fallback_draft_version(decoded)
    try:
        submitted = EditorRequest.model_validate(decoded)
        snapshot = services.config_store.load()
        _, instance = _get_snapshot_instance(snapshot, team, agent)
        catalog = get_tool_catalog(get_integration(instance.integration))
        if catalog.version == "unavailable":
            return _failure_json(
                submitted.draft_version,
                "preview-unavailable",
                [
                    {
                        "code": "preview-unavailable",
                        "field": "catalog",
                        "message": "Preview is temporarily unavailable.",
                        "hint": "Reload and try again once tool metadata is available.",
                    }
                ],
                status_code=503,
            )
        prepared = prepare_permissions(snapshot, team, agent, submitted, catalog)
    except ValidationError as exc:
        return _failure_json(draft_version, "invalid-request", _pydantic_issue_dicts(exc), status_code=422)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    except ConfigConflictError:
        return _failure_json(
            draft_version,
            "config-conflict",
            [
                {
                    "code": "config-conflict",
                    "field": "revision",
                    "message": "Configuration changed while this draft was open.",
                    "hint": "Reload before saving permissions again.",
                }
            ],
            status_code=409,
        )
    except CatalogConflictError:
        return _failure_json(
            draft_version,
            "catalog-conflict",
            [
                {
                    "code": "catalog-conflict",
                    "field": "catalog_id",
                    "message": "Tool metadata changed while this draft was open.",
                    "hint": "Reload before saving permissions again.",
                }
            ],
            status_code=409,
        )
    except ValidationFailed as exc:
        return _failure_json(draft_version, "validation-failed", issue_dicts(exc.issues), status_code=422)
    except Exception:
        logger.exception("Unexpected permissions preview failure", extra={"team": team, "agent": agent})
        raise

    summary_html = _render_summary_html(request, prepared.summary, summary_label="Draft preview")
    return JSONResponse(
        {
            "draft_version": submitted.draft_version,
            "revision": snapshot.revision,
            "catalog_id": catalog_id(catalog),
            "summary_html": summary_html,
            "workspace_write": prepared.summary.workspace_write,
            "issues": [],
        }
    )


@router.post("/{team}/agents/{agent}/permissions", response_class=HTMLResponse)
async def permissions_save(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
):
    form = await request.form()
    payload_text = str(form.get("payload", ""))
    snapshot = services.config_store.load()
    _get_snapshot_instance(snapshot, team, agent)

    try:
        decoded = json.loads(payload_text)
    except json.JSONDecodeError:
        return render_permissions_page(
            request,
            services,
            snapshot,
            team,
            agent,
            issues=(
                ValidationIssue(
                    code="malformed-transport",
                    scope="permissions",
                    field="payload",
                    message="Payload must be valid JSON.",
                    corrective_hint="Reload the page and retry the save.",
                ),
            ),
            status_code=422,
        )

    submitted: EditorRequest | None = None
    try:
        submitted = EditorRequest.model_validate(decoded)
        save_permissions(services.config_store, team, agent, submitted)
    except ValidationError as exc:
        return render_permissions_page(
            request,
            services,
            snapshot,
            team,
            agent,
            issues=tuple(
                ValidationIssue(
                    code=row["code"],
                    scope=row["field"].rsplit(".", 1)[0] if "." in row["field"] else row["field"],
                    field=row["field"],
                    message=row["message"],
                    corrective_hint=row["hint"],
                )
                for row in _pydantic_issue_dicts(exc)
            ),
            status_code=422,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    except ConfigConflictError:
        if submitted is None:
            raise HTTPException(status_code=409, detail="config.yaml changed; reload before saving")
        return render_permissions_page(
            request,
            services,
            services.config_store.load(),
            team,
            agent,
            submitted=submitted,
            issues=(
                ValidationIssue(
                    code="config-conflict",
                    scope="revision",
                    field="revision",
                    message="Configuration changed while this draft was open.",
                    corrective_hint="Reload before saving permissions again.",
                ),
            ),
            conflict=True,
            status_code=409,
        )
    except CatalogConflictError:
        if submitted is None:
            raise HTTPException(status_code=409, detail="Permission tool catalog changed; reload before saving")
        return render_permissions_page(
            request,
            services,
            snapshot,
            team,
            agent,
            submitted=submitted,
            issues=(
                ValidationIssue(
                    code="catalog-conflict",
                    scope="catalog_id",
                    field="catalog_id",
                    message="Tool metadata changed while this draft was open.",
                    corrective_hint="Reload before saving permissions again.",
                ),
            ),
            conflict=True,
            status_code=409,
        )
    except ValidationFailed as exc:
        if submitted is None:
            raise
        return render_permissions_page(
            request,
            services,
            snapshot,
            team,
            agent,
            submitted=submitted,
            issues=tuple(exc.issues),
            status_code=422,
        )

    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/permissions", status_code=303)