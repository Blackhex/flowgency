"""Rejected completion callbacks must reach the client as their fixed response.

A rejection that leaves request bytes unread can close the connection with a
TCP reset, which the client reports as an unreachable server instead of the
refusal that was sent.
"""

from __future__ import annotations

import collections
import http.client
import json
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from flowgency import app as app_mod
from flowgency.web.setup_completion import (
    SetupCompletionClientError,
    SetupCompletionCommand,
    submit_completion,
)
from flowgency.web.setup_sessions import SetupSessionConflict

LAUNCH_ID = "a" * 32
CAPABILITY = "live-capability-for-rejection-tests"
_PATH = "/setup/session/completion"
_BODY = SetupCompletionCommand(
    launch_id=LAUNCH_ID,
    revision="b" * 64,
    scheduler_result="declined",
    all_questions_answered=True,
    summary_delivered=True,
).model_dump_json().encode("utf-8")


class _LiveCapabilityManager:
    def __init__(self, origin: str) -> None:
        self.origin = origin

    def completion_context(self, token: str):
        if token != CAPABILITY:
            raise SetupSessionConflict("rejected")
        return self.origin, Path("config.yaml")

    def require_completion_token(self, token: str) -> str:
        return LAUNCH_ID

    async def shutdown(self) -> None:
        return None


class _Live:
    def __init__(self, server: uvicorn.Server, port: int) -> None:
        self.server = server
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"

    def use_live_capability(self, monkeypatch) -> None:
        monkeypatch.setattr(
            app_mod.app.state, "setup_sessions", _LiveCapabilityManager(self.origin)
        )

    def post(self, body: bytes, headers: dict[str, str], token: str = CAPABILITY) -> str:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "POST",
                _PATH,
                body=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                    **headers,
                },
            )
            response = connection.getresponse()
            response.read()
            return str(response.status)
        except Exception as error:
            return type(error).__name__
        finally:
            connection.close()


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(app_mod.app.state, "services", None, raising=False)
    server = uvicorn.Server(
        uvicorn.Config(app_mod.app, host="127.0.0.1", port=0, log_level="critical", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield _Live(server, port)
    server.should_exit = True
    thread.join(timeout=15)


def test_dead_capability_refusal_is_never_lost_to_a_connection_reset(live):
    environment = {
        "FLOWGENCY_SETUP_ORIGIN": live.origin,
        "FLOWGENCY_SETUP_TOKEN": secrets.token_urlsafe(32),
        "FLOWGENCY_SETUP_LAUNCH_ID": LAUNCH_ID,
    }
    command = SetupCompletionCommand.model_validate_json(_BODY)
    outcomes: collections.Counter[str] = collections.Counter()
    for _ in range(800):
        try:
            submit_completion(command, environment)
            outcomes["accepted"] += 1
        except SetupCompletionClientError as error:
            outcomes[error.code] += 1

    assert dict(outcomes) == {"invalid-credentials": 800}


def test_foreign_origin_refusal_is_never_lost_to_a_connection_reset(live, monkeypatch):
    live.use_live_capability(monkeypatch)

    outcomes = collections.Counter(
        live.post(_BODY, {"Origin": "http://evil.example"}) for _ in range(400)
    )

    assert dict(outcomes) == {"403": 400}


def test_oversized_declared_body_refusal_is_never_lost_to_a_connection_reset(live, monkeypatch):
    live.use_live_capability(monkeypatch)
    oversized = _BODY + b" " * 5000

    outcomes = collections.Counter(live.post(oversized, {}) for _ in range(400))

    assert dict(outcomes) == {"413": 400}


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (_BODY.replace(LAUNCH_ID.encode(), b"c" * 32), "409"),
        (b'{"launch_id": 7}', "422"),
    ],
    ids=["stale-launch-id", "schema-invalid"],
)
def test_refusal_after_the_body_was_read_is_not_delayed(live, monkeypatch, body, status):
    live.use_live_capability(monkeypatch)

    timings = []
    for _ in range(3):
        started = time.monotonic()
        assert live.post(body, {}) == status
        timings.append(time.monotonic() - started)

    assert min(timings) < 0.5, timings


def test_unauthenticated_incomplete_body_cannot_hold_the_handler(live):
    with socket.create_connection(("127.0.0.1", live.port), timeout=10) as client:
        client.sendall(
            (
                f"POST {_PATH} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{live.port}\r\n"
                "Authorization: Bearer dead-capability\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n"
                "Content-Length: 4000\r\n\r\n"
            ).encode()
            + b'{"launch_id"'
        )
        started = time.monotonic()
        reply = b""
        while chunk := client.recv(65536):
            reply += chunk
        elapsed = time.monotonic() - started

    assert reply.startswith(b"HTTP/1.1 401")
    assert json.loads(reply.partition(b"\r\n\r\n")[2])["code"] == "invalid-credentials"
    assert elapsed < 3
