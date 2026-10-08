from __future__ import annotations

import logging

from starlette.datastructures import MutableHeaders, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["LiveSnapshotCacheMiddleware", "is_live_snapshot_request"]

log = logging.getLogger("flowgency.web.live_cache")

_PRIVATE = "private, no-cache"
_NO_STORE = "no-store"


def is_live_snapshot_request(scope: Scope) -> bool:
    """True for a GET that polls a live snapshot: the shared `?__live=1` form or a `/snapshot` route."""
    if scope["type"] != "http" or scope["method"] != "GET":
        return False
    if scope["path"].endswith("/snapshot"):
        return True
    return QueryParams(scope.get("query_string", b"")).get("__live") == "1"


class LiveSnapshotCacheMiddleware:
    """One cache policy for every live snapshot transport, whatever produced the reply.

    A successful reply (and a 304) is private to the session cookie and always revalidated; any
    other reply, including one raised before a route's own helper ran, is never stored.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_live_snapshot_request(scope):
            await self.app(scope, receive, send)
            return

        started = False

        async def send_with_policy(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                status = message["status"]
                if 200 <= status < 300 or status == 304:
                    # A route that already forbids storing its reply keeps that stricter policy.
                    if "no-store" in headers.get("cache-control", "").lower():
                        await send(message)
                        return
                    headers["Cache-Control"] = _PRIVATE
                    vary = headers.get("vary")
                    if vary is None:
                        headers["Vary"] = "Cookie"
                    elif "cookie" not in {token.strip().lower() for token in vary.split(",")} and vary.strip() != "*":
                        headers["Vary"] = f"{vary}, Cookie"
                else:
                    headers["Cache-Control"] = _NO_STORE
            await send(message)

        try:
            await self.app(scope, receive, send_with_policy)
        except Exception:
            if started:
                raise
            log.exception("live snapshot request failed")
            response = JSONResponse({"detail": "Internal Server Error"}, status_code=500, headers={"Cache-Control": _NO_STORE})
            await response(scope, receive, send)
