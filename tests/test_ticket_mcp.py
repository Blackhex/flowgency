from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx2

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from flowgency.integrations.ticket_tools import build_ticket_tool_launch
from flowgency.tickets.broker import TicketBroker
from flowgency.tickets.models import TicketRef
from flowgency.workflows.models import ArtifactRef


def test_mcp_http_lifecycle_persists_mid_run(workflow_env):
    env = workflow_env
    env.publish_artifact_field_workflow()
    env.publish_criteria_workflow()

    async def exercise() -> None:
        authority = env.running_job("builder", "run-a")
        with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
            launch = build_ticket_tool_launch(broker.endpoint)
            client = httpx2.AsyncClient(headers=dict(launch.headers))
            async with streamable_http_client(launch.url, http_client=client) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = tuple(tool.name for tool in tools.tools)
                    assert names == (
                        "ticket_capture_git_evidence",
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
                    for name in ("ticket_transition", "ticket_report", "ticket_update", "ticket_capture_git_evidence"):
                        assert catalog[name].description
                        assert catalog[name].description.strip()
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
                    _assert_create_schema(catalog["ticket_create"].input_schema)
                    _assert_versioned_mutation_schema(catalog["ticket_start_work"].input_schema)
                    _assert_versioned_mutation_schema(catalog["ticket_update"].input_schema)
                    _assert_report_schema(catalog["ticket_report"].input_schema)
                    _assert_transition_schema(catalog["ticket_transition"].input_schema)
                    _assert_versioned_mutation_schema(catalog["ticket_end_work"].input_schema)
                    _assert_versioned_mutation_schema(catalog["ticket_sign_off"].input_schema)
                    _assert_artifact_schema(catalog["ticket_artifact_publish"].input_schema)
                    _assert_git_capture_schema(catalog["ticket_capture_git_evidence"].input_schema)
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
                            "inputs": {"verdict": True},
                            "outputs": {
                                "summary": "Done",
                                "evidence": {
                                    "kind": "url",
                                    "value": "https://example.com/evidence",
                                },
                            },
                            "assessments": [
                                {
                                    "criterion_id": "evidence-reviewed",
                                    "satisfied": True,
                                    "reasoning": "Reviewed evidence",
                                    "supporting_fields": ["summary", "evidence"],
                                }
                            ],
                        },
                    )
                    transitioned_payload = transitioned.structured_content
                    assert transitioned_payload["ok"] is True
                    record = env.current_provider().read(
                        env.current_provider()._ref_for(ref["team_id"], ref["workflow_id"], ref["ticket_id"])
                    )
                    assert record.field_values["evidence"] == ArtifactRef(
                        kind="url",
                        value="https://example.com/evidence",
                    )
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


def test_mcp_git_capture_rejects_an_unknown_field_at_runtime(workflow_env):
    """A closed schema must be enforced, not merely published."""
    env = workflow_env
    env.publish_artifact_field_workflow()
    ticket = env.create(values={"verdict": True, "summary": "Pending"})

    async def exercise() -> None:
        authority = env.running_job("builder", "run-schema")
        with TicketBroker(env.service, env.access_registry, authority=authority) as broker:
            launch = build_ticket_tool_launch(broker.endpoint)
            client = httpx2.AsyncClient(headers=dict(launch.headers))
            async with streamable_http_client(launch.url, http_client=client) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    rejected = await session.call_tool(
                        "ticket_capture_git_evidence",
                        {
                            "version": ticket.version.model_dump(mode="json"),
                            "operation_id": "schema-capture",
                            "transition_id": "complete",
                            "field_id": "evidence",
                            "base_commit": "a" * 40,
                            "end_commit": "b" * 40,
                            "workspace_path": "C:/outside",
                        },
                    )
                    assert rejected.is_error is True
                    assert rejected.structured_content is None
                    rendered = "".join(
                        getattr(block, "text", "") for block in rejected.content
                    )
                    assert "C:/outside" not in rendered

    asyncio.run(exercise())
    record = env.current_provider().read(ticket.ref)
    assert record.revision == ticket.version.revision
    assert all(event.kind != "git-evidence-captured" for event in record.events)


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


