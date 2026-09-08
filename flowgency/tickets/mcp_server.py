from __future__ import annotations

import os

from mcp.server import MCPServer
from pydantic import BaseModel

from flowgency.tickets.broker import TicketToolClient, validate_loopback_endpoint
from flowgency.tickets.models import TicketRef, TicketToolResponse, TicketVersion
from flowgency.tickets.protocol import (
    ListTicketsCommand,
    TicketArtifactPublishCommand,
    TicketCreateCommand,
    TicketEndWorkCommand,
    TicketGetCommand,
    TicketReportCommand,
    TicketSignOffCommand,
    TicketStartWorkCommand,
    TicketTransitionCommand,
    TicketUpdateCommand,
)
from flowgency.workflows.models import CriterionAssessment, FieldValue


def _call(client: TicketToolClient, operation: str, payload: dict) -> TicketToolResponse:
    return TicketToolResponse.model_validate(client.call(operation, payload))


def _payload(command: BaseModel) -> dict[str, object]:
    payload = command.model_dump(mode="json")
    assert isinstance(payload, dict)
    return payload


def build_mcp_server(client: TicketToolClient) -> MCPServer:
    server = MCPServer("flowgency-tickets")

    @server.tool(structured_output=True)
    def workflows_list() -> TicketToolResponse:
        return _call(client, "list_workflows", {})

    @server.tool(structured_output=True)
    def tickets_list(
        workflow_id: str,
        query: str = "",
        assignee: str | None = None,
        state_id: str | None = None,
    ) -> TicketToolResponse:
        return _call(
            client,
            "list_tickets",
            _payload(
                ListTicketsCommand(
                    workflow_id=workflow_id,
                    query=query,
                    assignee=assignee,
                    state_id=state_id,
                )
            ),
        )

    @server.tool(structured_output=True)
    def ticket_get(ref: TicketRef) -> TicketToolResponse:
        return _call(client, "get_ticket", _payload(TicketGetCommand(ref=ref)))

    @server.tool(structured_output=True)
    def ticket_create(
        workflow_id: str,
        title: str,
        description: str,
        operation_id: str,
        field_values: dict[str, FieldValue] = {},
    ) -> TicketToolResponse:
        return _call(
            client,
            "create_ticket",
            _payload(
                TicketCreateCommand(
                    workflow_id=workflow_id,
                    title=title,
                    description=description,
                    operation_id=operation_id,
                    field_values=dict(field_values),
                )
            ),
        )

    @server.tool(structured_output=True)
    def ticket_start_work(version: TicketVersion, operation_id: str) -> TicketToolResponse:
        return _call(
            client,
            "start_work",
            _payload(TicketStartWorkCommand(version=version, operation_id=operation_id)),
        )

    register_remaining_ticket_tools(server, client)
    return server


def register_remaining_ticket_tools(server: MCPServer, client: TicketToolClient) -> None:
    @server.tool(structured_output=True)
    def ticket_update(
        version: TicketVersion,
        operation_id: str,
        title: str | None = None,
        description: str | None = None,
        field_values: dict[str, FieldValue] | None = None,
    ) -> TicketToolResponse:
        return _call(
            client,
            "update_ticket",
            _payload(
                TicketUpdateCommand(
                    version=version,
                    operation_id=operation_id,
                    title=title,
                    description=description,
                    field_values=field_values,
                )
            ),
        )

    @server.tool(structured_output=True)
    def ticket_report(
        version: TicketVersion,
        operation_id: str,
        message: str,
        assessments: tuple[CriterionAssessment, ...] = (),
    ) -> TicketToolResponse:
        return _call(
            client,
            "report_ticket",
            _payload(
                TicketReportCommand(
                    version=version,
                    operation_id=operation_id,
                    message=message,
                    assessments=assessments,
                )
            ),
        )

    @server.tool(structured_output=True)
    def ticket_transition(
        version: TicketVersion,
        operation_id: str,
        transition_id: str,
        inputs: dict[str, FieldValue] = {},
        outputs: dict[str, FieldValue] = {},
        assessments: tuple[CriterionAssessment, ...] = (),
    ) -> TicketToolResponse:
        return _call(
            client,
            "transition_ticket",
            _payload(
                TicketTransitionCommand(
                    version=version,
                    operation_id=operation_id,
                    transition_id=transition_id,
                    inputs=dict(inputs),
                    outputs=dict(outputs),
                    assessments=assessments,
                )
            ),
        )

    @server.tool(structured_output=True)
    def ticket_end_work(version: TicketVersion, operation_id: str) -> TicketToolResponse:
        return _call(
            client,
            "end_work",
            _payload(TicketEndWorkCommand(version=version, operation_id=operation_id)),
        )

    @server.tool(structured_output=True)
    def ticket_sign_off(version: TicketVersion, operation_id: str) -> TicketToolResponse:
        return _call(
            client,
            "sign_off",
            _payload(TicketSignOffCommand(version=version, operation_id=operation_id)),
        )

    @server.tool(structured_output=True)
    def ticket_artifact_publish(
        version: TicketVersion,
        filename: str,
        media_type: str,
        content_b64: str,
    ) -> TicketToolResponse:
        return _call(
            client,
            "publish_artifact",
            _payload(
                TicketArtifactPublishCommand(
                    version=version,
                    filename=filename,
                    media_type=media_type,
                    content_b64=content_b64,
                )
            ),
        )


def main() -> None:
    endpoint = validate_loopback_endpoint(os.environ["FLOWGENCY_TICKET_ENDPOINT"])
    token = os.environ["FLOWGENCY_TICKET_TOKEN"]
    build_mcp_server(TicketToolClient(endpoint, token)).run(transport="stdio")


if __name__ == "__main__":
    main()