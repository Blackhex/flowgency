"""Local-browser access control for Flowgency's connected setup terminal.

Binds the setup control surface to a single local browser: the peer must be
a loopback address, the request's Host header must name this machine, and
unsafe requests must carry a matching Origin plus a CSRF token derived from
the browser's signed session cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import Request, Response, WebSocket
from starlette.requests import HTTPConnection

COOKIE_NAME = "flowgency_setup"


class SetupAccessDenied(Exception):
    """Raised when a request or WebSocket upgrade fails the local-browser guard."""


def _require_local_origin(connection: HTTPConnection, *, unsafe: bool) -> None:
    client = connection.client
    host = connection.headers.get("host", "")
    try:
        parsed = urlsplit("http://" + host)
        local = client is not None and ip_address(client.host).is_loopback
        allowed_host = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        clean_host = not parsed.path and not parsed.query and not parsed.fragment
    except ValueError:
        raise SetupAccessDenied("Local setup access required") from None
    if (
        not local
        or not allowed_host
        or not valid_port
        or not clean_host
        or parsed.username is not None
    ):
        raise SetupAccessDenied("Local setup access required")
    if unsafe:
        scheme = "https" if connection.url.scheme in {"https", "wss"} else "http"
        if connection.headers.get("origin") != f"{scheme}://{host}":
            raise SetupAccessDenied("Setup origin does not match")


class SetupBrowserAccess:
    """Issues and verifies a signed, loopback-bound credential for setup control.

    The credential lives only in an HMAC-signed cookie; it is never logged or
    echoed back in a URL. The secret is kept in memory for the app's lifetime,
    so a restart rotates it and drops every outstanding session.
    """

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret if secret is not None else secrets.token_bytes(32)

    def _sign(self, token: str) -> str:
        signature = hmac.new(self._secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{token}.{signature}"

    def _verify(self, credential: str | None) -> bool:
        if not credential or "." not in credential:
            return False
        token, _, signature = credential.rpartition(".")
        expected = hmac.new(self._secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def _csrf_for(self, credential: str) -> str:
        return hmac.new(self._secret, b"csrf:" + credential.encode("utf-8"), hashlib.sha256).hexdigest()

    def ensure_browser(self, request: Request) -> tuple[str, str, bool]:
        _require_local_origin(request, unsafe=False)
        existing = request.cookies.get(COOKIE_NAME)
        if existing is not None and self._verify(existing):
            credential, issued = existing, False
        else:
            credential, issued = self._sign(secrets.token_urlsafe(32)), True
        return credential, self._csrf_for(credential), issued

    def set_cookie(self, response: Response, credential: str, request: Request) -> None:
        response.set_cookie(
            COOKIE_NAME,
            credential,
            httponly=True,
            samesite="strict",
            path="/",
            secure=request.url.scheme == "https",
        )

    def require_http(self, request: Request, csrf: str | None = None, *, unsafe: bool = False) -> str:
        _require_local_origin(request, unsafe=unsafe)
        credential = request.cookies.get(COOKIE_NAME)
        if not self._verify(credential):
            raise SetupAccessDenied("Setup cookie is missing or invalid")
        if unsafe and not hmac.compare_digest(csrf or "", self._csrf_for(credential or "")):
            raise SetupAccessDenied("Setup CSRF token does not match")
        return credential or ""

    def require_ws(self, websocket: WebSocket) -> str:
        _require_local_origin(websocket, unsafe=True)
        credential = websocket.cookies.get(COOKIE_NAME)
        if not self._verify(credential):
            raise SetupAccessDenied("Setup cookie is missing or invalid")
        return credential or ""

    def is_owner(self, request: Request, credential: str) -> bool:
        _require_local_origin(request, unsafe=False)
        current = request.cookies.get(COOKIE_NAME)
        if not self._verify(current):
            raise SetupAccessDenied("Setup cookie is missing or invalid")
        return hmac.compare_digest(current or "", credential)
