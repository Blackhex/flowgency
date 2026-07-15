from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agency.configuration import (
    ABSENT_REVISION,
    ConfigConflictError,
    GroupCreateStatePatch,
    GroupSettingsStatePatch,
    ValidationFailed,
    create_group_state,
    patch_group_settings_state,
    validate_config_canonical,
)
from agency.integrations import REGISTRY
from agency.web.dependencies import AgencyServices, get_services


router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _workspace_types_json(request: Request) -> str:
    return request.app.state.workspace_types_json_getter()


def _base_admin_context(request: Request, snapshot=None) -> dict:
    groups = {}
    title = "Agency"
    if snapshot is not None:
        groups = {
            key: group.name
            for key, group in snapshot.config.groups.items()
        }
        title = snapshot.config.agency.title
    return {
        "request": request,
        "agency_title": title,
        "admin_active": True,
        "active": "admin",
        "admin_page": "groups",
        "theme_css": _theme_css(request),
        "groups": groups,
    }


def _diagnostic_issues(services: AgencyServices) -> list[dict]:
    error = services.startup_error
    if error is None:
        return []
    if isinstance(error, ValidationFailed):
        return [
            {
                "field": issue.field,
                "message": issue.message,
                "corrective_hint": issue.corrective_hint,
            }
            for issue in error.issues
        ]
    return [
        {
            "field": "startup",
            "message": str(error),
            "corrective_hint": "Fix the configuration and reload the page.",
        }
    ]


def _setup_response(
    request: Request,
    services: AgencyServices,
    *,
    values=None,
    error="",
    status_code: int = 200,
):
    values = values or {}
    return _templates(request).TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "agency_title": "Agency",
            "error": error,
            "issues": _diagnostic_issues(services),
            "group_key": values.get("group_key", ""),
            "group_name": values.get("group_name", ""),
            "path_value": values.get("path", ""),
            "agent_library_value": values.get("agent_library", ""),
            "compilation_cache_value": values.get("compilation_cache", ""),
            "memory_store_value": values.get("memory_store", ""),
            "workspace_name": values.get("workspace_name", ""),
            "workspace_type": values.get("workspace_type", "tmux"),
            "workspace_config": values.get("workspace_config", "{}"),
            "expected_revision": values.get(
                "expected_revision", ABSENT_REVISION
            ),
        },
        status_code=status_code,
    )


def _group_settings_response(
    request: Request,
    snapshot,
    group_id: str,
    *,
    warning: str = "",
    status_code: int = 200,
):
    group = snapshot.config.groups[group_id]
    runtime = group.runtime
    sandbox = runtime.sandbox
    tools = runtime.tools
    dispatch = group.dispatch
    return _templates(request).TemplateResponse(
        request,
        "admin_org_edit.html",
        {
            **_base_admin_context(request, snapshot),
            "mode": "edit",
            "org_key": group_id,
            "org_name": group.name,
            "org_path": str(group.path),
            "org_workspaces_json": json.dumps(
                [
                    workspace.model_dump(mode="json")
                    for workspace in group.workspaces
                ]
            ),
            "workspace_types_json": _workspace_types_json(request),
            "default_integration": group.default_integration,
            "runtime_timeout": runtime.timeout,
            "sandbox_mode": sandbox.mode,
            "sandbox_roots": "\n".join(str(root) for root in sandbox.roots),
            "tool_mode": tools.mode,
            "tool_names": "\n".join(tools.names),
            "dispatch_enabled": dispatch.enabled,
            "dispatch_daily_limit": dispatch.daily_limit,
            "agent_count": len(group.agents),
            "manage_agents_href": f"/{group_id}/agents",
            "warning": warning,
            "revision": snapshot.revision,
        },
        status_code=status_code,
    )


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _integration_names() -> list[str]:
    return sorted(REGISTRY)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    file_snapshot = services.config_store.inspect()
    if services.startup_error is None:
        try:
            snapshot = services.config_store.load()
            if snapshot.config.groups:
                return RedirectResponse("/", status_code=303)
        except Exception:
            pass
    return _setup_response(
        request,
        services,
        values={"expected_revision": file_snapshot.revision},
    )


