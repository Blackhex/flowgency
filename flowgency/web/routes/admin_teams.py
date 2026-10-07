from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from flowgency.configuration import (
    ConfigConflictError,
    delete_team,
    DirectoryPreparationError,
    TeamCreateStatePatch,
    TeamSettingsStatePatch,
    ValidationFailed,
    create_team_state,
    patch_team_settings_state,
    prepare_writable_directory,
)
from flowgency.integrations import BaseIntegration, IntegrationError, REGISTRY
from flowgency.integrations.models import InteractiveSetupRequest
from flowgency.jobs.connected_process import ConnectedLaunchError, connected_process_available
from flowgency.jobs.store import revision_bound_team_operation
from flowgency.web.dependencies import FlowgencyServices, build_services, get_services
from flowgency.web.admin_live import team_edit_policy, team_new_policy, team_summary, teams_policy
from flowgency.web.directory_browser import DirectoryBrowseError, list_directories
from flowgency.web.live import respond_live_or_html
from flowgency.web.setup_completion import (
    SetupCompletionDecision,
    completion_environment,
    validate_current_completion,
)
from flowgency.web.setup_flow import (
    build_setup_prompt,
    inspect_setup_status,
    launchable_integrations,
    startup_error_status,
)
from flowgency.web.setup_security import SetupAccessDenied, completion_origin
from flowgency.web.setup_sessions import SetupSessionConflict, _SANITIZED_START_FAILURE


router = APIRouter()

_AUTOMATIC_COMPLETION_UNAVAILABLE = (
    "Automatic setup completion is unavailable for this launch; "
    "return to the dashboard yourself once setup has finished."
)
_MANUAL_COMPLETION_UNAVAILABLE = (
    "Automatic setup completion is unavailable when you run this command manually; "
    "return to the dashboard yourself once setup has finished."
)

def _templates(request: Request):
    return request.app.state.templates


def _theme_css(request: Request) -> str:
    return request.app.state.theme_css_getter()


def _workspace_types_json(request: Request) -> str:
    return request.app.state.workspace_types_json_getter()


def _base_admin_context(request: Request, snapshot=None) -> dict:
    teams = {}
    title = "Flowgency"
    saved_revision = ""
    if snapshot is not None:
        teams = {
            key: tcfg.name
            for key, tcfg in snapshot.config.teams.items()
        }
        title = snapshot.config.flowgency.title
        saved_revision = snapshot.revision
    return {
        "request": request,
        "flowgency_title": title,
        "admin_active": True,
        "active": "admin",
        "admin_page": "teams",
        "theme_css": _theme_css(request),
        "teams": teams,
        # What a live region reports as saved; a form's own `revision` is its loaded baseline.
        "saved_revision": saved_revision,
    }


def _team_form_response(request: Request, context: dict, *, status_code: int = 200):
    """Render the team form page; every render registers the canonical GET read, never its POST."""
    if context["mode"] == "create":
        policy = team_new_policy(context)
    else:
        policy = team_edit_policy(context["team_key"], context)
    return respond_live_or_html(request, _templates(request), context, policy, status_code=status_code)


def _diagnostic_issues(services: FlowgencyServices) -> list[dict]:
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
    services: FlowgencyServices,
    *,
    status,
    waiting: bool = False,
    connected: bool = False,
    session_view: bool = False,
    data_root_value: str = "",
    selected_integration: str = "",
    selected_integration_name: str = "",
    integrations: tuple[BaseIntegration, ...] = (),
    fallback_command: str = "",
    launch_notice: str = "",
    error: str = "",
    setup_csrf: str = "",
    status_code: int = 200,
    inspection_view: bool = False,
    completion_launch_id: str | None = None,
):
    return _templates(request).TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "flowgency_title": "Flowgency",
            "error": error,
            "issues": (
                _diagnostic_issues(services) if status.state == "invalid" else []
            ),
            "status_state": status.state,
            "status_message": status.message,
            "waiting": waiting,
            "connected": connected,
            "session_view": session_view,
            "inspection_view": inspection_view,
            "completion_launch_id": completion_launch_id or "",
            "data_root_value": data_root_value,
            "selected_integration": selected_integration,
            "selected_integration_name": selected_integration_name,
            "integrations": integrations,
            "fallback_command": fallback_command,
            "fallback_completion_notice": _MANUAL_COMPLETION_UNAVAILABLE,
            "launch_notice": launch_notice,
            "setup_csrf": setup_csrf,
        },
        status_code=status_code,
    )


