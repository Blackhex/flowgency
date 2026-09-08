from __future__ import annotations

from dataclasses import replace
import json
import urllib.error
import urllib.request

import pytest

from flowgency.tickets.broker import TicketBroker, TicketToolClient
from flowgency.tickets.models import TicketOperation, UserTicketContext


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
            entries = next(iter(payload["original_targets"].values()))
            assert entries[0]["ticket_id"] == ticket.ref.ticket_id
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