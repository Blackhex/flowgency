from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Callable

from flowgency.configuration.store import ConfigSnapshot, ConfigStore
from flowgency.tickets.artifacts import RetainedArtifact, iter_internal_artifact_refs
from flowgency.tickets.errors import TicketConflict, TicketForbidden, WorkflowUnavailable
from flowgency.tickets.models import (
    ActiveTicketRun,
    AgentTicketContext,
    Clock,
    FieldProvenance,
    StorageBinding,
    TicketActor,
    TicketEvent,
    TicketMutationResult,
    TicketOperation,
    TicketPatch,
    TicketReport,
    TicketRecord,
    TicketRef,
    TransitionRequest,
    TicketVersion,
    TicketView,
    UserTicketContext,
)
from flowgency.tickets.storages.base import TicketStorage
from flowgency.tickets.transitions import report_event, transition_event
from flowgency.workflows.configuration import WorkflowBinding, resolve_workflow_binding
from flowgency.workflows.library import WorkflowLibrary
from flowgency.workflows.locking import workflow_operation
from flowgency.workflows.models import WorkflowDefinition, _kind_matches_value
from flowgency.workflows.rules import evaluate_transition


StorageFactory = Callable[[StorageBinding], TicketStorage]
AgentContextValidator = Callable[[AgentTicketContext], None]


def require_active_owner(record: TicketRecord, actor: AgentTicketContext) -> None:
    if record.assignee != actor.agent_name:
        raise TicketForbidden("assigned-elsewhere", "Ticket belongs to another agent")
    if record.active_run is None:
        raise TicketForbidden("not-working", "This run is not active on the ticket")
    if (record.active_run.job_id, record.active_run.session_id) != (
        actor.job_id,
        actor.session_id,
    ):
        raise TicketForbidden("not-working", "This run is not active on the ticket")


def _latest_assignment_event_id(record: TicketRecord) -> str:
    for event in reversed(record.events):
        if event.kind == "assigned":
            return event.id
    return ""


