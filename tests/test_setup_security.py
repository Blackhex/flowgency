import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flowgency.web.setup_security import SetupAccessDenied, SetupBrowserAccess


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