def _team_settings_response(
    request: Request,
    snapshot,
    team_id: str,
    *,
    warning: str = "",
    form_values: dict[str, Any] | None = None,
    revision: str | None = None,
    status_code: int = 200,
):
    team_cfg = snapshot.config.teams[team_id]
    runtime = team_cfg.runtime
    permissions = team_cfg.permissions
    dispatch = team_cfg.dispatch
    values = form_values or {}

    def value(key: str, default):
        return values[key] if key in values else default

    context = {
        **_base_admin_context(request, snapshot),
        "mode": "edit",
        "team_key": team_id,
        "team_name": value("name", team_cfg.name),
        "team_workspace_path": value("workspace_path", str(team_cfg.workspace_path)),
        "team_path": value("path", str(team_cfg.path)),
        "team_workspaces_json": value(
            "workspaces_json",
            json.dumps(
                [
                    workspace.model_dump(mode="json")
                    for workspace in team_cfg.workspaces
                ]
            ),
        ),
        "workspace_types_json": _workspace_types_json(request),
        "default_integration": value(
            "default_integration", team_cfg.default_integration
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
        "agent_count": len(team_cfg.agents),
        "manage_agents_href": f"/{team_id}/agents",
        "warning": warning,
        "revision": (
            revision
            if revision is not None
            else value("revision", snapshot.revision)
        ),
    }
    return _team_form_response(request, context, status_code=status_code)


def _team_create_response(
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
    context = {
        **_base_admin_context(request, snapshot),
        "mode": "create",
        "team_key": key,
        "team_name": name,
        "team_workspace_path": workspace_path,
        "team_path": path,
        "default_integration": default_integration,
        "team_workspaces_json": workspaces_json,
        "workspace_types_json": _workspace_types_json(request),
        "warning": warning,
        "integration_names": _integration_names(),
        "revision": revision,
    }
    return _team_form_response(request, context, status_code=status_code)


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


def _canonical_team_path(config_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def _setup_data_root_seed(services: FlowgencyServices, data_root_value: str) -> Path:
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
    services: FlowgencyServices,
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


def _rebuild_services(request: Request, services: FlowgencyServices) -> FlowgencyServices:
    builder = getattr(request.app.state, "build_services", build_services)
    refreshed = builder(services.config_path)
    request.app.state.services = refreshed
    return refreshed


def _setup_status_with_fresh_services(
    request: Request,
    services: FlowgencyServices,
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


def _current_setup_readiness(services: FlowgencyServices) -> tuple[str | None, bool]:
    try:
        current = services.config_store.inspect()
    except Exception:
        return None, False
    if not current.exists:
        return None, False
    try:
        return validate_current_completion(services.config_path, current.revision), True
    except Exception:
        return current.revision, False


async def setup_navigation_decision(
    request: Request, services: FlowgencyServices
) -> SetupCompletionDecision | None:
    """Return the owner's completion decision, or None without an owner-bound attempt."""
    manager = getattr(request.app.state, "setup_sessions", None)
    if manager is None:
        return None
    try:
        owner = request.app.state.setup_access.require_http(request)
    except SetupAccessDenied:
        return None
    if manager.completion_decision(owner, None, False).launch_id is None:
        return None
    revision, ready = await asyncio.to_thread(_current_setup_readiness, services)
    return manager.completion_decision(owner, revision, ready)


def navigation_permitted(decision: SetupCompletionDecision | None) -> bool:
    return decision is None or decision.redirect_allowed


def completion_presentation(decision: SetupCompletionDecision) -> dict[str, str | None]:
    return {"launch_id": decision.launch_id, "phase": decision.phase, "message": decision.message}


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
):
    services, status = _setup_status_with_fresh_services(request, services)
    if status.state == "ready" and navigation_permitted(
        await setup_navigation_decision(request, services)
    ):
        return RedirectResponse("/", status_code=303)
    try:
        credential, csrf, _issued = request.app.state.setup_access.ensure_browser(request)
    except SetupAccessDenied:
        credential, csrf = None, ""
    if credential is not None:
        sessions = getattr(request.app.state, "setup_sessions", None)
        snapshot = sessions.snapshot(credential) if sessions is not None else None
        # A confirmed Stop should return the user to the form, not strand them on the session view.
        if snapshot is not None and snapshot.state != "stopped":
            response = RedirectResponse("/setup/session", status_code=303)
            request.app.state.setup_access.set_cookie(response, credential, request)
            return response
    integrations = _setup_integrations(services, "")
    selected_integration, selected_integration_name = _select_integration(
        integrations,
        "",
    )
    response = _setup_response(
        request,
        services,
        status=status,
        integrations=integrations,
        selected_integration=selected_integration,
        selected_integration_name=selected_integration_name,
        setup_csrf=csrf,
    )
    if credential is not None:
        request.app.state.setup_access.set_cookie(response, credential, request)
    return response


async def _external_setup_launch(
    request: Request,
    services: FlowgencyServices,
    status,
    integrations: tuple[BaseIntegration, ...],
    requested_integration: str,
    selected_integration_name: str,
    integration: BaseIntegration,
    setup_request: InteractiveSetupRequest,
    resolved_data_root: Path,
    *,
    owner: str,
    launch_notice: str = "",
    setup_csrf: str = "",
):
    fallback_command = ""
    try:
        result = await request.app.state.setup_sessions._launch_external(
            owner,
            integration.name,
            resolved_data_root,
            lambda: integration.launch_interactive_setup(setup_request),
        )
        if result.fallback_command:
            fallback_command = result.fallback_command
        else:
            fallback_command = integration.interactive_setup_fallback_command(
                setup_request
            )
        if not getattr(result, "completion_environment_delivered", False):
            launch_notice = f"{launch_notice} {_AUTOMATIC_COMPLETION_UNAVAILABLE}".strip()
    except Exception as launch_error:
        if isinstance(launch_error, SetupSessionConflict) or (
            isinstance(launch_error, ConnectedLaunchError) and not launch_error.cleanup_confirmed
        ):
            message = str(launch_error) if isinstance(launch_error, SetupSessionConflict) else _SANITIZED_START_FAILURE
            return JSONResponse(
                {"error": message, "session": "/setup/session"}, status_code=409,
            )
        if not launch_notice:
            launch_notice = str(launch_error).strip() or "Interactive setup could not be launched."
        launch_notice = f"{launch_notice} {_AUTOMATIC_COMPLETION_UNAVAILABLE}"
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
                selected_integration_name=selected_integration_name,
                error=launch_notice,
                setup_csrf=setup_csrf,
            )
    decision = await setup_navigation_decision(request, services)
    return _setup_response(
        request,
        services,
        status=status,
        waiting=True,
        data_root_value=str(resolved_data_root),
        integrations=integrations,
        selected_integration=requested_integration,
        selected_integration_name=selected_integration_name,
        fallback_command=fallback_command,
        launch_notice=launch_notice,
        setup_csrf=setup_csrf,
        completion_launch_id=decision.launch_id if decision is not None else None,
    )


@router.post("/setup/launch", response_class=HTMLResponse)
async def setup_launch(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
):
    form = await request.form()
    csrf_token = str(form.get("setup_csrf", ""))
    try:
        owner = request.app.state.setup_access.require_http(
            request, csrf_token, unsafe=True,
        )
    except SetupAccessDenied:
        return JSONResponse({"error": "Local setup access required."}, status_code=403)

    status = inspect_setup_status(services.config_store)
    if status.state == "ready" and navigation_permitted(
        await setup_navigation_decision(request, services)
    ):
        return RedirectResponse("/", status_code=303)
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
            setup_csrf=csrf_token,
        )
    try:
        resolved_data_root = prepare_writable_directory(
            Path(data_root_value),
            label="Flowgency data root",
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
            setup_csrf=csrf_token,
        )
    integration = launchable_by_name[requested_integration]
    config_path = services.config_path.resolve()
    origin = completion_origin(request)
    try:
        completion = await request.app.state.setup_sessions.prepare_completion(
            owner, integration.name, resolved_data_root, config_path, origin
        )
    except SetupSessionConflict as exc:
        return JSONResponse(
            {"error": str(exc), "session": "/setup/session"}, status_code=409,
        )
    setup_request = InteractiveSetupRequest(
        data_root=resolved_data_root,
        config_path=config_path,
        prompt=build_setup_prompt(
            resolved_data_root,
            services.config_path,
            selected_integration=requested_integration,
        ),
        environment=completion_environment(completion),
    )
    connected = (
        requested_integration == "copilot"
        and getattr(integration, "connected_setup_available", lambda: False)()
        and connected_process_available()
    )
    if connected:
        try:
            fallback_command = integration.interactive_setup_fallback_command(setup_request)
            launch = integration.connected_setup_launch(setup_request)
        except IntegrationError as exc:
            return _setup_response(
                request,
                services,
                status=status,
                data_root_value=data_root_value,
                integrations=integrations,
                selected_integration=selected_integration,
                selected_integration_name=selected_integration_name,
                error=str(exc),
                setup_csrf=csrf_token,
            )
        try:
            await request.app.state.setup_sessions.start(
                owner, integration.name, launch, fallback_command
            )
        except SetupSessionConflict as exc:
            return JSONResponse(
                {"error": str(exc), "session": "/setup/session"},
                status_code=409,
            )
        except ConnectedLaunchError as exc:
            if not exc.cleanup_confirmed:
                return JSONResponse({"error": _SANITIZED_START_FAILURE}, status_code=409)
            # A confirmed-clean failure frees the slot; fall back to the
            # existing external launcher rather than leaving the user stuck.
            # The failed attempt is cancelled, so the fallback gets a fresh one.
            try:
                completion = await request.app.state.setup_sessions.prepare_completion(
                    owner, integration.name, resolved_data_root, config_path, origin
                )
            except SetupSessionConflict as conflict:
                return JSONResponse(
                    {"error": str(conflict), "session": "/setup/session"}, status_code=409,
                )
            return await _external_setup_launch(
                request,
                services,
                status,
                integrations,
                requested_integration,
                selected_integration_name,
                integration,
                replace(setup_request, environment=completion_environment(completion)),
                resolved_data_root,
                owner=owner,
                launch_notice=str(exc),
                setup_csrf=csrf_token,
            )
        except Exception:
            # No evidence the spawn attempt was cleaned up; never fall back
            # to an external launch that could race a still-blocked slot.
            return JSONResponse(
                {"error": _SANITIZED_START_FAILURE},
                status_code=409,
            )
        return RedirectResponse("/setup/session", status_code=303)

    return await _external_setup_launch(
        request,
        services,
        status,
        integrations,
        requested_integration,
        selected_integration_name,
        integration,
        setup_request,
        resolved_data_root,
        owner=owner,
        setup_csrf=csrf_token,
    )


@router.post("/setup/browse")
async def setup_browse(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
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
    services: FlowgencyServices = Depends(get_services),
) -> JSONResponse:
    services, status = _setup_status_with_fresh_services(request, services)
    payload: dict[str, Any] = {"state": status.state}
    decision = await setup_navigation_decision(request, services)
    if decision is not None:
        payload["completion"] = completion_presentation(decision)
    if status.state == "ready":
        if navigation_permitted(decision):
            payload["redirect"] = "/"
    elif status.message:
        payload["message"] = status.message
    return JSONResponse(payload)


@router.get("/admin/teams/{team}/edit", response_class=HTMLResponse)
async def admin_team_edit(
    request: Request,
    team: str,
    services: FlowgencyServices = Depends(get_services),
):
    if services.startup_error is not None:
        return _setup_response(
            request,
            services,
            status=inspect_setup_status(services.config_store),
        )
    snapshot = services.config_store.load()
    if team not in snapshot.config.teams:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team}")
    return _team_settings_response(request, snapshot, team)