class TicketService:
    def __init__(
        self,
        config_store: ConfigStore,
        library: WorkflowLibrary,
        storage_factory: StorageFactory,
        validate_agent_context: AgentContextValidator,
        clock: Clock,
    ) -> None:
        self.config_store = config_store
        self._library = library
        self.storage_factory = storage_factory
        self.validate_agent_context = validate_agent_context
        self.clock = clock

    def inspect(self, actor: TicketActor, ref: TicketRef) -> TicketView:
        snapshot = self.config_store.load()
        self._validate_actor(actor)
        self._require_team_access(actor, ref.team_id)
        if isinstance(actor, AgentTicketContext):
            self._require_configured_agent(snapshot, actor)
        binding = self._resolve_binding(snapshot, ref.team_id, ref.workflow_id)
        if binding.storage.binding_id != ref.binding_id:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        record = self.storage_factory(binding.storage).read(ref)
        try:
            definition, workflow_snapshot = self._resolve_definition(binding, snapshot=snapshot)
        except WorkflowUnavailable as error:
            return TicketView(record=record, version=None, definition=None, issues=(error.message,))
        return TicketView(
            record=record,
            version=TicketVersion(
                ref=ref,
                revision=record.revision,
                workflow_digest=workflow_snapshot.digest,
                context_digest=binding.context_digest,
            ),
            definition=definition,
            issues=(),
        )

    def list_workflows(self, actor: TicketActor) -> tuple[WorkflowBinding, ...]:
        snapshot = self.config_store.load()
        self._validate_actor(actor)
        if isinstance(actor, AgentTicketContext):
            self._require_configured_agent(snapshot, actor)
        team = snapshot.config.teams[actor.team_id]
        return tuple(
            resolve_workflow_binding(snapshot, actor.team_id, workflow_id)
            for workflow_id in sorted(team.workflows)
        )

    def list_tickets(
        self,
        actor: TicketActor,
        workflow_id: str,
        *,
        assignee: str | None = None,
        state_id: str | None = None,
        query: str = "",
    ) -> tuple[TicketView, ...]:
        snapshot = self.config_store.load()
        self._validate_actor(actor)
        if isinstance(actor, AgentTicketContext):
            self._require_configured_agent(snapshot, actor)
        binding = self._resolve_binding(snapshot, actor.team_id, workflow_id)
        provider = self.storage_factory(binding.storage)
        lowered_query = query.strip().lower()
        records = tuple(
            record
            for record in provider.list(actor.team_id, workflow_id)
            if (assignee is None or record.assignee == assignee)
            and (state_id is None or record.state_id == state_id)
            and (
                not lowered_query
                or lowered_query in record.title.lower()
                or lowered_query in record.description.lower()
            )
        )
        try:
            definition, workflow_snapshot = self._resolve_definition(binding, snapshot=snapshot)
        except WorkflowUnavailable as error:
            return tuple(
                TicketView(record=record, version=None, definition=None, issues=(error.message,))
                for record in records
            )
        return tuple(
            TicketView(
                record=record,
                version=TicketVersion(
                    ref=record.ref,
                    revision=record.revision,
                    workflow_digest=workflow_snapshot.digest,
                    context_digest=binding.context_digest,
                ),
                definition=definition,
                issues=(),
            )
            for record in records
        )

    def create(
        self,
        actor: TicketActor,
        workflow_id: str,
        title: str,
        description: str,
        values: dict[str, object] | None,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        self._validate_actor(actor)
        initial = self._resolve_current_binding(actor.team_id, workflow_id)
        with workflow_operation(
            self.config_store,
            (actor.team_id,),
            (initial.blueprint_id,),
        ) as snapshot:
            binding = resolve_workflow_binding(snapshot, actor.team_id, workflow_id)
            if binding.blueprint_id != initial.blueprint_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            if isinstance(actor, AgentTicketContext):
                self._require_configured_agent(snapshot, actor)
            definition, workflow_snapshot = self._resolve_definition(binding, snapshot=snapshot)
            field_values = dict(values or {})
            self._validate_field_values(definition, field_values)
            now = self.clock()
            event_id = uuid.uuid4().hex
            event = self._event("opened", actor, "Ticket created", event_id, now)
            provider = self.storage_factory(binding.storage)
            ticket_id = self._creation_ticket_id(binding.storage, actor, operation)
            ref = TicketRef.from_binding(binding.storage, ticket_id)
            record = TicketRecord(
                id=ticket_id,
                number=0,
                title=title,
                description=description,
                state_id=definition.initial_state,
                assignee=None,
                active_run=None,
                field_values=field_values,
                field_provenance=self._stamp_field_provenance(actor, field_values, event_id, now),
                revision=1,
                events=(event,),
                receipts=(),
                created_at=now,
                updated_at=now,
                ref=ref,
            )
            self._require_current_contract(binding, snapshot.revision, workflow_snapshot.digest)
            return provider.create(record, operation)

    def assign(
        self,
        actor: UserTicketContext,
        version: TicketVersion,
        assignee: str | None,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        if not isinstance(actor, UserTicketContext):
            raise TicketForbidden("forbidden", "Only a user may assign tickets")
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._assign_record(
                snapshot,
                record,
                assignee,
                now,
                self._event("assigned", actor, "Ticket assignment changed", event_id, now),
            ),
        )

    def start_work(
        self,
        actor: AgentTicketContext,
        version: TicketVersion,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        if not isinstance(actor, AgentTicketContext):
            raise TicketForbidden("forbidden", "Only an agent may start work")
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._start_work_record(
                snapshot,
                record,
                actor,
                now,
                self._event("started-work", actor, "Agent started work", event_id, now),
            ),
        )

    def end_work(
        self,
        actor: AgentTicketContext,
        version: TicketVersion,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        if not isinstance(actor, AgentTicketContext):
            raise TicketForbidden("forbidden", "Only an agent may end work")
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._end_work_record(
                record,
                actor,
                self._event("ended-work", actor, "Agent ended active work", event_id, now),
            ),
        )

    def sign_off(
        self,
        actor: AgentTicketContext,
        version: TicketVersion,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        if not isinstance(actor, AgentTicketContext):
            raise TicketForbidden("forbidden", "Only an agent may sign off tickets")
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._sign_off_record(
                record,
                actor,
                self._event("signed-off", actor, "Agent signed off the ticket", event_id, now),
            ),
        )

    def _cleanup_job_target(
        self,
        binding: StorageBinding,
        ref: TicketRef,
        *,
        job_id: str,
        assignment_event_id: str,
        expected_generation: str | None,
        stopped,
    ) -> TicketRecord:
        provider = self.storage_factory(binding)
        current = provider.read(ref)
        clear_pending = bool(
            stopped.confirmed
            and stopped.job_id == job_id
            and current.pending_run is not None
            and current.pending_run.job_id == job_id
            and current.pending_run.assignment_event_id == assignment_event_id
        )
        from flowgency.jobs.processes import may_clear_active_work

        clear_active = bool(
            expected_generation is not None
            and expected_generation == stopped.generation
            and may_clear_active_work(current, stopped)
        )
        if not clear_pending and not clear_active:
            return current
        operation = TicketOperation(
            operation_id=f"cleanup-{job_id}-{stopped.generation}",
            request_digest=uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cleanup:{ref.binding_id}:{ref.ticket_id}:{job_id}:{stopped.generation}:{assignment_event_id}:{stopped.confirmed}",
            ).hex,
        )
        result = provider.apply(
            ref,
            current.revision,
            operation,
            lambda record: record.model_copy(
                update={
                    "pending_run": None if clear_pending else record.pending_run,
                    "active_run": None if clear_active else record.active_run,
                    "events": record.events
                    + (
                        TicketEvent(
                            kind="ticket-run-cleanup",
                            actor="system",
                            summary="Ticket run cleanup confirmed",
                        ),
                    ),
                }
            ),
        )
        return result.ticket

    def update(
        self,
        actor: TicketActor,
        version: TicketVersion,
        patch: TicketPatch,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._update_record(
                record,
                definition,
                actor,
                patch,
                now,
                event_id,
                self._event("updated", actor, "Ticket content updated", event_id, now),
            ),
        )

    def transition(
        self,
        actor: AgentTicketContext,
        version: TicketVersion,
        request: TransitionRequest,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        if not isinstance(actor, AgentTicketContext):
            raise TicketForbidden("forbidden", "Only an agent may transition tickets")
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._transition_record(
                record,
                definition,
                actor,
                request,
                now,
                event_id,
                operation,
                version,
                binding,
            ),
        )

    def report(
        self,
        actor: AgentTicketContext,
        version: TicketVersion,
        report: TicketReport,
        operation: TicketOperation,
    ) -> TicketMutationResult:
        if not isinstance(actor, AgentTicketContext):
            raise TicketForbidden("forbidden", "Only an agent may report on tickets")
        return self._mutate(
            actor,
            version,
            operation,
            lambda record, definition, now, event_id, snapshot, binding: self._report_record(
                record,
                definition,
                actor,
                report,
                now,
                event_id,
                operation,
                version,
            ),
        )

    def publish_artifact(
        self,
        actor: TicketActor,
        version: TicketVersion,
        filename: str,
        media_type: str,
        content: bytes,
    ):
        self._validate_actor(actor)
        self._require_team_access(actor, version.ref.team_id)
        initial = self._resolve_current_binding(version.ref.team_id, version.ref.workflow_id)
        with workflow_operation(
            self.config_store,
            (version.ref.team_id,),
            (initial.blueprint_id,),
        ) as snapshot:
            binding = resolve_workflow_binding(snapshot, version.ref.team_id, version.ref.workflow_id)
            if binding.blueprint_id != initial.blueprint_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            if isinstance(actor, AgentTicketContext):
                self._require_configured_agent(snapshot, actor)
            self._require_current_version(binding, version)
            _, snapshot_def = self._resolve_definition(binding, snapshot=snapshot)
            if snapshot_def.digest != version.workflow_digest:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            provider = self.storage_factory(binding.storage)
            record = provider.read(version.ref)
            if record.revision != version.revision:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            if isinstance(actor, AgentTicketContext):
                require_active_owner(record, actor)
            artifact = RetainedArtifact.create(filename, media_type, content)
            self._require_current_contract(binding, snapshot.revision, snapshot_def.digest)
            return provider.put_artifact(version.ref, artifact)

    def _mutate(
        self,
        actor: TicketActor,
        version: TicketVersion,
        operation: TicketOperation,
        mutation: Callable[
            [TicketRecord, WorkflowDefinition, object, str, ConfigSnapshot, WorkflowBinding],
            TicketRecord,
        ],
    ) -> TicketMutationResult:
        self._validate_actor(actor)
        self._require_team_access(actor, version.ref.team_id)
        initial = self._resolve_current_binding(version.ref.team_id, version.ref.workflow_id)
        with workflow_operation(
            self.config_store,
            (version.ref.team_id,),
            (initial.blueprint_id,),
        ) as snapshot:
            binding = resolve_workflow_binding(snapshot, version.ref.team_id, version.ref.workflow_id)
            if binding.blueprint_id != initial.blueprint_id:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            if isinstance(actor, AgentTicketContext):
                self._require_configured_agent(snapshot, actor)
            self._require_current_version(binding, version)
            definition, snapshot_def = self._resolve_definition(binding, snapshot=snapshot)
            if snapshot_def.digest != version.workflow_digest:
                raise TicketConflict("stale-ticket", "Refresh the ticket")
            provider = self.storage_factory(binding.storage)
            now = self.clock()
            event_id = uuid.uuid4().hex
            return provider.apply(
                version.ref,
                version.revision,
                operation,
                lambda record: self._mutate_current_record(
                    mutation,
                    record,
                    definition,
                    now,
                    event_id,
                    snapshot,
                    binding,
                    snapshot_def.digest,
                ),
            )

    def _mutate_current_record(
        self,
        mutation: Callable[
            [TicketRecord, WorkflowDefinition, object, str, ConfigSnapshot, WorkflowBinding],
            TicketRecord,
        ],
        record: TicketRecord,
        definition: WorkflowDefinition,
        now,
        event_id: str,
        snapshot: ConfigSnapshot,
        binding: WorkflowBinding,
        workflow_digest: str,
    ) -> TicketRecord:
        self._require_current_contract(binding, snapshot.revision, workflow_digest)
        return mutation(record, definition, now, event_id, snapshot, binding)

    def _assign_record(
        self,
        snapshot: ConfigSnapshot,
        record: TicketRecord,
        assignee: str | None,
        now,
        event: TicketEvent,
    ) -> TicketRecord:
        if record.active_run is not None:
            raise TicketConflict("already-working", "Ticket is currently active")
        if assignee is not None and assignee not in snapshot.config.teams[record.ref.team_id].agents:
            raise TicketForbidden("unknown-agent", "Configured agent does not exist")
        return record.model_copy(
            update={
                "assignee": assignee,
                "events": record.events + (event,),
            }
        )

    def _start_work_record(
        self,
        snapshot: ConfigSnapshot,
        record: TicketRecord,
        actor: AgentTicketContext,
        now,
        event: TicketEvent,
    ) -> TicketRecord:
        if record.assignee not in (None, actor.agent_name):
            raise TicketForbidden("assigned-elsewhere", "Ticket belongs to another agent")
        pending_run = record.pending_run
        if pending_run is not None:
            assignment_event_id = _latest_assignment_event_id(record)
            if (
                pending_run.job_id != actor.job_id
                or pending_run.assignee != actor.agent_name
                or pending_run.assignment_event_id != assignment_event_id
            ):
                raise TicketConflict("already-queued", "Another queued run owns this ticket")
        if record.active_run is not None and (
            record.active_run.job_id,
            record.active_run.session_id,
        ) != (
            actor.job_id,
            actor.session_id,
        ):
            raise TicketConflict("already-working", "Another run is working on this ticket")
        return record.model_copy(
            update={
                "assignee": actor.agent_name,
                "pending_run": None,
                "active_run": ActiveTicketRun(
                    job_id=actor.job_id,
                    session_id=actor.session_id,
                    started_at=now,
                ),
                "events": record.events + (event,),
            }
        )

    def _end_work_record(
        self,
        record: TicketRecord,
        actor: AgentTicketContext,
        event: TicketEvent,
    ) -> TicketRecord:
        self._require_agent_ownership(record, actor)
        return record.model_copy(
            update={
                "active_run": None,
                "events": record.events + (event,),
            }
        )

    def _sign_off_record(
        self,
        record: TicketRecord,
        actor: AgentTicketContext,
        event: TicketEvent,
    ) -> TicketRecord:
        self._require_agent_ownership(record, actor)
        return record.model_copy(
            update={
                "assignee": None,
                "active_run": None,
                "events": record.events + (event,),
            }
        )

    def _update_record(
        self,
        record: TicketRecord,
        definition: WorkflowDefinition,
        actor: TicketActor,
        patch: TicketPatch,
        now,
        event_id: str,
        event: TicketEvent,
    ) -> TicketRecord:
        if isinstance(actor, AgentTicketContext):
            self._require_agent_ownership(record, actor)
        field_values = dict(record.field_values)
        field_provenance = dict(record.field_provenance)
        if patch.field_values is not None:
            self._validate_field_values(definition, patch.field_values)
            field_values.update(patch.field_values)
            field_provenance.update(
                self._stamp_field_provenance(actor, patch.field_values, event_id, now)
            )
        return record.model_copy(
            update={
                "title": patch.title if patch.title is not None else record.title,
                "description": patch.description if patch.description is not None else record.description,
                "field_values": field_values,
                "field_provenance": field_provenance,
                "events": record.events + (event,),
            }
        )

    def _require_current_version(
        self,
        binding: WorkflowBinding,
        version: TicketVersion,
    ) -> None:
        if binding.storage.binding_id != version.ref.binding_id:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if binding.context_digest != version.context_digest:
            raise TicketConflict("stale-ticket", "Refresh the ticket")

    def _require_agent_ownership(
        self,
        record: TicketRecord,
        actor: AgentTicketContext,
    ) -> None:
        require_active_owner(record, actor)

    def _validate_actor(self, actor: TicketActor) -> None:
        if isinstance(actor, AgentTicketContext):
            try:
                self.validate_agent_context(actor)
            except Exception as error:
                raise TicketForbidden("invalid-agent-context", "Agent context is not authorized") from error

    def _require_configured_agent(self, snapshot: ConfigSnapshot, actor: AgentTicketContext) -> None:
        if actor.agent_name not in snapshot.config.teams[actor.team_id].agents:
            raise TicketForbidden("unknown-agent", "Configured agent does not exist")

    def _require_team_access(self, actor: TicketActor, team_id: str) -> None:
        if actor.team_id != team_id:
            raise TicketForbidden("wrong-team", "Ticket belongs to another team")

    def _resolve_current_binding(self, team_id: str, workflow_id: str) -> WorkflowBinding:
        snapshot = self.config_store.load()
        return self._resolve_binding(snapshot, team_id, workflow_id)

    def _resolve_binding(
        self,
        snapshot: ConfigSnapshot,
        team_id: str,
        workflow_id: str,
    ) -> WorkflowBinding:
        try:
            return resolve_workflow_binding(snapshot, team_id, workflow_id)
        except KeyError as error:
            raise WorkflowUnavailable("unknown-workflow", "Workflow is not currently available") from error

    def _resolve_definition(
        self,
        binding: WorkflowBinding,
        *,
        snapshot: ConfigSnapshot | None = None,
    ) -> tuple[WorkflowDefinition, object]:
        current = snapshot or self.config_store.load()
        library = self.library_for(current)
        try:
            workflow_snapshot = library.inspect(binding.blueprint_id)
        except Exception as error:
            raise WorkflowUnavailable("unavailable-workflow", "Current workflow definition is unavailable") from error
        return workflow_snapshot.definition, workflow_snapshot

    def library_for(self, snapshot: ConfigSnapshot) -> WorkflowLibrary:
        configured = snapshot.config.flowgency.workflow_library
        if configured is None:
            raise WorkflowUnavailable("unavailable-workflow", "Current workflow definition is unavailable")
        configured_root = os.path.normcase(str(Path(configured).resolve(strict=False)))
        cached_root = os.path.normcase(str(self._library.root.resolve(strict=False)))
        if configured_root == cached_root:
            return self._library
        return WorkflowLibrary(Path(configured))

    def _require_current_contract(
        self,
        binding: WorkflowBinding,
        expected_config_revision: str,
        expected_workflow_digest: str | None,
    ) -> None:
        current = self._load_current_snapshot()
        current_binding = self._resolve_binding(current, binding.team_id, binding.workflow_id)
        if current.revision != expected_config_revision and not self._bindings_match(binding, current_binding):
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if current_binding.blueprint_id != binding.blueprint_id:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if expected_workflow_digest is None:
            return
        current_definition, current_snapshot = self._resolve_definition(current_binding, snapshot=current)
        if current_definition.id != binding.blueprint_id:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        if current_snapshot.digest != expected_workflow_digest:
            raise TicketConflict("stale-ticket", "Refresh the ticket")

    def _bindings_match(
        self,
        expected: WorkflowBinding,
        current: WorkflowBinding,
    ) -> bool:
        return (
            current.storage.binding_id == expected.storage.binding_id
            and current.context_digest == expected.context_digest
            and current.blueprint_id == expected.blueprint_id
        )

    def _load_current_snapshot(self) -> ConfigSnapshot:
        payload = self.config_store.path.read_bytes()
        return self.config_store._snapshot(payload)

    def _validate_field_values(
        self,
        definition: WorkflowDefinition,
        values: dict[str, object],
    ) -> None:
        field_kinds = {field.id: field.type for field in definition.fields}
        for field_id, value in values.items():
            kind = field_kinds.get(field_id)
            if kind is None:
                raise TicketConflict("unknown-field", f"Unknown field {field_id!r}")
            if value is not None and not _kind_matches_value(kind, value):
                raise TicketConflict(
                    "invalid-field-value",
                    f"Field {field_id!r} does not match type {kind!r}",
                )

    def _event(
        self,
        kind: str,
        actor: TicketActor,
        summary: str,
        event_id: str,
        now,
    ) -> TicketEvent:
        actor_name = actor.actor_name if isinstance(actor, UserTicketContext) else actor.agent_name
        data = {"job_id": actor.job_id} if isinstance(actor, AgentTicketContext) else {}
        return TicketEvent(id=event_id, kind=kind, actor=actor_name, summary=summary, data=data, at=now)

    def _stamp_field_provenance(
        self,
        actor: TicketActor,
        values: dict[str, object],
        event_id: str,
        now,
    ) -> dict[str, FieldProvenance]:
        if isinstance(actor, UserTicketContext):
            return {
                field_id: FieldProvenance(
                    actor_kind="user",
                    actor_name=actor.actor_name,
                    job_id=None,
                    event_id=event_id,
                    recorded_at=now,
                )
                for field_id in values
            }
        return {
            field_id: FieldProvenance(
                actor_kind="agent",
                actor_name=actor.agent_name,
                job_id=actor.job_id,
                event_id=event_id,
                recorded_at=now,
            )
            for field_id in values
        }

    def _creation_ticket_id(
        self,
        binding: StorageBinding,
        actor: TicketActor,
        operation: TicketOperation,
    ) -> str:
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, binding.binding_id)
        identity = json.dumps(
            {
                "actor": self._creation_actor_identity(actor),
                "operation_id": operation.operation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"ticket-{uuid.uuid5(namespace, identity)}"

    def _creation_actor_identity(self, actor: TicketActor) -> dict[str, str]:
        if isinstance(actor, UserTicketContext):
            return {
                "actor_kind": "user",
                "actor_name": actor.actor_name,
                "team_id": actor.team_id,
            }
        return {
            "actor_kind": "agent",
            "agent_name": actor.agent_name,
            "job_id": actor.job_id,
            "session_id": actor.session_id,
            "team_id": actor.team_id,
        }

    def _transition_record(
        self,
        record: TicketRecord,
        definition: WorkflowDefinition,
        actor: AgentTicketContext,
        request: TransitionRequest,
        now,
        event_id: str,
        operation: TicketOperation,
        version: TicketVersion,
        binding: WorkflowBinding,
    ) -> TicketRecord:
        require_active_owner(record, actor)
        evaluated = evaluate_transition(
            definition,
            request.transition_id,
            record.state_id,
            dict(record.field_values),
            dict(request.inputs),
            dict(request.outputs),
            request.assessments,
        )
        if record.ref is None:
            raise TicketConflict("unbound-record", "Ticket record must be scoped with a ref")
        provider = self.storage_factory(binding.storage)
        for artifact_ref in iter_internal_artifact_refs(
            dict(evaluated.effective_inputs),
            dict(evaluated.effective_outputs),
        ):
            provider.read_artifact(record.ref, artifact_ref.value)
        field_values = dict(record.field_values)
        field_values.update(dict(evaluated.effective_outputs))
        field_provenance = dict(record.field_provenance)
        field_provenance.update(
            self._stamp_field_provenance(
                actor,
                dict(evaluated.effective_outputs),
                event_id,
                now,
            )
        )
        base_event = transition_event(record, evaluated, actor)
        event = base_event.model_copy(
            update={
                "id": event_id,
                "at": now,
                "data": {
                    **base_event.data,
                    "source_state_name": definition.state(record.state_id).name,
                    "destination_state_name": definition.state(
                        evaluated.destination_state_id
                    ).name,
                    "blueprint_id": definition.id,
                    "blueprint_name": definition.name,
                    "workflow_digest": version.workflow_digest,
                    "context_digest": version.context_digest,
                    "binding_id": binding.storage.binding_id,
                    "operation_id": operation.operation_id,
                    "recorded_at": now.isoformat(),
                },
            }
        )
        return record.model_copy(
            update={
                "state_id": evaluated.destination_state_id,
                "field_values": field_values,
                "field_provenance": field_provenance,
                "events": record.events + (event,),
            }
        )

    def _report_record(
        self,
        record: TicketRecord,
        definition: WorkflowDefinition,
        actor: AgentTicketContext,
        report: TicketReport,
        now,
        event_id: str,
        operation: TicketOperation,
        version: TicketVersion,
    ) -> TicketRecord:
        require_active_owner(record, actor)
        base_event = report_event(report, actor)
        event = base_event.model_copy(
            update={
                "id": event_id,
                "at": now,
                "data": {
                    **base_event.data,
                    "state_id": record.state_id,
                    "state_name": definition.state(record.state_id).name,
                    "workflow_digest": version.workflow_digest,
                    "context_digest": version.context_digest,
                    "binding_id": version.ref.binding_id,
                    "operation_id": operation.operation_id,
                    "recorded_at": now.isoformat(),
                },
            }
        )
        return record.model_copy(update={"events": record.events + (event,)})
