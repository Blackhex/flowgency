from __future__ import annotations

import json
from ipaddress import ip_address
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from agency.configuration import (
    ConfigConflictError,
    delete_group,
    GroupCreateStatePatch,
    GroupSettingsStatePatch,
    ValidationFailed,
    create_group_state,
    patch_group_settings_state,
)
from agency.integrations import BaseIntegration, REGISTRY
from agency.integrations.models import InteractiveSetupRequest
from agency.jobs.store import revision_bound_group_operation
from agency.web.dependencies import AgencyServices, build_services, get_services
from agency.web.directory_browser import DirectoryBrowseError, list_directories
from agency.web.setup_flow import (
    build_setup_prompt,
    inspect_setup_status,
    launchable_integrations,
    startup_error_status,
)


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
    status,
    waiting: bool = False,
    project_dir_value: str = "",
    selected_integration: str = "",
    selected_integration_name: str = "",
    integrations: tuple[BaseIntegration, ...] = (),
    fallback_command: str = "",
    launch_notice: str = "",
    error: str = "",
    status_code: int = 200,
):
    return _templates(request).TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "agency_title": "Agency",
            "error": error,
            "issues": (
                _diagnostic_issues(services) if status.state == "invalid" else []
            ),
            "status_state": status.state,
            "status_message": status.message,
            "waiting": waiting,
            "project_dir_value": project_dir_value,
            "selected_integration": selected_integration,
            "selected_integration_name": selected_integration_name,
            "integrations": integrations,
            "fallback_command": fallback_command,
            "launch_notice": launch_notice,
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


