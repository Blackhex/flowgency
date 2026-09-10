from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import replace

import httpx2
import pytest

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from flowgency.integrations.ticket_tools import build_ticket_tool_launch
from flowgency.tickets.broker import MAX_BROKER_BODY_BYTES, TicketBroker


TICKET_TOOL_NAMES = (
    "workflows_list",
    "tickets_list",
    "ticket_get",
    "ticket_create",
    "ticket_start_work",
    "ticket_update",
    "ticket_report",
    "ticket_transition",
    "ticket_end_work",
    "ticket_sign_off",
    "ticket_artifact_publish",
)


@contextmanager
def _http_broker(env, authority):
    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        yield broker


def _mcp_url(broker: TicketBroker) -> str:
    return build_ticket_tool_launch(broker.endpoint).url


def _bearer(broker: TicketBroker) -> str:
    return broker.endpoint.grant.token


async def _open_session(url: str, token: str):
    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    return client, streamable_http_client(url, http_client=client)


def _raw_mcp_post(
    url: str,
    body: dict,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers is not None:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    opener = urllib.request.build_opener()
    try:
        with opener.open(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "flowgency-test", "version": "1.0"},
        },
    }


def test_sdk_http_client_initializes_and_lists_fixed_catalog(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    async def exercise() -> tuple[str, ...]:
        with _http_broker(env, authority) as broker:
            client, transport = await _open_session(_mcp_url(broker), _bearer(broker))
            try:
                async with transport as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        return tuple(tool.name for tool in tools.tools)
            finally:
                await client.aclose()

    names = asyncio.run(exercise())
    assert names == TICKET_TOOL_NAMES


def test_sdk_http_client_creates_ticket_through_shared_dispatch(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    async def exercise() -> dict:
        with _http_broker(env, authority) as broker:
            client, transport = await _open_session(_mcp_url(broker), _bearer(broker))
            try:
                async with transport as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        created = await session.call_tool(
                            "ticket_create",
                            {
                                "workflow_id": "board-a",
                                "title": "HTTP ticket",
                                "description": "Body text.",
                                "operation_id": "http-create",
                                "field_values": {"summary": "hello", "verdict": True},
                            },
                        )
                        return created.structured_content
            finally:
                await client.aclose()

    payload = asyncio.run(exercise())
    assert payload["ok"] is True
    assert payload["result"]["ticket"]["title"] == "HTTP ticket"


def test_http_broker_rejects_missing_token(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    with _http_broker(env, authority) as broker:
        status, _ = _raw_mcp_post(_mcp_url(broker), _initialize_body())
    assert status == 401


def test_http_broker_rejects_cross_job_token(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    other = env.running_job("observer", "run-observer")
    with _http_broker(env, authority) as broker:
        with TicketBroker(env.service, env.access_registry, authority=other) as other_broker:
            foreign_token = other_broker.endpoint.grant.token
            status, _ = _raw_mcp_post(
                _mcp_url(broker),
                _initialize_body(),
                token=foreign_token,
            )
    assert status == 403


def test_http_broker_rejects_wrong_origin(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    with _http_broker(env, authority) as broker:
        status, _ = _raw_mcp_post(
            _mcp_url(broker),
            _initialize_body(),
            token=_bearer(broker),
            headers={"Origin": "http://evil.invalid"},
        )
    assert status == 403


def test_http_broker_rejects_wrong_host(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    with _http_broker(env, authority) as broker:
        status, _ = _raw_mcp_post(
            _mcp_url(broker),
            _initialize_body(),
            token=_bearer(broker),
            headers={"Host": "127.0.0.1:1"},
        )
    assert status == 403


def test_http_broker_rejects_oversized_body(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    body = _initialize_body()
    body["params"]["padding"] = "x" * (2 * 1024 * 1024)
    with _http_broker(env, authority) as broker:
        status, _ = _raw_mcp_post(_mcp_url(broker), body, token=_bearer(broker))
    # Auth, host and origin are all valid here, so the size gate — not an
    # authorization failure — must be what rejects the request.
    assert status == 413


def _oversized_stream_chunks():
    chunk = b"x" * (256 * 1024)
    sent = 0
    limit = MAX_BROKER_BODY_BYTES + 256 * 1024
    while sent <= limit:
        yield chunk
        sent += len(chunk)


def test_http_broker_rejects_streamed_oversized_body(workflow_env):
    # No Content-Length: the body streams in chunks and its declared length is
    # absent, so the 2 MiB maximum must be enforced on the received bytes.
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    with _http_broker(env, authority) as broker:
        url = _mcp_url(broker)
        token = _bearer(broker)
        with httpx2.Client(timeout=10.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                content=_oversized_stream_chunks(),
            )
    assert response.status_code == 413


def test_http_broker_sanitizes_malformed_tool_arguments(workflow_env):
    # SDK Pydantic signature validation runs before our dispatch and, left
    # unsanitized, echoes the raw rejected argument values (a path or token an
    # agent slipped into a field) back through the tool error content.
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    secret_marker = "SECRET-etc-shadow-plus-token-9f8a7b6c"

    async def exercise():
        with _http_broker(env, authority) as broker:
            token = _bearer(broker)
            client, transport = await _open_session(_mcp_url(broker), token)
            try:
                async with transport as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "ticket_create",
                            {
                                "workflow_id": {"path": secret_marker},
                                "title": "t",
                                "description": "d",
                                "operation_id": "op",
                                "evil_extra": secret_marker,
                            },
                        )
                        return result, token
            finally:
                await client.aclose()

    result, token = asyncio.run(exercise())
    assert result.is_error
    text = " ".join(getattr(part, "text", "") or "" for part in result.content)
    assert secret_marker not in text
    assert token not in text
    assert "input_value" not in text
    assert "Traceback" not in text
    assert "validation error" not in text.lower()


def test_http_broker_bounds_oversized_tool_result(workflow_env):
    # The 64 KiB result contract governs the domain payload, not the protocol
    # envelope or the fixed catalog. An oversized result returns a bounded safe
    # error carrying none of the oversized content, and the commit still stands.
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    marker = "A" * 100_000

    async def exercise():
        with _http_broker(env, authority) as broker:
            client, transport = await _open_session(_mcp_url(broker), _bearer(broker))
            try:
                async with transport as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        created = await session.call_tool(
                            "ticket_create",
                            {
                                "workflow_id": "board-a",
                                "title": "Oversized result ticket",
                                "description": marker,
                                "operation_id": "http-oversized",
                                "field_values": {"summary": "hello", "verdict": True},
                            },
                        )
                        return tuple(t.name for t in tools.tools), created
            finally:
                await client.aclose()

    names, created = asyncio.run(exercise())
    assert names == TICKET_TOOL_NAMES
    payload = created.structured_content
    assert payload["ok"] is False
    assert payload["error"]["code"] == "result-too-large"
    assert marker not in json.dumps(payload)
    stored = [
        view
        for view in env.service.list_tickets(env.user, "board-a")
        if view.record.description == marker
    ]
    assert len(stored) == 1


def test_http_broker_rejects_after_job_ends(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    with _http_broker(env, authority) as broker:
        env.job_store.write(
            authority,
            replace(env.job_store.read(authority), status="complete"),
        )
        status, _ = _raw_mcp_post(_mcp_url(broker), _initialize_body(), token=_bearer(broker))
    assert status in {401, 403}
