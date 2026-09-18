from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from flowgency.tickets.artifacts import MAX_RETAINED_ARTIFACT_BYTES
from flowgency.tickets.errors import TicketConflict, TicketStorageError
from flowgency.tickets.git_evidence import GitCaptureRequest
from flowgency.tickets.models import (
    AgentTicketContext,
    TicketOperation,
    TicketPatch,
    TicketRef,
    TicketReport,
    TicketVersion,
    TransitionRequest,
)
from flowgency.tickets.service import TicketService
from flowgency.workflows.configuration import WorkflowBinding
from flowgency.workflows.models import CriterionAssessment, FieldValue


class InvalidTicketRequest(TicketStorageError):
    http_status = 422


class TicketBrokerUnavailable(TicketStorageError):
    http_status = 503


class ListWorkflowsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListTicketsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workflow_id: StrictStr
    assignee: StrictStr | None = None
    state_id: StrictStr | None = None
    query: StrictStr = ""


class TicketGetCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: TicketRef


class TicketCreateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workflow_id: StrictStr
    title: StrictStr
    description: StrictStr
    field_values: dict[str, FieldValue] = Field(default_factory=dict)
    operation_id: StrictStr


class TicketStartWorkCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr


class TicketUpdateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr
    title: StrictStr | None = None
    description: StrictStr | None = None
    field_values: dict[str, FieldValue] | None = None

    def patch(self) -> TicketPatch:
        return TicketPatch(
            title=self.title,
            description=self.description,
            field_values=self.field_values,
        )


class TicketReportCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr
    message: StrictStr
    assessments: tuple[CriterionAssessment, ...] = ()

    def report(self) -> TicketReport:
        return TicketReport(message=self.message, assessments=self.assessments)


class TicketTransitionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr
    transition_id: StrictStr
    inputs: dict[str, FieldValue] = Field(default_factory=dict)
    outputs: dict[str, FieldValue] = Field(default_factory=dict)
    assessments: tuple[CriterionAssessment, ...] = ()

    def request(self) -> TransitionRequest:
        return TransitionRequest(
            transition_id=self.transition_id,
            inputs=self.inputs,
            outputs=self.outputs,
            assessments=self.assessments,
        )


class TicketEndWorkCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr


class TicketSignOffCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr


class TicketGitCaptureCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    operation_id: StrictStr
    transition_id: StrictStr
    field_id: StrictStr
    base_commit: StrictStr
    end_commit: StrictStr
    publication_ref: StrictStr | None = None

    def request(self) -> GitCaptureRequest:
        return GitCaptureRequest(
            transition_id=self.transition_id,
            field_id=self.field_id,
            base_commit=self.base_commit,
            end_commit=self.end_commit,
            publication_ref=self.publication_ref,
        )


class TicketArtifactPublishCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: TicketVersion
    filename: StrictStr
    media_type: StrictStr
    content_b64: StrictStr

    def content(self) -> bytes:
        try:
            content = base64.b64decode(self.content_b64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise InvalidTicketRequest(
                "invalid-request",
                "Artifact content must be strict base64",
            ) from error
        if len(content) > MAX_RETAINED_ARTIFACT_BYTES:
            raise InvalidTicketRequest(
                "invalid-request",
                "Artifact content exceeds the maximum size",
            )
        return content


TicketCommand = (
    ListWorkflowsCommand
    | ListTicketsCommand
    | TicketGetCommand
    | TicketCreateCommand
    | TicketStartWorkCommand
    | TicketUpdateCommand
    | TicketReportCommand
    | TicketTransitionCommand
    | TicketEndWorkCommand
    | TicketSignOffCommand
    | TicketArtifactPublishCommand
    | TicketGitCaptureCommand
)


def _safe_validation_issues(
    error: ValidationError,
    model: type[BaseModel],
) -> list[dict[str, object]]:
    safe_top_level_fields = frozenset(model.model_fields)
    issues: list[dict[str, object]] = []
    for issue in error.errors(include_url=False):
        raw_location = issue.get("loc", ())
        location: list[str | int] = []
        if isinstance(raw_location, tuple | list) and raw_location:
            first = raw_location[0]
            if isinstance(first, str) and first in safe_top_level_fields:
                location.append(first)
        issue_type = issue.get("type")
        issues.append(
            {
                "location": location,
                "type": issue_type if isinstance(issue_type, str) else "invalid",
            }
        )
    return issues


def parse_ticket_command(operation: str, payload: dict[str, Any]) -> TicketCommand:
    mapping: dict[str, type[BaseModel]] = {
        "list_workflows": ListWorkflowsCommand,
        "list_tickets": ListTicketsCommand,
        "get_ticket": TicketGetCommand,
        "create_ticket": TicketCreateCommand,
        "start_work": TicketStartWorkCommand,
        "update_ticket": TicketUpdateCommand,
        "report_ticket": TicketReportCommand,
        "transition_ticket": TicketTransitionCommand,
        "end_work": TicketEndWorkCommand,
        "sign_off": TicketSignOffCommand,
        "publish_artifact": TicketArtifactPublishCommand,
        "capture_git_evidence": TicketGitCaptureCommand,
    }
    model = mapping.get(operation)
    if model is None:
        raise InvalidTicketRequest("invalid-request", "Unknown ticket operation")
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise InvalidTicketRequest(
            "invalid-request",
            "Ticket request payload is invalid",
            issues=_safe_validation_issues(error, model),
        ) from error


def command_target_ref(command: TicketCommand) -> TicketRef | None:
    version = getattr(command, "version", None)
    if version is not None:
        return version.ref
    ref = getattr(command, "ref", None)
    if isinstance(ref, TicketRef):
        return ref
    return None


def canonical_operation(
    actor: AgentTicketContext,
    operation_name: str,
    operation_id: str,
    payload: dict[str, Any],
) -> TicketOperation:
    digest = hashlib.sha256(
        json.dumps(
            {
                "actor": actor.model_dump(mode="json"),
                "operation": operation_name,
                "operation_id": operation_id,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return TicketOperation(operation_id=operation_id, request_digest=digest)


def binding_for_command(
    service: TicketService,
    actor: AgentTicketContext,
    command: TicketCommand,
) -> WorkflowBinding | None:
    ref = command_target_ref(command)
    if ref is None:
        return None
    if ref.team_id != actor.team_id:
        raise InvalidTicketRequest(
            "invalid-request",
            "Ticket payload does not match the authenticated team",
        )
    binding = service._resolve_current_binding(ref.team_id, ref.workflow_id)
    if binding.storage.binding_id != ref.binding_id:
        raise TicketConflict("stale-ticket", "Refresh the ticket")
    return binding


def dispatch_ticket_command(
    service: TicketService,
    actor: AgentTicketContext,
    command: TicketCommand,
):
    if isinstance(command, ListWorkflowsCommand):
        result = []
        for binding in service.list_workflows(actor):
            definition, _ = service._resolve_definition(binding)
            result.append(
                {
                    "team_id": binding.team_id,
                    "workflow_id": binding.workflow_id,
                    "blueprint_id": binding.blueprint_id,
                    "binding_id": binding.storage.binding_id,
                    "context_digest": binding.context_digest,
                    "definition": definition.model_dump(mode="json"),
                }
            )
        return result
    if isinstance(command, ListTicketsCommand):
        return [
            item.model_dump(mode="json")
            for item in service.list_tickets(
                actor,
                command.workflow_id,
                assignee=command.assignee,
                state_id=command.state_id,
                query=command.query,
            )
        ]
    if isinstance(command, TicketGetCommand):
        return service.inspect(actor, command.ref).model_dump(mode="json")
    if isinstance(command, TicketCreateCommand):
        operation = canonical_operation(
            actor,
            "create_ticket",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.create(
            actor,
            command.workflow_id,
            command.title,
            command.description,
            command.field_values,
            operation,
        ).model_dump(mode="json")
    if isinstance(command, TicketStartWorkCommand):
        operation = canonical_operation(
            actor,
            "start_work",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.start_work(actor, command.version, operation).model_dump(mode="json")
    if isinstance(command, TicketUpdateCommand):
        operation = canonical_operation(
            actor,
            "update_ticket",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.update(actor, command.version, command.patch(), operation).model_dump(mode="json")
    if isinstance(command, TicketReportCommand):
        operation = canonical_operation(
            actor,
            "report_ticket",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.report(actor, command.version, command.report(), operation).model_dump(mode="json")
    if isinstance(command, TicketTransitionCommand):
        operation = canonical_operation(
            actor,
            "transition_ticket",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.transition(actor, command.version, command.request(), operation).model_dump(mode="json")
    if isinstance(command, TicketEndWorkCommand):
        operation = canonical_operation(
            actor,
            "end_work",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.end_work(actor, command.version, operation).model_dump(mode="json")
    if isinstance(command, TicketSignOffCommand):
        operation = canonical_operation(
            actor,
            "sign_off",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.sign_off(actor, command.version, operation).model_dump(mode="json")
    if isinstance(command, TicketArtifactPublishCommand):
        return service.publish_artifact(
            actor,
            command.version,
            command.filename,
            command.media_type,
            command.content(),
        ).model_dump(mode="json")
    if isinstance(command, TicketGitCaptureCommand):
        operation = canonical_operation(
            actor,
            "capture_git_evidence",
            command.operation_id,
            command.model_dump(mode="json"),
        )
        return service.capture_git_evidence(
            actor, command.version, command.request(), operation
        ).model_dump(mode="json")
    raise TicketBrokerUnavailable("unavailable", "Ticket broker operation is unavailable")