@router.post("/setup", response_class=HTMLResponse)
async def setup_process(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    form = await request.form()
    values = {
        "expected_revision": (
            str(form.get("expected_revision", ABSENT_REVISION)).strip()
            or ABSENT_REVISION
        ),
        "group_key": str(form.get("group_key", "")).strip().lower(),
        "group_name": str(form.get("group_name", "")).strip(),
        "path": str(form.get("path", "")).strip(),
        "agent_library": str(form.get("agent_library", "")).strip(),
        "compilation_cache": str(form.get("compilation_cache", "")).strip(),
        "memory_store": str(form.get("memory_store", "")).strip(),
        "workspace_name": str(form.get("workspace_name", "")).strip(),
        "workspace_type": (
            str(form.get("workspace_type", "tmux")).strip() or "tmux"
        ),
        "workspace_config": (
            str(form.get("workspace_config", "{}")).strip() or "{}"
        ),
    }
    required_fields = (
        "group_key",
        "group_name",
        "path",
        "agent_library",
        "compilation_cache",
        "memory_store",
    )
    if any(not values[key] for key in required_fields):
        return _setup_response(
            request,
            services,
            values=values,
            error="All setup fields are required for strict canonical configuration.",
        )
    try:
        workspace_config = json.loads(values["workspace_config"])
        if not isinstance(workspace_config, dict):
            raise TypeError
    except (json.JSONDecodeError, TypeError):
        return _setup_response(
            request,
            services,
            values=values,
            error="Workspace config must be a JSON object.",
        )

    raw = {
        "schema_version": 2,
        "agency": {
            "title": "Agency",
            "default_group": values["group_key"],
            "ai_backend": "claude-code",
            "agent_library": values["agent_library"],
            "compilation_cache": values["compilation_cache"],
            "memory_store": values["memory_store"],
        },
        "memory": {"channels": {}},
        "groups": {
            values["group_key"]: {
                "name": values["group_name"],
                "path": values["path"],
                "default_integration": "claude-code",
                "dispatch": {"enabled": False, "daily_limit": 20},
                "workspaces": [
                    {
                        "name": values["workspace_name"] or "Workspace",
                        "type": values["workspace_type"],
                        "config": workspace_config,
                    }
                ],
                "agents": [],
            }
        },
    }
    issues = validate_config_canonical(raw, services.config_path)
    if issues:
        return _templates(request).TemplateResponse(
            request,
            "setup.html",
            {
                "request": request,
                "agency_title": "Agency",
                "error": "Setup values do not satisfy the strict canonical schema.",
                "issues": [
                    {
                        "field": issue.field,
                        "message": issue.message,
                        "corrective_hint": issue.corrective_hint,
                    }
                    for issue in issues
                ],
                "group_key": values["group_key"],
                "group_name": values["group_name"],
                "path_value": values["path"],
                "agent_library_value": values["agent_library"],
                "compilation_cache_value": values["compilation_cache"],
                "memory_store_value": values["memory_store"],
                "workspace_name": values["workspace_name"],
                "workspace_type": values["workspace_type"],
                "workspace_config": values["workspace_config"],
            },
        )

    try:
        services.config_store.replace(values["expected_revision"], raw)
    except ConfigConflictError:
        current = services.config_store.inspect()
        return _setup_response(
            request,
            services,
            values={**values, "expected_revision": current.revision},
            error="Configuration changed. Reload before saving setup.",
            status_code=409,
        )
    request.app.state.services = request.app.state.build_services(
        services.config_path
    )
    request.app.state.reload_groups()
    return RedirectResponse(f"/{values['group_key']}/agents", status_code=303)


@router.get("/admin/orgs/{org}/edit", response_class=HTMLResponse)
async def admin_org_edit(
    request: Request,
    org: str,
    services: AgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(request, services)
    snapshot = services.config_store.load()
    if org not in snapshot.config.groups:
        raise HTTPException(status_code=404, detail=f"Unknown org: {org}")
    return _group_settings_response(request, snapshot, org)


@router.post("/admin/orgs/{org}/save", response_class=HTMLResponse)
async def admin_org_save(
    request: Request,
    org: str,
    services: AgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(request, services)
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    name = str(form.get("name", "")).strip()
    path = str(form.get("path", "")).strip()
    default_integration = str(form.get("default_integration", "")).strip()
    runtime_timeout = int(str(form.get("runtime_timeout", "1800")) or "1800")
    sandbox_mode = (
        str(form.get("sandbox_mode", "unrestricted")).strip()
        or "unrestricted"
    )
    sandbox_roots = _split_lines(str(form.get("sandbox_roots", "")))
    tool_mode = str(form.get("tool_mode", "all")).strip() or "all"
    tool_names = _split_lines(str(form.get("tool_names", "")))
    dispatch_enabled = form.get("dispatch_enabled") == "on"
    daily_limit = int(str(form.get("daily_limit", "20")) or "20")
    workspaces_json = str(form.get("workspaces_json", "[]"))
    try:
        workspaces = json.loads(workspaces_json)
        if not isinstance(workspaces, list):
            raise TypeError
    except (json.JSONDecodeError, TypeError):
        snapshot = services.config_store.load()
        return _group_settings_response(
            request,
            snapshot,
            org,
            warning="Workspaces payload is invalid.",
            status_code=409,
        )

    try:
        patch_group_settings_state(
            services.config_store,
            revision,
            org,
            GroupSettingsStatePatch(
                name=name,
                path=path,
                default_integration=default_integration,
                runtime_timeout=runtime_timeout,
                sandbox_mode=sandbox_mode,
                sandbox_roots=tuple(sandbox_roots),
                tool_mode=tool_mode,
                tool_names=tuple(tool_names),
                dispatch_enabled=dispatch_enabled,
                dispatch_daily_limit=daily_limit,
                workspaces=tuple(workspaces),
            ),
        )
    except ConfigConflictError:
        snapshot = services.config_store.load()
        return _group_settings_response(
            request,
            snapshot,
            org,
            warning="Configuration changed. Reload before saving.",
            status_code=409,
        )

    request.app.state.reload_groups()
    return RedirectResponse(f"/admin/orgs/{org}/edit", status_code=303)


@router.post("/admin/orgs/create", response_class=HTMLResponse)
async def admin_org_create(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(request, services)
    form = await request.form()
    key = str(form.get("key", "")).strip().lower().replace(" ", "-")
    name = str(form.get("name", "")).strip()
    path = str(form.get("path", "")).strip()
    if not key or not name or not path:
        snapshot = services.config_store.load()
        return _templates(request).TemplateResponse(
            request,
            "admin_org_edit.html",
            {
                **_base_admin_context(request, snapshot),
                "mode": "create",
                "org_key": key,
                "org_name": name,
                "org_path": path,
                "default_integration": str(form.get("default_integration", "")).strip(),
                "org_workspaces_json": str(form.get("workspaces_json", "[]")),
                "workspace_types_json": _workspace_types_json(request),
                "warning": "Key, name, and path are required.",
                "integration_names": _integration_names(),
            },
        )
    snapshot = services.config_store.load()
    roots = _split_lines(str(form.get("sandbox_root", "")))
    default_integration = str(form.get("default_integration", "")).strip()
    if default_integration and default_integration not in REGISTRY:
        return _templates(request).TemplateResponse(
            request,
            "admin_org_edit.html",
            {
                **_base_admin_context(request, snapshot),
                "mode": "create",
                "org_key": key,
                "org_name": name,
                "org_path": path,
                "default_integration": default_integration,
                "org_workspaces_json": str(form.get("workspaces_json", "[]")),
                "workspace_types_json": _workspace_types_json(request),
                "warning": f"Integration '{default_integration}' is not registered.",
                "integration_names": _integration_names(),
            },
            status_code=409,
        )
    tools = [
        item.strip() for item in form.getlist("allowed_tools") if item.strip()
    ]
    workspaces_json = str(form.get("workspaces_json", "[]"))
    try:
        workspaces = json.loads(workspaces_json)
        if not isinstance(workspaces, list):
            raise TypeError
    except (json.JSONDecodeError, TypeError):
        return _templates(request).TemplateResponse(
            request,
            "admin_org_edit.html",
            {
                **_base_admin_context(request, snapshot),
                "mode": "create",
                "org_key": key,
                "org_name": name,
                "org_path": path,
                "default_integration": default_integration,
                "org_workspaces_json": workspaces_json,
                "workspace_types_json": _workspace_types_json(request),
                "warning": "Workspaces payload is invalid.",
                "integration_names": _integration_names(),
            },
            status_code=409,
        )
    create_group_state(
        services.config_store,
        snapshot.revision,
        key,
        GroupCreateStatePatch(
            name=name,
            path=path,
            default_integration=default_integration or "claude-code",
            runtime_timeout=1800,
            sandbox_mode="restricted" if roots else "unrestricted",
            sandbox_roots=tuple(roots),
            tool_mode="allowlist" if tools else "all",
            tool_names=tuple(tools),
            dispatch_enabled=False,
            dispatch_daily_limit=20,
            workspaces=tuple(workspaces),
        ),
    )
    request.app.state.reload_groups()
    return RedirectResponse("/admin/groups", status_code=303)
