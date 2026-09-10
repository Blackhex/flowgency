from __future__ import annotations

import hmac
import ipaddress
import json
import socket
import threading
from http.client import HTTPResponse
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from flowgency.jobs.authority import JobAuthorityRef
from flowgency.tickets.access import TicketAccessRegistry
from flowgency.tickets.errors import TicketStorageError
from flowgency.tickets.models import LiveTicketEndpoint
from flowgency.tickets.protocol import (
    InvalidTicketRequest,
    binding_for_command,
    dispatch_ticket_command,
    parse_ticket_command,
)
from flowgency.tickets.service import TicketService


MAX_BROKER_BODY_BYTES = 2 * 1024 * 1024
MAX_BROKER_RESPONSE_BYTES = 64 * 1024
BROKER_CLIENT_TIMEOUT_SECONDS = 5.0


class TicketUnauthorized(TicketStorageError):
    http_status = 401


class TicketRequestForbidden(TicketStorageError):
    http_status = 403


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "http":
        raise ValueError("Ticket endpoint must use http")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Ticket endpoint must not include credentials")
    if not parsed.hostname:
        raise ValueError("Ticket endpoint host is required")
    try:
        host = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("Ticket endpoint host must be a literal loopback address") from error
    if not host.is_loopback:
        raise ValueError("Ticket endpoint host must be loopback")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Ticket endpoint must be a bare loopback origin")
    if parsed.port is None:
        raise ValueError("Ticket endpoint port is required")
    return f"http://{parsed.hostname}:{parsed.port}"


def read_bearer(request: Request) -> str:
    header = request.headers.get("authorization")
    if header is None:
        raise TicketUnauthorized("missing-token", "Bearer token is required")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise TicketUnauthorized("invalid-token", "Bearer token is invalid")
    return value


async def read_bounded_json(request: Request, max_bytes: int) -> dict[str, Any]:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > max_bytes:
                raise InvalidTicketRequest(
                    "invalid-request",
                    "Ticket request body exceeds the maximum size",
                )
        except ValueError as error:
            raise InvalidTicketRequest(
                "invalid-request",
                "Ticket request body length is invalid",
            ) from error
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise InvalidTicketRequest("invalid-request", "Ticket request body exceeds the maximum size")
        chunks.append(chunk)
    body = b"".join(chunks)
    if len(body) > max_bytes:
        raise InvalidTicketRequest("invalid-request", "Ticket request body exceeds the maximum size")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidTicketRequest("invalid-request", "Ticket request body must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise InvalidTicketRequest("invalid-request", "Ticket request payload must be an object")
    return payload


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TicketToolClient:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = validate_loopback_endpoint(endpoint)
        self.token = token
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def call(self, operation: str, payload: dict) -> dict:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.endpoint}/operations/{operation}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=BROKER_CLIENT_TIMEOUT_SECONDS) as response:
                return _read_json_response(response)
        except urllib.error.HTTPError as error:
            try:
                return _read_json_response(error)
            except Exception:
                return {
                    "ok": False,
                    "error": {
                        "code": "unavailable",
                        "message": "Ticket broker request failed",
                        "details": {},
                    },
                }
        except (urllib.error.URLError, TimeoutError, OSError):
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "Ticket broker is unavailable",
                    "details": {},
                },
            }


def _read_json_response(response: HTTPResponse | urllib.error.HTTPError) -> dict:
    payload = response.read(MAX_BROKER_RESPONSE_BYTES + 1)
    if len(payload) > MAX_BROKER_RESPONSE_BYTES:
        raise ValueError("ticket broker response exceeded the maximum size")
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("ticket broker response must be a JSON object")
    return decoded


def _error_response(error: TicketStorageError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error.as_dict()}, status_code=error.http_status)


def _redacted_error_response() -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "code": "unavailable",
                "message": "Ticket broker request failed",
                "details": {},
            },
        },
        status_code=503,
    )


