import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flowgency.web.setup_security import (
    SetupAccessDenied,
    SetupBrowserAccess,
    SetupCompletionError,
    completion_error_response,
    read_completion_command,
    require_completion_peer_and_bearer,
)
from flowgency.web.setup_sessions import SetupSessionConflict


def _app() -> FastAPI:
    app = FastAPI()
    access = SetupBrowserAccess(secret=b"s" * 32)

    @app.get("/token")
    async def token(request: Request):
        try:
            credential, csrf, issued = access.ensure_browser(request)
        except SetupAccessDenied:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        response = JSONResponse({"csrf": csrf})
        if issued:
            access.set_cookie(response, credential, request)
        return response

    @app.get("/owner")
    async def owner(request: Request):
        return JSONResponse({"owner": access.is_owner(request, app.state.owner)})

    @app.post("/control")
    async def control(request: Request):
        form = await request.form()
        try:
            credential = access.require_http(request, str(form.get("csrf", "")), unsafe=True)
        except SetupAccessDenied:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse({"credential": credential})

    @app.websocket("/control/ws")
    async def control_ws(websocket: WebSocket):
        try:
            access.require_ws(websocket)
        except SetupAccessDenied:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_text("connected")

    return app


def test_setup_token_is_bound_to_local_browser_and_csrf():
    with TestClient(_app(), base_url="http://127.0.0.1:8500", client=("127.0.0.1", 10001)) as client:
        csrf = client.get("/token").json()["csrf"]
        assert client.post("/control", data={"csrf": csrf}, headers={"Origin": "http://127.0.0.1:8500"}).status_code == 200
        assert client.post("/control", data={"csrf": "wrong"}, headers={"Origin": "http://127.0.0.1:8500"}).status_code == 403
        assert client.post("/control", data={"csrf": csrf}, headers={"Origin": "http://evil.test"}).status_code == 403
        assert client.post("/control", data={"csrf": csrf}).status_code == 403
        # starlette's TestClient.websocket_connect resolves relative paths against a
        # hardcoded "ws://testserver" authority, ignoring base_url; use an absolute
        # URL so the Host header and cookie jar both target 127.0.0.1 as intended.
        with client.websocket_connect("ws://127.0.0.1:8500/control/ws", headers={"Origin": "http://127.0.0.1:8500"}) as ws:
            assert ws.receive_text() == "connected"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://127.0.0.1:8500/control/ws", headers={"Origin": "http://evil.test"}) as ws:
                ws.receive_text()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://127.0.0.1:8500/control/ws") as ws:
                ws.receive_text()


def test_setup_control_rejects_remote_peer_and_rebound_host():
    app = _app()
    with TestClient(app, base_url="http://127.0.0.1:8500", client=("192.0.2.9", 10002)) as remote:
        assert remote.get("/token").status_code == 403
    with TestClient(app, base_url="http://evil.test:8500", client=("127.0.0.1", 10003)) as rebound:
        assert rebound.get("/token").status_code == 403


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8500/evil",
        "127.0.0.1:8500?evil",
        "127.0.0.1:8500#evil",
    ],
)
def test_setup_control_rejects_host_with_path_query_or_fragment(host):
    app = _app()
    with TestClient(app, client=("127.0.0.1", 10006)) as client:
        assert client.get("/token", headers={"Host": host}).status_code == 403


def test_second_browser_is_not_session_owner():
    app = _app()
    with TestClient(app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 10004)) as first:
        first.get("/token")
        app.state.owner = first.cookies.get("flowgency_setup")
        with TestClient(app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 10005)) as second:
            second.get("/token")
            assert first.get("/owner").json() == {"owner": True}
            assert second.get("/owner").json() == {"owner": False}


_CAPABILITY = "good-capability-7c1d"
_PREPARED_ORIGIN = "http://127.0.0.1:8500"


class _CompletionManager:
    def completion_context(self, token: str):
        if token != _CAPABILITY:
            raise SetupSessionConflict("Setup completion was rejected.")
        return _PREPARED_ORIGIN, Path("config.yaml")


def _completion_app(max_bytes: int = 512) -> FastAPI:
    app = FastAPI()
    manager = _CompletionManager()

    @app.post("/completion")
    async def completion(request: Request):
        try:
            token = require_completion_peer_and_bearer(request, manager)
            command = await read_completion_command(request, max_bytes=max_bytes)
        except SetupCompletionError as error:
            return completion_error_response(error)
        return JSONResponse({"token_ok": token == _CAPABILITY, "launch_id": command.launch_id})

    return app


def _completion_body(**overrides) -> dict:
    body = {
        "launch_id": "a" * 32,
        "revision": "b" * 64,
        "scheduler_result": "manual-only",
        "all_questions_answered": True,
        "summary_delivered": True,
    }
    body.update(overrides)
    return body