@router.post("/admin/teams/{team}/save", response_class=HTMLResponse)
async def admin_team_save(
    request: Request,
    team: str,
    services: FlowgencyServices = Depends(get_services),
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
        return _team_settings_response(
            request,
            snapshot,
            team,
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
        return _team_settings_response(
            request,
            snapshot,
            team,
            warning="Workspaces payload is invalid.",
            status_code=409,
        )

    try:
        with revision_bound_team_operation(
            services.config_store,
            team_ids=(team,),
            proposed_paths=(
                _canonical_team_path(services.config_path, path),
            ),
            expected_revision=revision,
        ) as locked:
            patch_team_settings_state(
                services.config_store,
                locked.revision,
                team,
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
        return _team_settings_response(
            request,
            snapshot,
            team,
            warning="Configuration changed. Reload before saving.",
            status_code=409,
        )
    except ValidationFailed as exc:
        snapshot = services.config_store.load()
        return _team_settings_response(
            request,
            snapshot,
            team,
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
    return RedirectResponse(f"/admin/teams/{team}/edit", status_code=303)


@router.post("/admin/teams/create", response_class=HTMLResponse)
async def admin_team_create(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
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
        return _team_create_response(
            request,
            snapshot,
            key=key,
            name=name,
            workspace_path=workspace_path,
            path=path,
            default_integration=str(form.get("default_integration", "")).strip(),
            workspaces_json=str(form.get("workspaces_json", "[]")),
            warning="Key, name, workspace path, and path are required.",
            revision=snapshot.revision,
            status_code=200,
        )
    snapshot = services.config_store.load()
    default_integration = str(form.get("default_integration", "")).strip()
    if default_integration and default_integration not in REGISTRY:
        return _team_create_response(
            request,
            snapshot,
            key=key,
            name=name,
            workspace_path=workspace_path,
            path=path,
            default_integration=default_integration,
            workspaces_json=str(form.get("workspaces_json", "[]")),
            warning=f"Integration '{default_integration}' is not registered.",
            revision=snapshot.revision,
            status_code=409,
        )
    workspaces_json = str(form.get("workspaces_json", "[]"))
    try:
        permission_rules = _parse_permission_rules(form)
    except PermissionRulesInvalid as exc:
        return _team_create_response(
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
    # A new team has no stored state to leave unchanged, so an absent field
    # falls back to the documented default rather than the patch sentinel.
    permission_mode = _parse_permission_mode(form) or "unrestricted"
    try:
        workspaces = json.loads(workspaces_json)
        if not isinstance(workspaces, list):
            raise TypeError
    except (json.JSONDecodeError, TypeError):
        return _team_create_response(
            request,
            snapshot,
            key=key,
            name=name,
            workspace_path=workspace_path,
            path=path,
            default_integration=default_integration,
            workspaces_json=workspaces_json,
            warning="Workspaces payload is invalid.",
            revision=snapshot.revision,
            status_code=409,
        )
    try:
        with revision_bound_team_operation(
            services.config_store,
            proposed_paths=(
                _canonical_team_path(services.config_path, path),
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
        return _team_create_response(
            request,
            current,
            key=key,
            name=name,
            workspace_path=workspace_path,
            path=path,
            default_integration=default_integration,
            workspaces_json=workspaces_json,
            warning="Configuration changed. Reload before saving.",
            revision=current.revision,
            status_code=409,
        )
    except ValidationFailed as exc:
        current = services.config_store.load()
        return _team_create_response(
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
    return RedirectResponse("/admin/teams", status_code=303)


@router.post("/admin/teams/{team}/delete", response_class=HTMLResponse)
async def admin_team_delete(
    request: Request,
    team: str,
    services: FlowgencyServices = Depends(get_services),
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
        with revision_bound_team_operation(
            services.config_store,
            team_ids=(team,),
            expected_revision=revision or snapshot.revision,
        ) as locked:
            delete_team(
                services.config_store,
                locked.revision,
                team,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigConflictError:
        current = services.config_store.load()
        context = {
            **_base_admin_context(request, current),
            "team_summaries": [
                team_summary(key, tcfg)
                for key, tcfg in current.config.teams.items()
            ],
            "revision": current.revision,
            "dispatch_error": "Configuration changed. Reload before deleting.",
        }
        return respond_live_or_html(
            request, _templates(request), context, teams_policy(context), status_code=409
        )
    request.app.state.refresh_services()
    return RedirectResponse("/admin/teams", status_code=303)
