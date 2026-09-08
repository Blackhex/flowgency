from __future__ import annotations

import ipaddress
import json
import socket
import threading
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


class TicketUnauthorized(TicketStorageError):
    http_status = 401


class TicketRequestForbidden(TicketStorageError):
    http_status = 403


def validate_loopback_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "http":
        raise ValueError("Ticket endpoint must use http")
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
    body = await request.body()
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
            with self._opener.open(request) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                return json.loads(error.read().decode("utf-8"))
            except Exception:
                return {
                    "ok": False,
                    "error": {
                        "code": "unavailable",
                        "message": "Ticket broker request failed",
                        "details": {},
                    },
                }
        except urllib.error.URLError:
            return {
                "ok": False,
                "error": {
                    "code": "unavailable",
                    "message": "Ticket broker is unavailable",
                    "details": {},
                },
            }


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
        try:
            host = request.headers.get("host")
            if host != origin.removeprefix("http://"):
                raise InvalidTicketRequest("invalid-request", "Ticket request host is invalid")
            request_origin = request.headers.get("origin")
            if request_origin is not None and request_origin != origin:
                raise TicketRequestForbidden("forbidden-origin", "Ticket request origin is not allowed")
            actor = registry.authenticate(read_bearer(request))
            payload = await read_bounded_json(request, MAX_BROKER_BODY_BYTES)
            command = parse_ticket_command(operation, payload)
            binding = binding_for_command(service, actor, command)
            version = getattr(command, "version", None)
            if binding is not None and version is not None:
                registry.register_target(actor, binding, version.ref)
            result = await run_in_threadpool(dispatch_ticket_command, service, actor, command)
            return JSONResponse({"ok": True, "result": result})
        except TicketStorageError as error:
            if error.http_status == 500:
                return _redacted_error_response()
            return _error_response(error)
        except Exception:
            return _redacted_error_response()

    return app


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

    def start(self) -> LiveTicketEndpoint:
        grant = self.registry.open(self.authority)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        app = _build_app(self.service, self.registry, origin, self._ready)
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
            server.run(sockets=[listener])

        thread = threading.Thread(target=runner, name="ticket-broker", daemon=True)
        thread.start()
        if not self._ready.wait(timeout=5):
            self.registry.close(grant.session_id)
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            raise RuntimeError("Ticket broker did not become ready")
        self._server = server
        self._thread = thread
        self._socket = listener
        self.endpoint = LiveTicketEndpoint(url=origin, grant=grant)
        return self.endpoint

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