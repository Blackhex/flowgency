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
from flowgency.tickets.broker import TicketBroker


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
    assert status in {403, 413}


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
