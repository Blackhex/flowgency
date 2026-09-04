from __future__ import annotations

import json
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from agency.configuration import (
    ConfigConflictError,
    delete_team,
    DirectoryPreparationError,
    TeamCreateStatePatch,
    TeamSettingsStatePatch,
    ValidationFailed,
    create_team_state,
    patch_team_settings_state,
    prepare_writable_directory,
    resolve_team_paths,
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


def _group_summary(key: str, group) -> dict:
    paths = resolve_team_paths(group)
    return {
        "key": key,
        "name": group.name,
        "workspace_path": str(group.workspace_path),
        "group_path": str(group.path),
        "agents": list(group.agents.keys()),
        "agent_count": len(group.agents),
        "initialized": all(path.is_dir() for path in paths.record_directories),
        "workspace_exists": paths.workspace_root.exists(),
        "dispatch_enabled": group.dispatch.enabled,
    }


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


def _validation_warning(error: ValidationFailed) -> str:
    details = "; ".join(
        f"{issue.field}: {issue.message} {issue.corrective_hint}"
        for issue in error.issues
    )
    return f"Configuration is invalid. {details}"


def _setup_response(
    request: Request,
    services: AgencyServices,
    *,
    status,
    waiting: bool = False,
    data_root_value: str = "",
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
            "data_root_value": data_root_value,
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
    form_values: dict[str, Any] | None = None,
    revision: str | None = None,
    status_code: int = 200,
):
    group = snapshot.config.groups[group_id]
    runtime = group.runtime
    permissions = runtime.permissions
    dispatch = group.dispatch
    values = form_values or {}

    def value(key: str, default):
        return values[key] if key in values else default

    return _templates(request).TemplateResponse(
        request,
        "admin_org_edit.html",
        {
            **_base_admin_context(request, snapshot),
            "mode": "edit",
            "org_key": group_id,
            "org_name": value("name", group.name),
            "org_workspace_path": value("workspace_path", str(group.workspace_path)),
            "org_path": value("path", str(group.path)),
            "org_workspaces_json": value(
                "workspaces_json",
                json.dumps(
                    [
                        workspace.model_dump(mode="json")
                        for workspace in group.workspaces
                    ]
                ),
            ),
            "workspace_types_json": _workspace_types_json(request),
            "default_integration": value(
                "default_integration", group.default_integration
            ),
            "runtime_timeout": value("runtime_timeout", runtime.timeout),
            "permission_mode": value("permission_mode", permissions.mode),
            "permission_rules_yaml": value(
                "permission_rules_yaml",
                yaml.safe_dump(
                    [
                        {
                            k: v
                            for k, v in (
                                ("path", str(rule.path) if rule.path else None),
                                ("tools", list(rule.tools) if rule.tools is not None else None),
                            )
                            if v is not None
                        }
                        for rule in permissions.rules
                    ],
                    default_flow_style=False,
                    sort_keys=False,
                ).strip() if permissions.rules else "",
            ),
            "dispatch_enabled": value("dispatch_enabled", dispatch.enabled),
            "agent_count": len(group.agents),
            "manage_agents_href": f"/{group_id}/agents",
            "warning": warning,
            "revision": (
                revision
                if revision is not None
                else value("revision", snapshot.revision)
            ),
        },
        status_code=status_code,
    )


def _group_create_response(
    request: Request,
    snapshot,
    *,
    key: str,
    name: str,
    workspace_path: str,
    path: str,
    default_integration: str,
    workspaces_json: str,
    warning: str,
    revision: str,
    status_code: int,
):
    return _templates(request).TemplateResponse(
        request,
        "admin_org_edit.html",
        {
            **_base_admin_context(request, snapshot),
            "mode": "create",
            "org_key": key,
            "org_name": name,
            "org_workspace_path": workspace_path,
            "org_path": path,
            "default_integration": default_integration,
            "org_workspaces_json": workspaces_json,
            "workspace_types_json": _workspace_types_json(request),
            "warning": warning,
            "integration_names": _integration_names(),
            "revision": revision,
        },
        status_code=status_code,
    )


PERMISSION_RULES_WARNING = "Permission rules must be valid YAML (a list of mappings)."


class PermissionRulesInvalid(ValueError):
    """The permission rules textarea did not hold a YAML list of mappings."""


def _parse_permission_mode(form) -> str | None:
    """The posted mode, or None when the field is absent (leave unchanged)."""
    raw = form.get("permission_mode")
    return None if raw is None else str(raw).strip()


def _parse_permission_rules(form) -> tuple[dict[str, Any], ...] | None:
    """The posted rules, or None when the field is absent (leave unchanged).

    An empty textarea is an explicit clear, not an absent field. Create and
    save share this so the two cannot drift apart again.
    """
    raw = form.get("permission_rules_yaml")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return ()
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PermissionRulesInvalid(PERMISSION_RULES_WARNING) from exc
    if not isinstance(parsed, list):
        raise PermissionRulesInvalid(PERMISSION_RULES_WARNING)
    return tuple(parsed)


def _integration_names() -> list[str]:
    return sorted(REGISTRY)


def _canonical_group_path(config_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def _setup_data_root_seed(services: AgencyServices, data_root_value: str) -> Path:
    candidate = (
        Path(data_root_value).expanduser()
        if data_root_value
        else services.config_path.parent
    )
    try:
        return candidate.resolve()
    except OSError:
        return services.config_path.parent.resolve()


def _setup_integrations(
    services: AgencyServices,
    data_root_value: str,
) -> tuple[BaseIntegration, ...]:
    return tuple(
        launchable_integrations(
            services.integrations,
            _setup_data_root_seed(services, data_root_value),
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
    data_root_value = str(form.get("data_root", "")).strip()
    requested_integration = str(form.get("integration", "")).strip()
    integrations = _setup_integrations(services, data_root_value)
    selected_integration, selected_integration_name = _select_integration(
        integrations,
        requested_integration,
    )
    launchable_by_name = {item.name: item for item in integrations}
    if requested_integration not in launchable_by_name:
        return _setup_response(
            request,
            services,
            status=status,
            data_root_value=data_root_value,
            integrations=integrations,
            selected_integration=selected_integration,
            selected_integration_name=selected_integration_name,
            error="Choose an available integration.",
        )
    try:
        resolved_data_root = prepare_writable_directory(
            Path(data_root_value),
            label="Agency data root",
        )
    except DirectoryPreparationError as exc:
        return _setup_response(
            request,
            services,
            status=status,
            data_root_value=data_root_value,
            integrations=integrations,
            selected_integration=selected_integration,
            selected_integration_name=selected_integration_name,
            error=str(exc),
        )
    integration = launchable_by_name[requested_integration]
    setup_request = InteractiveSetupRequest(
        data_root=resolved_data_root,
        config_path=services.config_path.resolve(),
        prompt=build_setup_prompt(
            resolved_data_root,
            services.config_path,
            selected_integration=requested_integration,
        ),
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
    except Exception as launch_error:
        launch_notice = str(launch_error).strip() or "Interactive setup could not be launched."
        try:
            fallback_command = integration.interactive_setup_fallback_command(
                setup_request
            )
        except Exception:
            return _setup_response(
                request,
                services,
                status=status,
                data_root_value=str(resolved_data_root),
                integrations=integrations,
                selected_integration=requested_integration,
                selected_integration_name=launchable_by_name[
                    requested_integration
                ].display_name,
                error=launch_notice,
            )
    return _setup_response(
        request,
        services,
        status=status,
        waiting=True,
        data_root_value=str(resolved_data_root),
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
    workspace_path = str(form.get("workspace_path", "")).strip()
    path = str(form.get("path", "")).strip()
    default_integration = str(form.get("default_integration", "")).strip()
    runtime_timeout = int(str(form.get("runtime_timeout", "1800")) or "1800")
    permission_mode = _parse_permission_mode(form)
    try:
        permission_rules = _parse_permission_rules(form)
    except PermissionRulesInvalid as exc:
        snapshot = services.config_store.load()
        return _group_settings_response(
            request,
            snapshot,
            org,
            warning=str(exc),
            status_code=409,
        )
    dispatch_enabled = form.get("dispatch_enabled") == "on"
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
            patch_team_settings_state(
                services.config_store,
                locked.revision,
                org,
                TeamSettingsStatePatch(
                    name=name,
                    workspace_path=workspace_path,
                    path=path,
                    default_integration=default_integration,
                    runtime_timeout=runtime_timeout,
                    permission_mode=permission_mode,
                    permission_rules=permission_rules,
                    dispatch_enabled=dispatch_enabled,
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
    except ValidationFailed as exc:
        snapshot = services.config_store.load()
        return _group_settings_response(
            request,
            snapshot,
            org,
            warning=_validation_warning(exc),
            form_values={
                "revision": revision,
                "name": name,
                "workspace_path": workspace_path,
                "path": path,
                "default_integration": default_integration,
                "runtime_timeout": runtime_timeout,
                "permission_mode": permission_mode,
                "dispatch_enabled": dispatch_enabled,
                "workspaces_json": workspaces_json,
            },
            revision=revision,
            status_code=422,
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
    workspace_path = str(form.get("workspace_path", "")).strip()
    path = str(form.get("path", "")).strip()
    if not key or not name or not workspace_path or not path:
        snapshot = services.config_store.load()
        return _templates(request).TemplateResponse(
            request,
            "admin_org_edit.html",
            {
                **_base_admin_context(request, snapshot),
                "mode": "create",
                "org_key": key,
                "org_name": name,
                "org_workspace_path": workspace_path,
                "org_path": path,
                "default_integration": str(
                    form.get("default_integration", "")
                ).strip(),
                "org_workspaces_json": str(form.get("workspaces_json", "[]")),
                "workspace_types_json": _workspace_types_json(request),
                "warning": "Key, name, workspace path, and path are required.",
                "integration_names": _integration_names(),
                "revision": snapshot.revision,
            },
        )
    snapshot = services.config_store.load()
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
                "org_workspace_path": workspace_path,
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
    workspaces_json = str(form.get("workspaces_json", "[]"))
    try:
        permission_rules = _parse_permission_rules(form)
    except PermissionRulesInvalid as exc:
        return _group_create_response(
            request,
            snapshot,
            key=key,
            name=name,
            workspace_path=workspace_path,
            path=path,
            default_integration=default_integration,
            workspaces_json=workspaces_json,
            warning=str(exc),
            revision=snapshot.revision,
            status_code=409,
        )
    # A new group has no stored state to leave unchanged, so an absent field
    # falls back to the documented default rather than the patch sentinel.
    permission_mode = _parse_permission_mode(form) or "unrestricted"
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
                "org_workspace_path": workspace_path,
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
            create_team_state(
                services.config_store,
                locked.revision,
                key,
                TeamCreateStatePatch(
                    name=name,
                    workspace_path=workspace_path,
                    path=path,
                    default_integration=default_integration or "claude-code",
                    runtime_timeout=1800,
                    permission_mode=permission_mode,
                    permission_rules=permission_rules or (),
                    dispatch_enabled=False,
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
                "org_workspace_path": workspace_path,
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
    except ValidationFailed as exc:
        current = services.config_store.load()
        return _group_create_response(
            request,
            current,
            key=key,
            name=name,
            workspace_path=workspace_path,
            path=path,
            default_integration=default_integration,
            workspaces_json=workspaces_json,
            warning=_validation_warning(exc),
            revision=revision,
            status_code=422,
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
            delete_team(
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
                    _group_summary(key, group)
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
