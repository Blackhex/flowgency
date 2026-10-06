from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from flowgency.web import setup_completion
from flowgency.web.setup_completion import (
    SetupCompletionClientError,
    SetupCompletionCommand,
    submit_completion,
)

TOKEN = "client-test-capability-token"
LAUNCH_ID = "a" * 32


def _command(**overrides) -> SetupCompletionCommand:
    values = dict(
        launch_id=LAUNCH_ID,
        revision="b" * 64,
        scheduler_result="declined",
        all_questions_answered=True,
        summary_delivered=True,
    )
    values.update(overrides)
    return SetupCompletionCommand(**values)


class _Server:
    def __init__(self, responder):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": self.rfile.read(length),
                    }
                )
                responder(self)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def serve():
    servers: list[_Server] = []

    def start(responder) -> _Server:
        server = _Server(responder)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def _reply(handler, status: int, body: bytes, headers: dict[str, str] | None = None):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def _environment(origin: str) -> dict[str, str]:
    return {
        "FLOWGENCY_SETUP_ORIGIN": origin,
        "FLOWGENCY_SETUP_TOKEN": TOKEN,
        "FLOWGENCY_SETUP_LAUNCH_ID": LAUNCH_ID,
    }


def test_submit_completion_posts_closed_command_with_bearer_capability(serve):
    payload = {"ok": True, "completion": {"phase": "complete"}}
    server = serve(lambda h: _reply(h, 200, json.dumps(payload).encode()))

    result = submit_completion(_command(), _environment(server.origin))

    assert result == payload
    (request,) = server.requests
    assert request["path"] == "/setup/session/completion"
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["headers"]["Host"] == server.origin.removeprefix("http://")
    assert json.loads(request["body"]) == {
        "launch_id": LAUNCH_ID,
        "revision": "b" * 64,
        "scheduler_result": "declined",
        "all_questions_answered": True,
        "summary_delivered": True,
        "limitations_acknowledged": False,
    }


def test_submit_completion_reports_refusal_by_fixed_code_only(serve):
    body = json.dumps(
        {"ok": False, "code": "stale", "error": "server-controlled text " + TOKEN}
    ).encode()
    server = serve(lambda h: _reply(h, 409, body))

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(server.origin))

    assert caught.value.code == "stale"
    assert "server-controlled" not in str(caught.value)
    assert TOKEN not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize(
    "status, body",
    [
        (500, b"Traceback (most recent call last): secret " + TOKEN.encode()),
        (409, json.dumps({"ok": False, "code": "made-up", "error": "x"}).encode()),
        (200, b"not json"),
        (200, b"[]"),
        (200, json.dumps({"ok": False, "code": "stale", "error": "x"}).encode()),
        (200, json.dumps({"ok": True}).encode()),
    ],
)
def test_submit_completion_rejects_unrecognised_responses_without_echoing_them(
    serve, status, body
):
    server = serve(lambda h: _reply(h, status, body))

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(server.origin))

    assert caught.value.code == "invalid-response"
    assert "Traceback" not in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_submit_completion_times_out(serve, monkeypatch):
    release = threading.Event()
    server = serve(lambda h: release.wait(5))
    monkeypatch.setattr(setup_completion, "COMPLETION_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    try:
        with pytest.raises(SetupCompletionClientError) as caught:
            submit_completion(_command(), _environment(server.origin))
    finally:
        release.set()

    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 3


def test_completion_timeout_is_five_seconds():
    assert setup_completion.COMPLETION_TIMEOUT_SECONDS == 5.0


def test_submit_completion_refuses_redirects_without_following(serve):
    target = serve(lambda h: _reply(h, 200, b'{"ok": true, "completion": {}}'))
    source = serve(
        lambda h: _reply(h, 307, b"", {"Location": target.origin + "/setup/session/completion"})
    )

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(source.origin))

    assert caught.value.code == "redirect"
    assert target.requests == []


def test_submit_completion_bounds_response_size(serve):
    oversized = b'{"ok": true, "completion": {"pad": "' + b"x" * (64 * 1024) + b'"}}'
    server = serve(lambda h: _reply(h, 200, oversized))

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(server.origin))

    assert caught.value.code == "response-too-large"


def test_submit_completion_bounds_unlabelled_response_size(serve):
    def respond(handler):
        handler.send_response(200)
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(b"x" * (64 * 1024))
        handler.close_connection = True

    server = serve(respond)

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(server.origin))

    assert caught.value.code == "response-too-large"


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com:8500",
        "http://localhost:8500",
        "http://10.0.0.5:8500",
        "http://0.0.0.0:8500",
        "http://127.0.0.1.example.com:8500",
        "http://127.0.0.1:8500/setup",
        "http://127.0.0.1:8500/?next=1",
        "http://user:pass@127.0.0.1:8500",
        "http://127.0.0.1:99999",
        "ftp://127.0.0.1:8500",
        "file:///etc/passwd",
        "127.0.0.1:8500",
    ],
)
def test_submit_completion_rejects_non_loopback_or_unpinned_origins(origin, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no connection may be opened")

    monkeypatch.setattr(setup_completion.http.client, "HTTPConnection", forbidden)
    monkeypatch.setattr(setup_completion.http.client, "HTTPSConnection", forbidden)

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(origin))

    assert caught.value.code == "invalid-origin"
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("name", list(_environment("x")))
def test_submit_completion_requires_all_three_variables(serve, name):
    server = serve(lambda h: _reply(h, 200, b'{"ok": true, "completion": {}}'))
    environment = _environment(server.origin)
    environment.pop(name)

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), environment)

    assert caught.value.code == "missing-context"
    assert server.requests == []


def test_submit_completion_rejects_launch_mismatch_and_unsafe_token(serve):
    server = serve(lambda h: _reply(h, 200, b'{"ok": true, "completion": {}}'))

    mismatched = _environment(server.origin) | {"FLOWGENCY_SETUP_LAUNCH_ID": "c" * 32}
    with pytest.raises(SetupCompletionClientError) as first:
        submit_completion(_command(), mismatched)
    unsafe = _environment(server.origin) | {"FLOWGENCY_SETUP_TOKEN": "bad\r\nX-Evil: 1"}
    with pytest.raises(SetupCompletionClientError) as second:
        submit_completion(_command(), unsafe)

    assert first.value.code == second.value.code == "invalid-context"
    assert server.requests == []


def test_submit_completion_reads_only_the_named_variables_and_ignores_proxies(
    serve, monkeypatch
):
    proxy = serve(lambda h: _reply(h, 200, b'{"ok": true, "completion": {"via": "proxy"}}'))
    server = serve(lambda h: _reply(h, 200, b'{"ok": true, "completion": {"via": "direct"}}'))
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, proxy.origin)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("FLOWGENCY_SETUP_TOKEN", "process-environment-must-not-be-used")

    result = submit_completion(_command(), _environment(server.origin))

    assert result["completion"] == {"via": "direct"}
    assert proxy.requests == []
    assert server.requests[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_submit_completion_reports_unreachable_origin_without_detail():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with pytest.raises(SetupCompletionClientError) as caught:
        submit_completion(_command(), _environment(f"http://127.0.0.1:{port}"))

    assert caught.value.code == "unreachable"
    assert TOKEN not in str(caught.value)
