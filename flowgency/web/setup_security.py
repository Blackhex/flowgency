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
from typing import Protocol
from urllib.parse import urlsplit

import anyio
from fastapi import Request, Response, WebSocket
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection

from flowgency.web.setup_completion import SetupCompletionCommand
from flowgency.web.setup_sessions import SetupSessionConflict

COOKIE_NAME = "flowgency_setup"

_COMPLETION_ERRORS = {
    "invalid-credentials": (401, "Setup completion credentials are invalid."),
    "forbidden": (403, "Setup completion is only available to the local setup process."),
    "payload-too-large": (413, "Setup completion payload is too large."),
    "invalid-completion": (422, "Setup completion payload was rejected."),
    "stale": (409, "The setup launch or configuration changed; report completion again."),
    "not-ready": (503, "Setup configuration is not ready to be completed."),
    "unavailable": (503, "Setup completion could not be verified right now."),
}


class SetupAccessDenied(Exception):
    """Raised when a request or WebSocket upgrade fails the local-browser guard."""


class SetupCompletionError(Exception):
    """A completion rejection carrying only a fixed, secret-free code and message."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.status_code, self.message = _COMPLETION_ERRORS[code]
        super().__init__(self.message)


def completion_error_response(error: SetupCompletionError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if error.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"ok": False, "code": error.code, "error": error.message},
        status_code=error.status_code,
        headers=headers,
    )


class _CompletionContext(Protocol):
    def completion_context(self, token: str) -> tuple[str, object]: ...


def completion_origin(request: Request) -> str:
    """The literal loopback origin the setup process must call back to."""
    scheme = "https" if request.url.scheme == "https" else "http"
    parsed = urlsplit(f"{scheme}://{request.headers.get('host', '')}")
    host = parsed.hostname or ""
    if host == "localhost":
        server = request.scope.get("server")
        host = "::1" if server and server[0] == "::1" else "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"


def _bearer_token(request: Request) -> str:
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    credential = credential.strip()
    if scheme.lower() != "bearer" or not credential or len(credential) > 256:
        raise SetupCompletionError("invalid-credentials")
    return credential


def require_completion_peer_and_bearer(request: Request, manager: _CompletionContext) -> str:
    """Authenticate the local peer and capability before any body is read."""
    client = request.client
    try:
        local = client is not None and ip_address(client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise SetupCompletionError("forbidden")
    token = _bearer_token(request)
    try:
        origin, _config_path = manager.completion_context(token)
    except SetupSessionConflict:
        raise SetupCompletionError("invalid-credentials") from None
    prepared = urlsplit(origin)
    if (
        request.url.scheme != prepared.scheme
        or request.headers.get("host") != prepared.netloc
        or request.headers.get("origin", origin) != origin
    ):
        raise SetupCompletionError("forbidden")
    return token


async def read_completion_command(request: Request, *, max_bytes: int) -> SetupCompletionCommand:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not (declared.isascii() and declared.isdigit()):
            raise SetupCompletionError("invalid-completion")
        if int(declared) > max_bytes:
            raise SetupCompletionError("payload-too-large")
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise SetupCompletionError("invalid-completion")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise SetupCompletionError("payload-too-large")
    try:
        return SetupCompletionCommand.model_validate_json(bytes(body))
    except ValueError:
        raise SetupCompletionError("invalid-completion") from None


async def discard_unread_body(
    request: Request, *, max_bytes: int, timeout: float = 1.0
) -> None:
    """Throw away the part of a rejected request's body still on the wire, within bounds.

    Closing with unread request bytes can reset the connection and lose the
    rejection the client is waiting for. Call only after the rejection is decided.
    """
    discarded = 0
    try:
        with anyio.move_on_after(timeout):
            while discarded <= max_bytes:
                message = await request.receive()
                if message["type"] != "http.request":
                    return
                discarded += len(message.get("body", b""))
                if not message.get("more_body", False):
                    return
    except Exception:
        return


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
