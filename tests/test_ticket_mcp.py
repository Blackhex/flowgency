from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from flowgency.tickets.broker import TicketBroker
from flowgency.tickets.models import TicketRef


def test_mcp_stdio_lifecycle_persists_mid_run(workflow_env):
    env = workflow_env
    worktree = Path(__file__).resolve().parents[1]

    async def exercise() -> None:
        authority = env.running_job("builder", "run-a")
        with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "flowgency.tickets.mcp_server"],
                env={
                    "FLOWGENCY_TICKET_ENDPOINT": broker.endpoint.url,
                    "FLOWGENCY_TICKET_TOKEN": broker.endpoint.grant.token,
                },
                cwd=str(worktree),
            )
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = tuple(tool.name for tool in tools.tools)
                    assert names == (
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
                    catalog = {tool.name: tool for tool in tools.tools}
                    _assert_response_schema(catalog["ticket_create"].output_schema)
                    _assert_required_properties(
                        catalog["ticket_create"].input_schema,
                        ("workflow_id", "title", "description", "operation_id"),
                    )
                    _assert_required_properties(
                        catalog["ticket_start_work"].input_schema,
                        ("version", "operation_id"),
                    )
                    _assert_required_properties(
                        catalog["ticket_get"].input_schema,
                        ("ref",),
                    )
                    _assert_required_properties(
                        catalog["ticket_artifact_publish"].input_schema,
                        ("version", "filename", "media_type", "content_b64"),
                    )
                    created = await session.call_tool(
                        "ticket_create",
                        {
                            "workflow_id": "board-a",
                            "title": "MCP ticket",
                            "description": "Body text.",
                            "operation_id": "mcp-create",
                            "field_values": {"summary": "hello", "verdict": True},
                        },
                    )
                    created_payload = created.structured_content
                    assert created_payload["ok"] is True
                    ticket = created_payload["result"]["ticket"]
                    ref = ticket["ref"]
                    assert env.current_provider().read(TicketRef.model_validate(ref)).title == "MCP ticket"
                    fetched = await session.call_tool("ticket_get", {"ref": ref})
                    fetched_payload = fetched.structured_content
                    assert fetched_payload["ok"] is True
                    version = fetched_payload["result"]["version"]
                    started = await session.call_tool(
                        "ticket_start_work",
                        {"version": version, "operation_id": "mcp-start"},
                    )
                    started_payload = started.structured_content
                    assert started_payload["ok"] is True
                    current = env.current_provider().read(
                        env.current_provider()._ref_for(ref["team_id"], ref["workflow_id"], ref["ticket_id"])
                    )
                    assert current.active_run is not None
                    started_version = {
                        "ref": ref,
                        "revision": started_payload["result"]["ticket"]["revision"],
                        "workflow_digest": version["workflow_digest"],
                        "context_digest": version["context_digest"],
                    }
                    transitioned = await session.call_tool(
                        "ticket_transition",
                        {
                            "version": started_version,
                            "operation_id": "mcp-transition",
                            "transition_id": "complete",
                            "outputs": {"summary": "Done"},
                        },
                    )
                    transitioned_payload = transitioned.structured_content
                    assert transitioned_payload["ok"] is True
                    rejected = await session.call_tool(
                        "ticket_transition",
                        {
                            "version": started_version,
                            "operation_id": "mcp-transition-stale",
                            "transition_id": "complete",
                            "outputs": {"summary": "Again"},
                        },
                    )
                    rejected_payload = rejected.structured_content
                    assert rejected_payload["ok"] is False
                    assert rejected_payload["error"]["code"] == "stale-ticket"

    asyncio.run(exercise())


def _assert_required_properties(schema: Mapping[str, object] | None, required: tuple[str, ...]) -> None:
    assert schema is not None
    assert schema.get("type") == "object"
    assert tuple(schema.get("required", ())) == required
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    for name in required:
        assert name in properties


def _assert_response_schema(schema: Mapping[str, object] | None) -> None:
    assert schema is not None
    assert schema.get("type") == "object"
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    assert properties["ok"]["type"] == "boolean"
    assert "result" in properties
    assert "error" in properties