def _assert_create_schema(schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(schema, schema)
    _assert_object_schema(
        schema,
        resolved,
        ("workflow_id", "title", "description", "operation_id"),
    )
    properties = resolved["properties"]
    _assert_schema_type(schema, properties["workflow_id"], "string")
    _assert_schema_type(schema, properties["title"], "string")
    _assert_schema_type(schema, properties["description"], "string")
    _assert_schema_type(schema, properties["operation_id"], "string")
    _assert_field_value_map_schema(schema, properties["field_values"])
    assert "actor" not in properties
    assert "assignee" not in properties
    assert "config" not in properties


def _assert_versioned_mutation_schema(schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(schema, schema)
    _assert_object_schema(schema, resolved, ("version", "operation_id"))
    properties = resolved["properties"]
    _assert_ticket_version_schema(schema, properties["version"])
    _assert_schema_type(schema, properties["operation_id"], "string")
    assert "actor" not in properties
    assert "assignee" not in properties
    assert "config" not in properties


def _assert_report_schema(schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(schema, schema)
    _assert_object_schema(schema, resolved, ("version", "operation_id", "message"))
    properties = resolved["properties"]
    _assert_ticket_version_schema(schema, properties["version"])
    _assert_schema_type(schema, properties["operation_id"], "string")
    _assert_schema_type(schema, properties["message"], "string")
    _assert_assessment_array_schema(schema, properties["assessments"])
    assert "actor" not in properties
    assert "assignee" not in properties
    assert "config" not in properties


def _assert_transition_schema(schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(schema, schema)
    _assert_object_schema(
        schema,
        resolved,
        ("version", "operation_id", "transition_id"),
    )
    properties = resolved["properties"]
    _assert_ticket_version_schema(schema, properties["version"])
    _assert_schema_type(schema, properties["operation_id"], "string")
    _assert_schema_type(schema, properties["transition_id"], "string")
    _assert_field_value_map_schema(schema, properties["inputs"])
    _assert_field_value_map_schema(schema, properties["outputs"])
    _assert_assessment_array_schema(schema, properties["assessments"])
    assert "actor" not in properties
    assert "assignee" not in properties
    assert "config" not in properties


def _assert_artifact_schema(schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(schema, schema)
    _assert_object_schema(
        schema,
        resolved,
        ("version", "filename", "media_type", "content_b64"),
    )
    properties = resolved["properties"]
    _assert_ticket_version_schema(schema, properties["version"])
    _assert_schema_type(schema, properties["filename"], "string")
    _assert_schema_type(schema, properties["media_type"], "string")
    _assert_schema_type(schema, properties["content_b64"], "string")
    assert "operation_id" not in properties
    assert "actor" not in properties
    assert "assignee" not in properties
    assert "config" not in properties


def _assert_git_capture_schema(schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(schema, schema)
    _assert_closed_object_schema(
        schema,
        resolved,
        ("version", "operation_id", "transition_id", "field_id", "base_commit", "end_commit"),
    )
    properties = resolved["properties"]
    _assert_ticket_version_schema(schema, properties["version"])
    _assert_schema_type(schema, properties["operation_id"], "string")
    _assert_schema_type(schema, properties["transition_id"], "string")
    _assert_schema_type(schema, properties["field_id"], "string")
    _assert_schema_type(schema, properties["base_commit"], "string")
    _assert_schema_type(schema, properties["end_commit"], "string")
    assert "publication_ref" in properties
    # No caller-controlled authority: only a range and its ticket target.
    assert "workspace_path" not in properties
    assert "job_id" not in properties
    assert "agent_name" not in properties
    assert "actor" not in properties
    assert "assignee" not in properties
    assert "config" not in properties


def _assert_ticket_version_schema(root: Mapping[str, object] | None, schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(root, schema)
    _assert_closed_object_schema(
        root,
        resolved,
        ("ref", "revision", "workflow_digest", "context_digest"),
    )
    properties = resolved["properties"]
    _assert_ticket_ref_schema(root, properties["ref"])
    _assert_schema_type(root, properties["revision"], "integer")
    _assert_schema_type(root, properties["workflow_digest"], "string")
    _assert_schema_type(root, properties["context_digest"], "string")


def _assert_ticket_ref_schema(root: Mapping[str, object] | None, schema: Mapping[str, object] | None) -> None:
    resolved = _resolve_schema(root, schema)
    _assert_closed_object_schema(
        root,
        resolved,
        ("binding_id", "team_id", "workflow_id", "ticket_id"),
    )
    properties = resolved["properties"]
    _assert_schema_type(root, properties["binding_id"], "string")
    _assert_schema_type(root, properties["team_id"], "string")
    _assert_schema_type(root, properties["workflow_id"], "string")
    _assert_schema_type(root, properties["ticket_id"], "string")


def _assert_field_value_map_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
) -> None:
    resolved = _resolve_schema(root, schema)
    assert resolved.get("type") == "object"
    additional = resolved.get("additionalProperties")
    assert isinstance(additional, Mapping)
    variants = additional.get("anyOf")
    assert isinstance(variants, list)
    seen = set()
    for variant in variants:
        assert isinstance(variant, Mapping)
        candidate = _resolve_schema(root, variant)
        if candidate.get("type") == "object":
            _assert_artifact_ref_schema(root, candidate)
            seen.add("artifact")
            continue
        seen.add(candidate.get("type"))
    assert seen == {"artifact", "boolean", "integer", "number", "string", "null"}


def _assert_assessment_array_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
) -> None:
    resolved = _resolve_schema(root, schema)
    _assert_schema_type(root, resolved, "array")
    _assert_criterion_assessment_schema(root, resolved["items"])


def _assert_artifact_ref_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
) -> None:
    resolved = _resolve_schema(root, schema)
    _assert_closed_object_schema(root, resolved, ("kind", "value"))
    properties = resolved["properties"]
    _assert_schema_type(root, properties["kind"], "string")
    _assert_schema_type(root, properties["value"], "string")


def _assert_criterion_assessment_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
) -> None:
    resolved = _resolve_schema(root, schema)
    _assert_closed_object_schema(
        root,
        resolved,
        ("criterion_id", "satisfied", "reasoning", "supporting_fields"),
    )
    properties = resolved["properties"]
    _assert_schema_type(root, properties["criterion_id"], "string")
    bool_schema = _resolve_schema(root, properties["satisfied"])
    assert bool_schema.get("type") == "boolean"
    _assert_schema_type(root, properties["reasoning"], "string")
    supporting_fields = _resolve_schema(root, properties["supporting_fields"])
    assert supporting_fields.get("type") == "array"
    _assert_schema_type(root, supporting_fields["items"], "string")


def _assert_closed_object_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
    required: tuple[str, ...],
) -> None:
    _assert_object_schema(root, schema, required)
    assert schema is not None
    assert schema.get("additionalProperties") is False


def _assert_object_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
    required: tuple[str, ...],
) -> None:
    assert schema is not None
    assert schema.get("type") == "object"
    assert tuple(schema.get("required", ())) == required
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    for name in required:
        assert name in properties


def _resolve_schema(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
) -> Mapping[str, object]:
    assert root is not None
    assert schema is not None
    current = schema
    while "$ref" in current:
        ref = current["$ref"]
        assert isinstance(ref, str)
        current = _resolve_ref(root, ref)
    return current


def _resolve_ref(root: Mapping[str, object], ref: str) -> Mapping[str, object]:
    assert ref.startswith("#/")
    current: object = root
    for segment in ref.removeprefix("#/").split("/"):
        assert isinstance(current, Mapping)
        current = current[segment]
    assert isinstance(current, Mapping)
    return current


def _assert_schema_type(
    root: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
    expected_type: str,
) -> None:
    resolved = _resolve_schema(root, schema)
    direct_type = resolved.get("type")
    if direct_type == expected_type:
        return
    variants = resolved.get("anyOf")
    assert isinstance(variants, list)
    for variant in variants:
        assert isinstance(variant, Mapping)
        candidate = _resolve_schema(root, variant)
        if candidate.get("type") == expected_type:
            return
    raise AssertionError(f"schema does not include type {expected_type!r}")