def _completion_client(peer: str = "127.0.0.1", base_url: str = _PREPARED_ORIGIN) -> TestClient:
    return TestClient(_completion_app(), base_url=base_url, client=(peer, 10010))


def _bearer(token: str = _CAPABILITY) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_completion_accepts_bearer_from_prepared_loopback_authority():
    with _completion_client() as client:
        response = client.post("/completion", json=_completion_body(), headers=_bearer())

    assert response.status_code == 200
    assert response.json() == {"token_ok": True, "launch_id": "a" * 32}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": f"Basic {_CAPABILITY}"},
        {"Authorization": "Bearer wrong-capability-sentinel"},
    ],
)
def test_completion_rejects_missing_or_invalid_bearer_without_echo(headers):
    with _completion_client() as client:
        response = client.post("/completion", json=_completion_body(), headers=headers)

    assert response.status_code == 401
    assert "wrong-capability-sentinel" not in response.text
    assert _CAPABILITY not in response.text
    assert response.json()["ok"] is False


def test_completion_authenticates_before_reading_the_body():
    with _completion_client() as client:
        response = client.post(
            "/completion", content=b"x" * 100_000, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("peer", "base_url", "extra"),
    [
        ("192.0.2.9", _PREPARED_ORIGIN, {}),
        ("127.0.0.1", "http://localhost:8500", {}),
        ("127.0.0.1", "http://127.0.0.1:9999", {}),
        ("127.0.0.1", "http://evil.test:8500", {}),
        ("127.0.0.1", _PREPARED_ORIGIN, {"Origin": "http://evil.example"}),
        ("127.0.0.1", _PREPARED_ORIGIN, {"Origin": "null"}),
    ],
)
def test_completion_denies_foreign_peer_authority_or_origin(peer, base_url, extra):
    with _completion_client(peer, base_url) as client:
        response = client.post(
            "/completion", json=_completion_body(), headers={**_bearer(), **extra}
        )

    assert response.status_code == 403
    assert _CAPABILITY not in response.text


def test_completion_denies_remote_peer_even_without_a_bearer():
    with _completion_client("192.0.2.9") as client:
        assert client.post("/completion", json=_completion_body()).status_code == 403


def test_completion_allows_the_prepared_origin_header():
    with _completion_client() as client:
        response = client.post(
            "/completion",
            json=_completion_body(),
            headers={**_bearer(), "Origin": _PREPARED_ORIGIN},
        )

    assert response.status_code == 200


def test_completion_rejects_declared_oversized_body():
    with _completion_client() as client:
        response = client.post(
            "/completion",
            content=json.dumps(_completion_body(launch_id="a" * 32, revision="b" * 64)) + " " * 600,
            headers={**_bearer(), "Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["code"] == "payload-too-large"


def test_completion_rejects_chunked_oversized_body():
    def chunks():
        for _ in range(10):
            yield b" " * 100

    with _completion_client() as client:
        response = client.post(
            "/completion",
            content=chunks(),
            headers={**_bearer(), "Content-Type": "application/json"},
        )

    assert response.status_code == 413


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"[]",
        json.dumps({**_completion_body(), "unexpected": "leaked-field-sentinel"}).encode(),
        json.dumps(_completion_body(all_questions_answered="true")).encode(),
        json.dumps(_completion_body(launch_id="leaked-launch-sentinel")).encode(),
        json.dumps(_completion_body(scheduler_result="failed")).encode(),
    ],
)
def test_completion_schema_rejection_never_echoes_input(payload):
    with _completion_client() as client:
        response = client.post(
            "/completion", content=payload, headers={**_bearer(), "Content-Type": "application/json"}
        )

    assert response.status_code == 422
    assert "sentinel" not in response.text
    assert "Input" not in response.text
    assert set(response.json()) == {"ok", "code", "error"}


def test_completion_requires_json_content_type():
    with _completion_client() as client:
        response = client.post(
            "/completion",
            content=json.dumps(_completion_body()),
            headers={**_bearer(), "Content-Type": "text/plain"},
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("scheme", "host", "server", "expected"),
    [
        ("http", "127.0.0.1:8500", ("127.0.0.1", 8500), "http://127.0.0.1:8500"),
        ("http", "localhost:8500", ("127.0.0.1", 8500), "http://127.0.0.1:8500"),
        ("http", "localhost:8500", ("::1", 8500), "http://[::1]:8500"),
        ("http", "[::1]:8500", ("::1", 8500), "http://[::1]:8500"),
        ("https", "localhost", ("127.0.0.1", 443), "https://127.0.0.1"),
    ],
)
def test_completion_origin_is_a_literal_loopback_address(scheme, host, server, expected):
    from flowgency.web.setup_security import completion_origin

    request = Request(
        {
            "type": "http",
            "scheme": scheme,
            "method": "POST",
            "path": "/setup/launch",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
            "server": server,
            "client": ("127.0.0.1", 1),
        }
    )

    assert completion_origin(request) == expected
