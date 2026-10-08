from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from flowgency.web.live_cache import LiveSnapshotCacheMiddleware, is_live_snapshot_request


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LiveSnapshotCacheMiddleware)

    @app.get("/plain")
    async def plain() -> Response:
        return JSONResponse({"ok": True}, headers={"Cache-Control": "max-age=60"})

    @app.get("/page")
    async def page(n: int = 0) -> Response:
        return JSONResponse({"n": n}, headers={"Cache-Control": "no-cache", "Vary": "Accept-Encoding"})

    @app.get("/page/not-modified")
    async def not_modified() -> Response:
        return Response(status_code=304, headers={"Cache-Control": "no-cache"})

    @app.get("/page/forbidden")
    async def forbidden() -> Response:
        raise HTTPException(status_code=403, detail="No access")

    @app.get("/page/boom")
    async def boom() -> Response:
        raise RuntimeError("secret detail")

    @app.get("/team/board/snapshot")
    async def board_snapshot() -> Response:
        return JSONResponse({"board": []}, headers={"Cache-Control": "no-cache", "ETag": 'W/"x"'})

    @app.get("/team/board/missing/snapshot")
    async def missing_snapshot() -> Response:
        raise HTTPException(status_code=404, detail="Unknown ticket")

    @app.post("/team/board/snapshot")
    async def post_snapshot() -> Response:
        return JSONResponse({"posted": True})

    return app


def test_the_policy_recognises_only_a_get_for_a_live_snapshot():
    def scope(method: str, path: str, query: bytes = b"") -> dict:
        return {"type": "http", "method": method, "path": path, "query_string": query}

    assert is_live_snapshot_request(scope("GET", "/a/b", b"__live=1"))
    assert is_live_snapshot_request(scope("GET", "/a/b/snapshot"))
    assert not is_live_snapshot_request(scope("GET", "/a/b", b"__live=0"))
    assert not is_live_snapshot_request(scope("GET", "/a/b"))
    assert not is_live_snapshot_request(scope("POST", "/a/b/snapshot", b"__live=1"))
    assert not is_live_snapshot_request({"type": "websocket", "path": "/a/snapshot", "query_string": b""})


def test_a_successful_live_reply_is_private_revalidated_and_varies_on_the_cookie():
    response = TestClient(_app()).get("/page", params={"__live": "1"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache"
    assert [token.strip() for token in response.headers["vary"].split(",")] == ["Accept-Encoding", "Cookie"]


def test_a_snapshot_route_reply_is_private_without_the_live_query():
    response = TestClient(_app()).get("/team/board/snapshot")

    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["vary"] == "Cookie"
    assert response.headers["etag"] == 'W/"x"'


def test_a_not_modified_reply_carries_the_private_headers():
    response = TestClient(_app()).get("/page/not-modified", params={"__live": "1"})

    assert response.status_code == 304
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["vary"] == "Cookie"


def test_a_reply_that_already_forbids_storing_keeps_that_policy():
    app = _app()

    @app.get("/team/board/content-free/snapshot")
    async def content_free() -> Response:
        return JSONResponse({"regions": []}, headers={"Cache-Control": "no-store"})

    response = TestClient(app).get("/team/board/content-free/snapshot")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_every_error_reply_is_never_stored():
    client = TestClient(_app(), raise_server_exceptions=False)

    forbidden = client.get("/page/forbidden", params={"__live": "1"})
    missing = client.get("/team/board/missing/snapshot")
    invalid = client.get("/page", params={"__live": "1", "n": "not-a-number"})

    assert [forbidden.status_code, missing.status_code, invalid.status_code] == [403, 404, 422]
    for response in (forbidden, missing, invalid):
        assert response.headers["cache-control"] == "no-store"


def test_an_unhandled_failure_is_never_stored_and_leaks_nothing():
    response = TestClient(_app(), raise_server_exceptions=False).get("/page/boom", params={"__live": "1"})

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert "secret detail" not in response.text


def test_other_requests_keep_their_own_cache_policy():
    client = TestClient(_app())

    assert client.get("/plain").headers["cache-control"] == "max-age=60"
    posted = client.post("/team/board/snapshot")
    assert posted.status_code == 200
    assert "cache-control" not in posted.headers
    assert client.get("/page/forbidden").status_code == 403
    assert "cache-control" not in client.get("/page/forbidden").headers


def test_the_real_app_installs_the_policy_for_workflow_and_ticket_snapshots(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="Cache policy")
    urls = [f"{env.base_path}/snapshot", f"{env.base_path}/tickets/{ticket.ref.ticket_id}/snapshot"]

    for url in urls:
        first = env.client.get(url)
        assert first.status_code == 200, url
        assert first.headers["cache-control"] == "private, no-cache", url
        assert "cookie" in first.headers["vary"].lower(), url
        again = env.client.get(url, headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304, url
        assert again.headers["cache-control"] == "private, no-cache", url
        assert "cookie" in again.headers["vary"].lower(), url


def test_the_real_app_never_stores_unavailable_snapshots(workflow_web_env):
    env = workflow_web_env
    team = env.team_id
    urls = [
        f"{env.base_path}/tickets/missing-ticket/snapshot",
        f"/{team}/workflows/ghost/snapshot",
        f"/{team}/agents/ghost/profile?__live=1",
        "/ghost-team/agents/ghost/profile?__live=1",
        f"/{team}/jobs/ghost-job?__live=1",
    ]

    for url in urls:
        response = env.client.get(url)
        assert response.status_code in {404, 409, 422}, (url, response.status_code)
        assert response.headers["cache-control"] == "no-store", url
