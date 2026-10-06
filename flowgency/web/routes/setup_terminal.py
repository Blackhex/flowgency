"""Owner-only routes serving the connected setup terminal session."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocketState

from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.routes.admin_teams import _setup_response, _setup_status_with_fresh_services
from flowgency.web.setup_completion import (
    SetupCompletionStale,
    SetupCompletionUnavailable,
    validate_current_completion,
)
from flowgency.web.setup_flow import inspect_setup_status
from flowgency.web.setup_security import (
    SetupAccessDenied,
    SetupCompletionError,
    completion_error_response,
    read_completion_command,
    require_completion_peer_and_bearer,
)
from flowgency.web.setup_sessions import SetupSessionConflict, SetupSessionManager

router = APIRouter()

_OUTPUT_CHUNK_BYTES = 16 * 1024
_COMPLETION_MAX_BYTES = 4096


def _require_setup_owner(request: Request, csrf: str | None = None, *, unsafe: bool = False) -> str:
    try:
        return request.app.state.setup_access.require_http(request, csrf, unsafe=unsafe)
    except SetupAccessDenied as exc:
        raise HTTPException(status_code=403, detail="Local setup access required") from exc


@router.get("/setup/session")
async def setup_session_view(
    request: Request,
    services: FlowgencyServices = Depends(get_services),
):
    owner = _require_setup_owner(request)
    manager = getattr(request.app.state, "setup_sessions", None)
    snapshot = manager.snapshot(owner) if manager is not None else None
    services, status = _setup_status_with_fresh_services(request, services)
    if snapshot is None:
        return RedirectResponse("/" if status.state == "ready" else "/setup", status_code=303)
    credential, csrf, _issued = request.app.state.setup_access.ensure_browser(request)
    integration = services.integrations[snapshot.integration_name]
    response = _setup_response(
        request,
        services,
        status=status,
        waiting=True,
        connected=True,
        session_view=True,
        data_root_value=str(snapshot.data_root),
        selected_integration=snapshot.integration_name,
        selected_integration_name=integration.display_name,
        fallback_command=snapshot.fallback_command,
        setup_csrf=csrf,
    )
    request.app.state.setup_access.set_cookie(response, credential, request)
    return response


@router.get("/setup/session/state")
async def setup_session_state(request: Request) -> JSONResponse:
    owner = _require_setup_owner(request)
    manager = getattr(request.app.state, "setup_sessions", None)
    snapshot = manager.snapshot(owner) if manager is not None else None
    if snapshot is None:
        return JSONResponse({"error": "No setup session for this browser"}, status_code=404)
    return JSONResponse(
        {
            "state": snapshot.state,
            "exit_code": snapshot.exit_code,
            "truncated": snapshot.truncated,
            "message": snapshot.message,
        }
    )


@router.post("/setup/session/stop")
async def stop_setup_session(request: Request, services: FlowgencyServices = Depends(get_services)):
    form = await request.form()
    owner = _require_setup_owner(request, str(form.get("setup_csrf", "")), unsafe=True)
    try:
        evidence = await request.app.state.setup_sessions.stop(owner)
    except SetupSessionConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if not evidence.confirmed:
        return JSONResponse({"error": evidence.reason}, status_code=409)
    status = inspect_setup_status(services.config_store)
    return RedirectResponse("/" if status.state == "ready" else "/setup", status_code=303)


@router.post("/setup/session/completion")
async def setup_completion_callback(request: Request) -> JSONResponse:
    manager = getattr(request.app.state, "setup_sessions", None)
    try:
        if manager is None:
            raise SetupCompletionError("invalid-credentials")
        token = require_completion_peer_and_bearer(request, manager)
        launch_id = manager.require_completion_token(token)
        command = await read_completion_command(request, max_bytes=_COMPLETION_MAX_BYTES)
        if command.launch_id != launch_id:
            raise SetupCompletionError("stale")
        decision = await manager.acknowledge_validated(
            token, command, validate_current_completion
        )
    except SetupCompletionError as error:
        return completion_error_response(error)
    except (SetupCompletionStale, SetupSessionConflict):
        return completion_error_response(SetupCompletionError("stale"))
    except SetupCompletionUnavailable as error:
        return completion_error_response(SetupCompletionError(error.code))
    except Exception:
        return completion_error_response(SetupCompletionError("unavailable"))
    return JSONResponse(
        {"ok": True, "completion": asdict(decision)}, headers={"Cache-Control": "no-store"}
    )


async def _receive_controls(
    websocket: WebSocket, manager: SetupSessionManager, owner: str, connection_id: str
) -> None:
    while True:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > 65536:
            await websocket.close(code=1009)
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.close(code=1003)
            return
        if not isinstance(event, dict):
            await websocket.close(code=1003)
            return
        try:
            if event.get("type") == "input" and isinstance(event.get("data"), str):
                await manager.send_input(owner, connection_id, event["data"].encode("utf-8"))
            elif (
                event.get("type") == "resize"
                and type(event.get("rows")) is int
                and type(event.get("cols")) is int
            ):
                await manager.resize(owner, connection_id, event["rows"], event["cols"])
            else:
                await websocket.close(code=1003)
                return
        except (SetupSessionConflict, ValueError):
            await websocket.close(code=1008)
            return


async def _send_output(
    websocket: WebSocket,
    manager: SetupSessionManager,
    owner: str,
    connection_id: str,
    pending: "asyncio.Queue[bytes | None]",
) -> None:
    while True:
        chunk = await pending.get()
        if chunk is None:
            state = manager.snapshot(owner)
            if state is None:
                await websocket.send_json(
                    {
                        "type": "state",
                        "state": "unavailable",
                        "message": "Setup session is no longer available.",
                    }
                )
                return
            await websocket.send_json({"type": "state", "state": state.state, "message": state.message})
            return
        await websocket.send_bytes(chunk)
        await manager.consumed(owner, connection_id, len(chunk))


@router.websocket("/setup/session/ws")
async def setup_session_ws(websocket: WebSocket) -> None:
    try:
        owner = websocket.app.state.setup_access.require_ws(websocket)
    except SetupAccessDenied:
        await websocket.close(code=1008)
        return
    manager: SetupSessionManager | None = getattr(websocket.app.state, "setup_sessions", None)
    await websocket.accept()
    if manager is None:
        await websocket.send_json(
            {"type": "state", "state": "unavailable", "message": "Setup session is no longer available."}
        )
        await websocket.close(code=1008)
        return
    try:
        snapshot, connection_id, pending = await manager.attach(owner)
    except SetupSessionConflict:
        await websocket.send_json(
            {"type": "state", "state": "unavailable", "message": "Setup session is no longer available."}
        )
        await websocket.close(code=1008)
        return
    await websocket.send_json(
        {
            "type": "state",
            "state": snapshot.state,
            "truncated": snapshot.truncated,
            "exit_code": snapshot.exit_code,
        }
    )
    for offset in range(0, len(snapshot.output), _OUTPUT_CHUNK_BYTES):
        await websocket.send_bytes(snapshot.output[offset : offset + _OUTPUT_CHUNK_BYTES])

    async def _run_sender(task_group: anyio.abc.TaskGroup) -> None:
        try:
            await _send_output(websocket, manager, owner, connection_id, pending)
        except WebSocketDisconnect:
            pass
        finally:
            task_group.cancel_scope.cancel()

    async def _run_receiver(task_group: anyio.abc.TaskGroup) -> None:
        try:
            await _receive_controls(websocket, manager, owner, connection_id)
        except WebSocketDisconnect:
            pass
        finally:
            task_group.cancel_scope.cancel()

    try:
        # A plain asyncio task pair races the test client's cancel-scope-based
        # WebSocket teardown; anyio's task group is cancellation-aware and lets
        # either loop end the connection without leaking the other's task.
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(_run_sender, task_group)
            task_group.start_soon(_run_receiver, task_group)
        # A terminal state message (e.g. the session vanished) ends both
        # loops without either one closing the socket; send the close frame
        # here so the ASGI task always finishes instead of hanging forever.
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except WebSocketDisconnect:
                # The peer already disconnected (e.g. a real transport raises
                # on send once the client is gone); nothing left to notify.
                pass
    finally:
        await manager.detach(owner, connection_id)