def _redacted_error_envelope() -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "unavailable",
            "message": "Ticket broker request failed",
            "details": {},
        },
    }


async def _handle_operation_request(
    service: TicketService,
    registry: TicketAccessRegistry,
    origin: str,
    operation: str,
    request: Request,
) -> JSONResponse:
    try:
        host = request.headers.get("host")
        if host != origin.removeprefix("http://"):
            raise InvalidTicketRequest("invalid-request", "Ticket request host is invalid")
        request_origin = request.headers.get("origin")
        if request_origin is not None and request_origin != origin:
            raise TicketRequestForbidden("forbidden-origin", "Ticket request origin is not allowed")
        token = read_bearer(request)
        payload = await read_bounded_json(request, MAX_BROKER_BODY_BYTES)
        result = await run_in_threadpool(
            _dispatch_authenticated_request,
            service,
            registry,
            token,
            operation,
            payload,
        )
        return JSONResponse({"ok": True, "result": result})
    except TicketStorageError as error:
        if error.http_status == 500:
            return _redacted_error_response()
        return _error_response(error)
    except Exception:
        return _redacted_error_response()


def dispatch_tool_envelope(
    service: TicketService,
    registry: TicketAccessRegistry,
    token: str,
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run a ticket tool in-process and return the ``{ok, result|error}`` envelope.

    Shared by the in-process MCP HTTP tools; keeps a single authenticated
    dispatch path with the raw ``/operations`` endpoint.
    """
    try:
        result = _dispatch_authenticated_request(service, registry, token, operation, payload)
    except TicketStorageError as error:
        if error.http_status == 500:
            return _redacted_error_envelope()
        return {"ok": False, "error": error.as_dict()}
    except Exception:
        return _redacted_error_envelope()
    return {"ok": True, "result": result}


def _build_app(
    service: TicketService,
    registry: TicketAccessRegistry,
    origin: str,
    ready: threading.Event,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ready.set()
        yield

    app = FastAPI(lifespan=lifespan)

    @app.post("/operations/{operation}")
    async def operate(operation: str, request: Request):
        return await _handle_operation_request(service, registry, origin, operation, request)

    return app


class _McpAuthGuard:
    """Pure-ASGI guard for the worker-owned MCP HTTP endpoint.

    Every request to the MCP path — initialize, tools/list, tools/call — must
    carry the bearer token this broker minted, arrive on the exact loopback
    host, and present no foreign ``Origin``. Authentication is re-checked on
    every call so revocation or a fingerprint change blocks later requests.
    """

    def __init__(
        self,
        app,
        *,
        registry: TicketAccessRegistry,
        grant,
        origin: str,
        mcp_path: str,
        ready: threading.Event,
    ) -> None:
        self.app = app
        self.registry = registry
        self.grant = grant
        self.origin = origin
        self.host_authority = origin.removeprefix("http://")
        self.mcp_path = mcp_path
        self.ready = ready

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            async def _send(message):
                if message["type"] == "lifespan.startup.complete":
                    self.ready.set()
                await send(message)

            await self.app(scope, receive, _send)
            return
        if scope["type"] == "http" and scope.get("path", "") == self.mcp_path:
            request = Request(scope, receive)
            try:
                self._verify_host(request)
                self._verify_origin(request)
                token = read_bearer(request)
                self._verify_broker_token(token)
                await run_in_threadpool(self.registry.authenticate, token)
            except TicketStorageError as error:
                response = _error_response(error) if error.http_status != 500 else _redacted_error_response()
                await response(scope, receive, send)
                return
            except Exception:
                await _redacted_error_response()(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _verify_host(self, request: Request) -> None:
        if request.headers.get("host") != self.host_authority:
            raise TicketRequestForbidden("forbidden-host", "Ticket request host is not allowed")

    def _verify_origin(self, request: Request) -> None:
        request_origin = request.headers.get("origin")
        if request_origin is not None and request_origin != self.origin:
            raise TicketRequestForbidden("forbidden-origin", "Ticket request origin is not allowed")

    def _verify_broker_token(self, token: str) -> None:
        if not hmac.compare_digest(token, self.grant.token):
            raise TicketRequestForbidden("forbidden-token", "Ticket token does not match this session")


def build_ticket_mcp_app(
    service: TicketService,
    registry: TicketAccessRegistry,
    grant,
    origin: str,
    ready: threading.Event,
):
    """Worker-owned combined app: authenticated MCP HTTP at ``/mcp`` plus the
    raw ``/operations`` endpoint for trusted internal callers and tests."""
    from mcp.server.transport_security import TransportSecuritySettings

    from flowgency.tickets.mcp_server import build_mcp_server

    def caller(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return dispatch_tool_envelope(service, registry, grant.token, operation, payload)

    server = build_mcp_server(caller)

    @server.custom_route("/operations/{operation}", methods=["POST"])
    async def operations(request: Request):
        return await _handle_operation_request(
            service, registry, origin, request.path_params["operation"], request
        )

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=MAX_BROKER_BODY_BYTES,
        host="127.0.0.1",
        # The loopback host and Origin are enforced by _McpAuthGuard with an
        # exact match, so the SDK's wildcard DNS-rebinding guard is redundant.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return _McpAuthGuard(
        app,
        registry=registry,
        grant=grant,
        origin=origin,
        mcp_path="/mcp",
        ready=ready,
    )


class TicketBroker:
    def __init__(
        self,
        service: TicketService,
        registry: TicketAccessRegistry,
        *,
        authority: JobAuthorityRef,
    ) -> None:
        self.service = service
        self.registry = registry
        self.authority = authority
        self.endpoint = None
        self._server = None
        self._thread = None
        self._socket = None
        self._ready = threading.Event()
        self._start_error = None

    def start(self) -> LiveTicketEndpoint:
        grant = self.registry.open(self.authority)
        listener = None
        server = None
        thread = None
        try:
            self._ready = threading.Event()
            self._start_error = None
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            app = build_ticket_mcp_app(self.service, self.registry, grant, origin, self._ready)
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=listener.getsockname()[1],
                log_level="warning",
                access_log=False,
                proxy_headers=False,
            )
            server = uvicorn.Server(config)

            def runner() -> None:
                try:
                    server.run(sockets=[listener])
                except Exception as exc:
                    self._start_error = exc
                finally:
                    self._ready.set()

            thread = threading.Thread(target=runner, name="ticket-broker", daemon=True)
            thread.start()
            if not self._ready.wait(timeout=5):
                raise RuntimeError("Ticket broker did not become ready")
            if self._start_error is not None:
                raise self._start_error
            self._server = server
            self._thread = thread
            self._socket = listener
            self.endpoint = LiveTicketEndpoint(url=origin, grant=grant)
            return self.endpoint
        except Exception:
            self._cleanup_start_failure(grant.session_id, server, thread, listener)
            raise

    def close(self) -> None:
        if self.endpoint is not None:
            self.registry.close(self.endpoint.grant.session_id)
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Ticket broker did not shut down cleanly")
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self.endpoint = None

    def __enter__(self) -> "TicketBroker":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _cleanup_start_failure(
        self,
        session_id: str,
        server: uvicorn.Server | None,
        thread: threading.Thread | None,
        listener: socket.socket | None,
    ) -> None:
        self.registry.close(session_id)
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass


def _dispatch_authenticated_request(
    service: TicketService,
    registry: TicketAccessRegistry,
    token: str,
    operation: str,
    payload: dict[str, Any],
):
    actor = registry.authenticate(token)
    command = parse_ticket_command(operation, payload)
    binding = binding_for_command(service, actor, command)
    version = getattr(command, "version", None)
    if binding is not None and version is not None:
        registry.register_target(actor, binding, version.ref)
    return dispatch_ticket_command(service, actor, command)