from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import json
import socket
import threading
import urllib.error
import urllib.request

import httpx
import pytest

import flowgency.tickets.broker as broker_module
from flowgency.tickets.broker import (
    BROKER_CLIENT_TIMEOUT_SECONDS,
    MAX_BROKER_BODY_BYTES,
    TicketBroker,
    TicketToolClient,
    _build_app,
)
from flowgency.tickets.models import TicketOperation, UserTicketContext
from flowgency.tickets.protocol import InvalidTicketRequest, parse_ticket_command


def _raw_call(
    endpoint: str,
    operation: str,
    payload: dict,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers is not None:
        request_headers.update(headers)
    request = urllib.request.Request(
        f"{endpoint}/operations/{operation}",
        data=body,
        headers=request_headers,
        method="POST",
    )
    opener = urllib.request.build_opener()
    try:
        with opener.open(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


class _BoundaryHookStorage:
    def __init__(self, inner, before_apply):
        self._inner = inner
        self._before_apply = before_apply

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def apply(self, ref, expected_revision, operation, mutate):
        self._before_apply()
        return self._inner.apply(ref, expected_revision, operation, mutate)


def test_broker_rejects_actor_fields(workflow_env):
    env = workflow_env
    authority = env.running_job("observer", "run-observer")

    with env.broker_for(authority) as client:
        result = client.call(
            "list_tickets",
            {
                "workflow_id": "board-a",
                "agent_name": "builder",
                "team_id": "other",
            },
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid-request"


def test_request_digest_is_not_agent_controlled(workflow_env):
    env = workflow_env
    ticket = env.create()

    with env.broker_for(env.running_job("builder", "run-a")) as client:
        result = client.call(
            "start_work",
            {
                "version": ticket.version.model_dump(mode="json"),
                "operation_id": "start-one",
                "request_digest": "forged",
            },
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid-request"


def test_broker_rejects_missing_token(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        status, payload = _raw_call(broker.endpoint.url, "list_workflows", {})

    assert status == 401
    assert payload["error"]["code"] == "missing-token"


def test_broker_rejects_invalid_token(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        status, payload = _raw_call(
            broker.endpoint.url,
            "list_workflows",
            {},
            token=f"{broker.endpoint.grant.token}-tampered",
        )

    assert status == 403
    assert payload["error"]["code"] == "invalid-token"


def test_broker_rejects_invalid_host(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        status, payload = _raw_call(
            broker.endpoint.url,
            "list_workflows",
            {},
            token=broker.endpoint.grant.token,
            headers={"Host": "127.0.0.1:1"},
        )

    assert status == 422
    assert payload["error"]["code"] == "invalid-request"


def test_broker_rejects_bad_origin(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        status, payload = _raw_call(
            broker.endpoint.url,
            "list_workflows",
            {},
            token=broker.endpoint.grant.token,
            headers={"Origin": "https://example.com"},
        )

    assert status == 403
    assert payload["error"]["code"] == "forbidden-origin"


def test_broker_rejects_cross_team_ref(workflow_env):
    env = workflow_env
    other_team = UserTicketContext(team_id="support")
    other_ticket = env.service.create(
        other_team,
        "board-a",
        "Support task",
        "Body text.",
        {"summary": "hello"},
        TicketOperation("support-create", "a" * 64),
    )

    with env.broker_for(env.running_job("builder", "run-a")) as client:
        result = client.call(
            "get_ticket",
            {"ref": other_ticket.ticket.ref.model_dump(mode="json")},
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid-request"


def test_broker_rejects_ended_session(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        client = TicketToolClient(broker.endpoint.url, broker.endpoint.grant.token)
        env.job_store.write(authority, replace(env.job_store.read(authority), status="complete"))
        result = client.call("list_workflows", {})

    assert result["ok"] is False
    assert result["error"]["code"] == "ended-session"


def test_broker_replays_duplicate_operation(workflow_env):
    env = workflow_env
    ticket = env.create()

    with env.broker_for(env.running_job("builder", "run-a")) as client:
        first = client.call(
            "start_work",
            {
                "version": ticket.version.model_dump(mode="json"),
                "operation_id": "same-op",
            },
        )
        second = client.call(
            "start_work",
            {
                "version": ticket.version.model_dump(mode="json"),
                "operation_id": "same-op",
            },
        )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["result"]["replayed"] is True


def test_broker_bypasses_proxy_settings(workflow_env, monkeypatch):
    env = workflow_env
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    with env.broker_for(env.running_job("builder", "run-a")) as client:
        result = client.call("list_workflows", {})

    assert result["ok"] is True


def test_broker_shutdown_makes_client_unavailable(workflow_env):
    env = workflow_env
    broker = TicketBroker(
        env.service,
        env.access_registry,
        authority=env.running_job("builder", "run-a"),
    )
    endpoint = broker.start()
    client = TicketToolClient(endpoint.url, endpoint.grant.token)

    broker.close()
    result = client.call("list_workflows", {})

    assert result["ok"] is False
    assert result["error"]["code"] == "unavailable"


def test_broker_rejects_oversized_body(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")

    with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
        status, payload = _raw_call(
            broker.endpoint.url,
            "list_tickets",
            {"workflow_id": "board-a", "query": "x" * (2 * 1024 * 1024)},
            token=broker.endpoint.grant.token,
        )

    assert status == 422
    assert payload["error"]["code"] == "invalid-request"


def test_broker_rejects_invalid_artifact_bytes(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    builder = env.agent("builder", "direct-run")
    env.service.start_work(
        builder,
        ticket.version,
        env.operation("direct-start", actor_name=builder.agent_name),
    )
    current = env.read(ticket.ref)

    with env.broker_for(env.running_job("builder", "run-a")) as client:
        result = client.call(
            "publish_artifact",
            {
                "version": current.version.model_dump(mode="json"),
                "filename": "evidence.txt",
                "media_type": "text/plain",
                "content_b64": "not-base64$$$",
            },
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid-request"


def test_broker_redacts_unexpected_errors(workflow_env):
    env = workflow_env
    original = env.service.list_workflows

    def broken(actor):
        raise RuntimeError("C:/very/secret/path")

    env.service.list_workflows = broken
    try:
        with env.broker_for(env.running_job("builder", "run-a")) as client:
            result = client.call("list_workflows", {})
    finally:
        env.service.list_workflows = original

    assert result["ok"] is False
    assert result["error"]["code"] == "unavailable"
    assert "secret" not in result["error"]["message"].lower()


def test_parse_ticket_command_redacts_validation_details_and_stays_json_safe(workflow_env):
    sentinel = "Bearer sentinel C:/private/server/path"

    class _NonJsonSafe:
        def __repr__(self) -> str:
            return sentinel

    with pytest.raises(InvalidTicketRequest) as exc_info:
        parse_ticket_command(
            "create_ticket",
            {
                "workflow_id": _NonJsonSafe(),
                "title": "MCP ticket",
                "description": "Body text.",
                "operation_id": "create-redaction",
            },
        )

    payload = exc_info.value.as_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["code"] == "invalid-request"
    assert payload["details"]["issues"] == [
        {"location": ["workflow_id"], "type": "string_type"}
    ]
    assert "sentinel" not in serialized.lower()
    assert "private/server/path" not in serialized.lower()
    assert "mcp ticket" not in serialized.lower()


def test_broker_redacts_validation_details_and_keeps_422(workflow_env):
    sentinel = "Bearer sentinel C:/private/server/path"

    with workflow_env.broker_for(workflow_env.running_job("builder", "run-a")) as client:
        result = client.call(
            "create_ticket",
            {
                "workflow_id": {"token": sentinel},
                "title": "MCP ticket",
                "description": "Body text.",
                "operation_id": "create-redaction",
            },
        )

    serialized = json.dumps(result, sort_keys=True)

    assert result == {
        "ok": False,
        "error": {
            "code": "invalid-request",
            "message": "Ticket request payload is invalid",
            "details": {
                "issues": [{"location": ["workflow_id"], "type": "string_type"}],
            },
        },
    }
    assert "sentinel" not in serialized.lower()
    assert "private/server/path" not in serialized.lower()


def test_parse_ticket_command_redacts_unknown_and_dynamic_location_keys(workflow_env):
    ticket = workflow_env.create()
    sentinel = "PRIVATE-SENTINEL-C:/private/server/path"
    cases = (
        (
            "create_ticket",
            {
                "workflow_id": "board-a",
                "title": "MCP ticket",
                "description": "Body text.",
                "operation_id": "create-redaction-extra",
                sentinel: "leak-me",
            },
            {()},
        ),
        (
            "start_work",
            {
                "version": {
                    **ticket.version.model_dump(mode="json"),
                    "ref": {
                        **ticket.ref.model_dump(mode="json"),
                        sentinel: "leak-me",
                    },
                },
                "operation_id": "start-redaction-extra",
            },
            {("version",)},
        ),
        (
            "update_ticket",
            {
                "version": ticket.version.model_dump(mode="json"),
                "operation_id": "update-redaction-map",
                "field_values": {sentinel: []},
            },
            {("field_values",)},
        ),
    )

    for operation, payload, allowed_locations in cases:
        with pytest.raises(InvalidTicketRequest) as exc_info:
            parse_ticket_command(operation, payload)

        error_payload = exc_info.value.as_dict()
        serialized = json.dumps(error_payload, sort_keys=True)

        assert error_payload["code"] == "invalid-request"
        assert error_payload["details"]["issues"]
        assert {
            tuple(issue["location"])
            for issue in error_payload["details"]["issues"]
        } <= allowed_locations
        assert sentinel not in serialized
        assert sentinel.lower() not in serialized.lower()
        assert "private/server/path" not in serialized.lower()


def test_broker_redacts_unknown_and_dynamic_location_keys_and_keeps_422(workflow_env):
    sentinel = "PRIVATE-SENTINEL-C:/private/server/path"
    ticket = workflow_env.create()
    authority = workflow_env.running_job("builder", "run-a")
    cases = (
        (
            "create_ticket",
            {
                "workflow_id": "board-a",
                "title": "MCP ticket",
                "description": "Body text.",
                "operation_id": "create-redaction-extra",
                sentinel: "leak-me",
            },
            {()},
        ),
        (
            "start_work",
            {
                "version": {
                    **ticket.version.model_dump(mode="json"),
                    "ref": {
                        **ticket.ref.model_dump(mode="json"),
                        sentinel: "leak-me",
                    },
                },
                "operation_id": "start-redaction-extra",
            },
            {("version",)},
        ),
        (
            "update_ticket",
            {
                "version": ticket.version.model_dump(mode="json"),
                "operation_id": "update-redaction-map",
                "field_values": {sentinel: []},
            },
            {("field_values",)},
        ),
    )

    with TicketBroker(workflow_env.service, workflow_env.access_registry, authority=authority) as broker:
        for operation, payload, allowed_locations in cases:
            status, response = _raw_call(
                broker.endpoint.url,
                operation,
                payload,
                token=broker.endpoint.grant.token,
            )
            serialized = json.dumps(response, sort_keys=True)

            assert status == 422
            assert response["error"]["code"] == "invalid-request"
            assert response["error"]["details"]["issues"]
            assert {
                tuple(issue["location"])
                for issue in response["error"]["details"]["issues"]
            } <= allowed_locations
            assert sentinel not in serialized
            assert sentinel.lower() not in serialized.lower()
            assert "private/server/path" not in serialized.lower()


def test_failed_authentication_does_not_create_registry_directory(workflow_env):
    env = workflow_env
    target = env.access_registry._access_path(env.team_id, "missing-job", create=False).parent

    with pytest.raises(Exception):
        env.access_registry.authenticate(f"{env.team_id}:missing-job:nonce:secret")

    assert not target.exists()


def test_broker_rejects_chunked_oversized_body_without_full_consumption(workflow_env):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    grant = env.access_registry.open(authority)
    origin = "http://127.0.0.1:8500"
    app = _build_app(env.service, env.access_registry, origin, threading.Event())
    body = json.dumps(
        {
            "workflow_id": "board-a",
            "query": "x" * MAX_BROKER_BODY_BYTES,
        }
    ).encode("utf-8")
    split = MAX_BROKER_BODY_BYTES - 32
    chunks = (body[:split], body[split : split + 128], body[split + 128 :])
    yielded: list[int] = []

    async def stream_body():
        for index, chunk in enumerate(chunks):
            yielded.append(index)
            yield chunk

    async def check() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=origin) as client:
            response = await client.post(
                "/operations/list_tickets",
                headers={"Authorization": f"Bearer {grant.token}"},
                content=stream_body(),
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid-request"

    asyncio.run(check())

    assert yielded == [0, 1]


def test_blocked_binding_resolution_does_not_freeze_independent_request(workflow_env, monkeypatch):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    ticket = env.create()
    grant = env.access_registry.open(authority)
    origin = "http://127.0.0.1:8500"
    app = _build_app(env.service, env.access_registry, origin, threading.Event())
    entered = threading.Event()
    release = threading.Event()
    original = broker_module.binding_for_command

    def blocked_binding(service, actor, command):
        entered.set()
        assert release.wait(timeout=5)
        return original(service, actor, command)

    monkeypatch.setattr(broker_module, "binding_for_command", blocked_binding)

    async def check() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=origin) as client:
            pending = asyncio.create_task(
                client.post(
                    "/operations/get_ticket",
                    headers={"Authorization": f"Bearer {grant.token}"},
                    json={"ref": ticket.ref.model_dump(mode="json")},
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await asyncio.wait_for(
                    client.post("/operations/list_workflows", json={}),
                    1,
                )
                assert response.status_code == 401
                assert response.json()["error"]["code"] == "missing-token"
                assert not pending.done()
            finally:
                release.set()
                await pending

    asyncio.run(check())


def test_broker_start_failure_revokes_opened_grant(workflow_env, monkeypatch):
    env = workflow_env
    authority = env.running_job("builder", "run-a")
    broker = TicketBroker(env.service, env.access_registry, authority=authority)
    opened = {}
    original_open = env.access_registry.open

    def recording_open(job_authority):
        grant = original_open(job_authority)
        opened["grant"] = grant
        return grant

    def broken_bind(self, address):
        raise OSError("bind failed")

    monkeypatch.setattr(env.access_registry, "open", recording_open)
    monkeypatch.setattr(socket.socket, "bind", broken_bind)

    with pytest.raises(OSError, match="bind failed"):
        broker.start()

    assert opened["grant"].session_id
    with pytest.raises(Exception):
        env.access_registry.authenticate(opened["grant"].token)


def test_ticket_tool_client_uses_timeout_and_redacts_malformed_error(monkeypatch):
    client = TicketToolClient("http://127.0.0.1:8500", "token")
    observed = {}

    def broken_open(request, timeout=None):
        observed["timeout"] = timeout
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "boom",
            hdrs=None,
            fp=io.BytesIO(b"not-json token C:/secret/path"),
        )

    monkeypatch.setattr(client._opener, "open", broken_open)

    result = client.call("list_workflows", {"request_digest": "token", "path": "C:/secret/path"})

    assert observed["timeout"] == BROKER_CLIENT_TIMEOUT_SECONDS
    assert result["ok"] is False
    assert result["error"]["code"] == "unavailable"
    assert "token" not in json.dumps(result)
    assert "secret" not in json.dumps(result).lower()


def test_broker_registers_target_before_start_work_commit(workflow_env):
    env = workflow_env
    ticket = env.create()
    authority = env.running_job("builder", "run-a")
    access_path = env.access_registry._access_path(env.team_id, authority.job_id)
    observed = {"checked": False}
    original_factory = env.service.storage_factory

    def factory(storage):
        def before_apply() -> None:
            payload = json.loads(access_path.read_text(encoding="utf-8"))
            entry = payload["original_targets"][0]
            assert entry["ref"]["ticket_id"] == ticket.ref.ticket_id
            observed["checked"] = True

        return _BoundaryHookStorage(original_factory(storage), before_apply)

    env.service.storage_factory = factory
    try:
        with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
            client = TicketToolClient(broker.endpoint.url, broker.endpoint.grant.token)
            result = client.call(
                "start_work",
                {
                    "version": ticket.version.model_dump(mode="json"),
                    "operation_id": "start-one",
                },
            )
    finally:
        env.service.storage_factory = original_factory

    assert observed["checked"] is True
    assert result["ok"] is True


def test_ticket_tool_client_rejects_non_loopback_endpoint():
    with pytest.raises(ValueError):
        TicketToolClient("http://example.com:8500", "token")


def test_ticket_tool_client_rejects_endpoint_credentials():
    with pytest.raises(ValueError):
        TicketToolClient("http://user:pass@127.0.0.1:8500", "token")