def _canonical_group_path(config_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def _setup_project_seed(services: AgencyServices, project_dir_value: str) -> Path:
    candidate = (
        Path(project_dir_value).expanduser()
        if project_dir_value
        else services.config_path.parent
    )
    try:
        return candidate.resolve()
    except OSError:
        return services.config_path.parent.resolve()


def _setup_integrations(
    services: AgencyServices,
    project_dir_value: str,
) -> tuple[BaseIntegration, ...]:
    return tuple(
        launchable_integrations(
            services.integrations,
            _setup_project_seed(services, project_dir_value),
        )
    )


def _select_integration(
    integrations: tuple[BaseIntegration, ...],
    requested_name: str,
) -> tuple[str, str]:
    if requested_name:
        for integration in integrations:
            if integration.name == requested_name:
                return integration.name, integration.display_name
    if integrations:
        return integrations[0].name, integrations[0].display_name
    return "", ""


def _rebuild_services(request: Request, services: AgencyServices) -> AgencyServices:
    builder = getattr(request.app.state, "build_services", build_services)
    refreshed = builder(services.config_path)
    request.app.state.services = refreshed
    return refreshed


def _setup_status_with_fresh_services(
    request: Request,
    services: AgencyServices,
):
    status = inspect_setup_status(services.config_store)
    if status.state != "ready":
        return services, status
    if services.startup_error is None and services.instances is not None:
        return services, status
    refreshed = _rebuild_services(request, services)
    if refreshed.startup_error is None and refreshed.instances is not None:
        return refreshed, status
    error = refreshed.startup_error or RuntimeError("services are unavailable")
    return refreshed, startup_error_status(error)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    services, status = _setup_status_with_fresh_services(request, services)
    if status.state == "ready":
        return RedirectResponse("/", status_code=303)
    integrations = _setup_integrations(services, "")
    selected_integration, selected_integration_name = _select_integration(
        integrations,
        "",
    )
    return _setup_response(
        request,
        services,
        status=status,
        integrations=integrations,
        selected_integration=selected_integration,
        selected_integration_name=selected_integration_name,
    )


@router.post("/setup/launch", response_class=HTMLResponse)
async def setup_launch(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    status = inspect_setup_status(services.config_store)
    if status.state == "ready":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    project_dir_value = str(form.get("project_dir", "")).strip()
    requested_integration = str(form.get("integration", "")).strip()
    integrations = _setup_integrations(services, project_dir_value)
    selected_integration, selected_integration_name = _select_integration(
        integrations,
        requested_integration,
    )

    project_dir = Path(project_dir_value).expanduser() if project_dir_value else None
    if (
        project_dir is None
        or not project_dir.is_absolute()
        or not project_dir.exists()
        or not project_dir.is_dir()
    ):
        return _setup_response(
            request,
            services,
            status=status,
            project_dir_value=project_dir_value,
            integrations=integrations,
            selected_integration=selected_integration,
            selected_integration_name=selected_integration_name,
            error="Select an absolute existing project folder.",
        )
    launchable_by_name = {item.name: item for item in integrations}
    if requested_integration not in launchable_by_name:
        return _setup_response(
            request,
            services,
            status=status,
            project_dir_value=str(project_dir.resolve()),
            integrations=integrations,
            selected_integration=selected_integration,
            selected_integration_name=selected_integration_name,
            error="Choose an available integration.",
        )
    resolved_project_dir = project_dir.resolve()
    integration = launchable_by_name[requested_integration]
    setup_request = InteractiveSetupRequest(
        project_dir=resolved_project_dir,
        config_path=services.config_path.resolve(),
        prompt=build_setup_prompt(resolved_project_dir, services.config_path),
    )
    fallback_command = ""
    launch_notice = ""
    try:
        result = await run_in_threadpool(
            integration.launch_interactive_setup,
            setup_request,
        )
        if result.fallback_command:
            fallback_command = result.fallback_command
        else:
            fallback_command = integration.interactive_setup_fallback_command(
                setup_request
            )
    except Exception as exc:
        fallback_command = integration.interactive_setup_fallback_command(
            setup_request
        )
        launch_notice = str(exc).strip()
    return _setup_response(
        request,
        services,
        status=status,
        waiting=True,
        project_dir_value=str(resolved_project_dir),
        integrations=integrations,
        selected_integration=requested_integration,
        selected_integration_name=launchable_by_name[requested_integration].display_name,
        fallback_command=fallback_command,
        launch_notice=launch_notice,
    )


@router.post("/setup/browse")
async def setup_browse(
    request: Request,
    services: AgencyServices = Depends(get_services),
) -> JSONResponse:
    client_host = request.client.host if request.client is not None else ""
    try:
        is_loopback = ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        return JSONResponse(
            {
                "error": "Folder browsing is available only from this computer.",
            },
            status_code=403,
        )

    form = await request.form()
    requested_path = str(form.get("path", "")).strip()
    try:
        listing = await run_in_threadpool(
            list_directories,
            requested_path,
            default_path=services.config_path.parent,
        )
    except DirectoryBrowseError as exc:
        return JSONResponse(
            {
                "error": str(exc),
            },
            status_code=400,
        )
    return JSONResponse(
        {
            "path": str(listing.path),
            "parent": str(listing.parent),
            "roots": [str(root) for root in listing.roots],
            "directories": [
                {
                    "name": directory.name,
                    "path": str(directory.path),
                }
                for directory in listing.directories
            ],
        }
    )


@router.get("/setup/status")
async def setup_status(
    request: Request,
    services: AgencyServices = Depends(get_services),
) -> JSONResponse:
    services, status = _setup_status_with_fresh_services(request, services)
    payload: dict[str, str] = {"state": status.state}
    if status.state == "ready":
        payload["redirect"] = "/"
        return JSONResponse(payload)
    if status.message:
        payload["message"] = status.message
    return JSONResponse(payload)


@router.get("/admin/orgs/{org}/edit", response_class=HTMLResponse)
async def admin_org_edit(
    request: Request,
    org: str,
    services: AgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(
            request,
            services,
            status=inspect_setup_status(services.config_store),
        )
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
        return _setup_response(
            request,
            services,
            status=inspect_setup_status(services.config_store),
        )
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
        with revision_bound_group_operation(
            services.config_store,
            group_ids=(org,),
            proposed_paths=(
                _canonical_group_path(services.config_path, path),
            ),
            expected_revision=revision,
        ) as locked:
            patch_group_settings_state(
                services.config_store,
                locked.revision,
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

    request.app.state.refresh_services()
    return RedirectResponse(f"/admin/orgs/{org}/edit", status_code=303)


@router.post("/admin/orgs/create", response_class=HTMLResponse)
async def admin_org_create(
    request: Request,
    services: AgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(
            request,
            services,
            status=inspect_setup_status(services.config_store),
        )
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
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
                "default_integration": str(
                    form.get("default_integration", "")
                ).strip(),
                "org_workspaces_json": str(form.get("workspaces_json", "[]")),
                "workspace_types_json": _workspace_types_json(request),
                "warning": "Key, name, and path are required.",
                "integration_names": _integration_names(),
                "revision": snapshot.revision,
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
                "warning": (
                    f"Integration '{default_integration}' is not registered."
                ),
                "integration_names": _integration_names(),
                "revision": snapshot.revision,
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
                "revision": snapshot.revision,
            },
            status_code=409,
        )
    try:
        with revision_bound_group_operation(
            services.config_store,
            proposed_paths=(
                _canonical_group_path(services.config_path, path),
            ),
            expected_revision=revision,
        ) as locked:
            create_group_state(
                services.config_store,
                locked.revision,
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
    except ConfigConflictError:
        current = services.config_store.load()
        return _templates(request).TemplateResponse(
            request,
            "admin_org_edit.html",
            {
                **_base_admin_context(request, current),
                "mode": "create",
                "org_key": key,
                "org_name": name,
                "org_path": path,
                "default_integration": default_integration,
                "org_workspaces_json": workspaces_json,
                "workspace_types_json": _workspace_types_json(request),
                "warning": "Configuration changed. Reload before saving.",
                "integration_names": _integration_names(),
                "revision": current.revision,
            },
            status_code=409,
        )
    request.app.state.refresh_services()
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/orgs/{org}/delete", response_class=HTMLResponse)
async def admin_org_delete(
    request: Request,
    org: str,
    services: AgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(
            request,
            services,
            status=inspect_setup_status(services.config_store),
        )
    snapshot = services.config_store.load()
    revision = str((await request.form()).get("revision", "")).strip()
    try:
        with revision_bound_group_operation(
            services.config_store,
            group_ids=(org,),
            expected_revision=revision or snapshot.revision,
        ) as locked:
            delete_group(
                services.config_store,
                locked.revision,
                org,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigConflictError:
        current = services.config_store.load()
        return _templates(request).TemplateResponse(
            request,
            "admin_groups.html",
            {
                **_base_admin_context(request, current),
                "orgs": [
                    {
                        "key": key,
                        "name": group.name,
                        "path": str(group.path),
                        "agents": list(group.agents.keys()),
                        "agent_count": len(group.agents),
                        "initialized": (Path(group.path) / "shared").exists(),
                        "path_exists": Path(group.path).exists(),
                        "dispatch_enabled": group.dispatch.enabled,
                    }
                    for key, group in current.config.groups.items()
                ],
                "revision": current.revision,
                "dispatch_error": (
                    "Configuration changed. Reload before deleting."
                ),
            },
            status_code=409,
        )
    request.app.state.refresh_services()
    return RedirectResponse("/admin/groups", status_code=303)
