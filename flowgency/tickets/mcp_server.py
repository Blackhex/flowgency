from __future__ import annotations

from collections.abc import Awaitable, Callable

from mcp.server import MCPServer
from mcp.server.context import ServerRequestContext
from pydantic import BaseModel

from flowgency.tickets.models import TicketRef, TicketToolResponse, TicketVersion
from flowgency.tickets.protocol import (
    ListTicketsCommand,
    TicketArtifactPublishCommand,
    TicketCreateCommand,
    TicketEndWorkCommand,
    TicketGetCommand,
    TicketGitCaptureCommand,
    TicketReportCommand,
    TicketSignOffCommand,
    TicketStartWorkCommand,
    TicketTransitionCommand,
    TicketUpdateCommand,
)
from flowgency.workflows.models import CriterionAssessment, FieldValue

TicketToolCaller = Callable[[str, dict], dict]

# SDK signature validation runs before our dispatch, and on failure the SDK's
# tool-error content echoes the raw rejected argument values (a path or token an
# agent slipped into a typed field) and, for a crash, a stack trace. This fixed
# message is actionable — it tells the caller its arguments did not fit the
# typed schema — without leaking any of that. Domain errors never surface here:
# they return a structured ``{ok:false,error}`` envelope with ``isError`` unset.
_SAFE_TOOL_ERROR_TEXT = "Tool call failed: the arguments did not satisfy the tool's typed schema."


async def _sanitize_tool_errors(
    ctx: ServerRequestContext,
    call_next: Callable[[ServerRequestContext], Awaitable[object]],
) -> object:
    """Scrub SDK-level ``tools/call`` error content before it leaves the server.

    Runs as official ``MCPServer`` middleware, so the typed schemas and fixed
    command set stay fully in force; only the outgoing error text is replaced.
    """
    result = await call_next(ctx)
    if ctx.method != "tools/call":
        return result
    if isinstance(result, dict):
        if not result.get("isError"):
            return result
        result["content"] = [{"type": "text", "text": _SAFE_TOOL_ERROR_TEXT}]
        result.pop("structuredContent", None)
        return result
    if getattr(result, "is_error", False):
        from mcp.types import CallToolResult, TextContent

        return CallToolResult(
            content=[TextContent(type="text", text=_SAFE_TOOL_ERROR_TEXT)],
            is_error=True,
        )
    return result


def _call(caller: TicketToolCaller, operation: str, payload: dict) -> TicketToolResponse:
    return TicketToolResponse.model_validate(caller(operation, payload))


def _payload(command: BaseModel) -> dict[str, object]:
    payload = command.model_dump(mode="json")
    assert isinstance(payload, dict)
    return payload


def build_mcp_server(caller: TicketToolCaller) -> MCPServer:
    server = MCPServer("flowgency-tickets", middleware=[_sanitize_tool_errors])

    @server.tool(structured_output=True)
    def workflows_list() -> TicketToolResponse:
        return _call(caller, "list_workflows", {})

    @server.tool(structured_output=True)
    def tickets_list(
        workflow_id: str,
        query: str = "",
        assignee: str | None = None,
        state_id: str | None = None,
    ) -> TicketToolResponse:
        return _call(
            caller,
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
        return _call(caller, "get_ticket", _payload(TicketGetCommand(ref=ref)))

    @server.tool(structured_output=True)
    def ticket_create(
        workflow_id: str,
        title: str,
        description: str,
        operation_id: str,
        field_values: dict[str, FieldValue] = {},
    ) -> TicketToolResponse:
        return _call(
            caller,
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
            caller,
            "start_work",
            _payload(TicketStartWorkCommand(version=version, operation_id=operation_id)),
        )

    register_remaining_ticket_tools(server, caller)
    return server


def register_remaining_ticket_tools(server: MCPServer, caller: TicketToolCaller) -> None:
    @server.tool(structured_output=True)
    def ticket_update(
        version: TicketVersion,
        operation_id: str,
        title: str | None = None,
        description: str | None = None,
        field_values: dict[str, FieldValue] | None = None,
    ) -> TicketToolResponse:
        """Explicitly edit ticket title, description, or field values without a transition.

        This is a direct field edit, not a substitute for submitting a required
        transition output through ticket_transition.
        """
        return _call(
            caller,
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
        """Record an informational note. Does not mutate ticket fields or state.

        Use this to report a blocker or progress. It never satisfies a required
        transition output; submit durable results through ticket_transition instead.
        """
        return _call(
            caller,
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
        """Advance a ticket with its declared inputs, durable outputs, and assessments.

        Required outputs must be supplied in outputs. Inputs are attempt-only context;
        reports and logs do not save outputs. Accepted outputs and state commit together.
        """
        return _call(
            caller,
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
            caller,
            "end_work",
            _payload(TicketEndWorkCommand(version=version, operation_id=operation_id)),
        )

    @server.tool(structured_output=True)
    def ticket_sign_off(version: TicketVersion, operation_id: str) -> TicketToolResponse:
        return _call(
            caller,
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
            caller,
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

    @server.tool(structured_output=True)
    def ticket_capture_git_evidence(
        version: TicketVersion,
        operation_id: str,
        transition_id: str,
        field_id: str,
        base_commit: str,
        end_commit: str,
        publication_ref: str | None = None,
    ) -> TicketToolResponse:
        """Capture an already-committed local Git range as trusted ticket evidence.

        Capture only reads local history that is already committed; it never
        commits, pushes, fetches, or verifies a push. Commit locally (and push
        separately, if the project's own instructions require it) before calling
        this tool with the exact base and end commit ids to capture.
        """
        return _call(
            caller,
            "capture_git_evidence",
            _payload(
                TicketGitCaptureCommand(
                    version=version,
                    operation_id=operation_id,
                    transition_id=transition_id,
                    field_id=field_id,
                    base_commit=base_commit,
                    end_commit=end_commit,
                    publication_ref=publication_ref,
                )
            ),
        )
