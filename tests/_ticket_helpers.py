from __future__ import annotations

import hashlib
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import yaml

from flowgency.configuration.store import ConfigStore
from flowgency.blueprints.projectors import StaticRuntimeProjector
from flowgency.integrations import BaseIntegration
from flowgency.integrations.models import RuntimeCapabilities
from flowgency.jobs.authority import JobAuthorityRef, JobStore
from flowgency.jobs.launcher import LaunchResult
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from flowgency.jobs.store import read_job, write_job
from flowgency.projector_capabilities import ProjectorCapabilities
from flowgency.tickets.models import (
    ActiveTicketRun,
    AgentTicketContext,
    FieldProvenance,
    StorageBinding,
    TicketEvent,
    TicketOperation,
    TicketReport,
    TicketRecord,
    TicketRef,
    TransitionRequest,
    TicketView,
    UserTicketContext,
)
from flowgency.tickets.service import TicketService
from flowgency.tickets.storages.local import LocalTicketStorage
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.configuration import (
    WorkflowConfigurationService,
    WorkflowInstancePatch,
    resolve_workflow_binding,
)
from flowgency.workflows.library import WorkflowLibrary
from flowgency.workflows.models import AgentCriterion, ArtifactRef

SEED_TIME = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _ticket_runtime_projector() -> StaticRuntimeProjector:
    return StaticRuntimeProjector(
        version="v-ticket-tests",
        capabilities=ProjectorCapabilities(
            instruction_target=Path("AGENTS.md"),
            skills_target=Path(".agents/skills"),
            prompts_target=Path(".github/prompts"),
            prompt_format="prompt-markdown",
            discovers_instructions=True,
            discovers_skills=True,
            discovers_prompts=True,
            activates_selected_skill=True,
        ),
    )


class TicketRuntimeIntegration(BaseIntegration):
    name = "claude-code"
    display_name = "Ticket Test Runtime"
    supports_execution = True
    projector = _ticket_runtime_projector()
    declared_runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"restricted", "unrestricted"}),
        path_scopable_tools=frozenset({"read", "search", "write", "shell"}),
        live_ticket_transport="mcp-stdio",
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


@dataclass(frozen=True)
class DurableJobProbe:
    store: JobStore
    team_id: str

    def authority_for_handle(self, handle) -> JobAuthorityRef:
        record = read_job(handle.path)
        return self.store.reference(
            self.team_id,
            handle.job_id,
            record.authority_digest,
        )

    def read(self, handle) -> JobRecord:
        return self.store.read(self.authority_for_handle(handle))


def system_event(
    kind: str = "opened",
    actor: str = "system",
    summary: str = "Ticket created",
) -> TicketEvent:
    """A trusted, not-yet-finalized audit event supplied by a mutation."""
    return TicketEvent(kind=kind, actor=actor, summary=summary)


def ticket_record(
    ticket_id: str = "ticket-a",
    state_id: str = "review",
    agent: str | None = None,
    active_run: ActiveTicketRun | None = None,
) -> TicketRecord:
    """Build a valid seed record with UTC timestamps and fixed test IDs."""
    return TicketRecord(
        id=ticket_id,
        number=0,
        title="Ticket A",
        description="Body text for ticket A.",
        state_id=state_id,
        assignee=agent,
        active_run=active_run,
        field_values={
            "summary": "hello",
            "spec": ArtifactRef(kind="url", value="https://example.com/spec"),
        },
        field_provenance={
            "summary": FieldProvenance(
                actor_kind="user",
                actor_name="seed",
                job_id=None,
                event_id="seed",
                recorded_at=SEED_TIME,
            )
        },
        revision=1,
        events=(system_event(),),
        receipts=(),
        created_at=SEED_TIME,
        updated_at=SEED_TIME,
    )


def storage_binding(
    root: Path,
    team_id: str = "team-a",
    workflow_id: str = "board-a",
) -> StorageBinding:
    """Provider envelope carrying the board namespace, not a blueprint pin."""
    return StorageBinding(
        integration="local",
        config={"root": str(root)},
        team_id=team_id,
        workflow_id=workflow_id,
    )


def delivery_definition() -> dict:
    """Task 1's sample blueprint, as an authored source mapping."""
    return {
        "schema_version": 1,
        "id": "delivery",
        "name": "Delivery",
        "description": "Deliver verified work",
        "initial_state": "review",
        "states": [
            {"id": "review", "name": "Review", "color": "#ebc77c"},
            {"id": "done", "name": "Done", "color": "#7ad7bf"},
        ],
        "fields": [
            {"id": "verdict", "label": "Review verdict", "type": "boolean"},
            {"id": "summary", "label": "Review summary", "type": "text"},
        ],
        "transitions": [
            {
                "id": "complete",
                "name": "Complete",
                "from_state": "review",
                "to_state": "done",
                "inputs": [{"field_id": "verdict", "required": True}],
                "outputs": [{"field_id": "summary", "required": True}],
                "preconditions": [
                    {"field_id": "verdict", "operator": "equals", "value": True}
                ],
                "criteria": [],
            }
        ],
    }


@dataclass
class WorkflowTestEnv:
    tmp_path: Path
    store: ConfigStore
    library: WorkflowLibrary
    binding: StorageBinding
    provider: LocalTicketStorage
    service: TicketService
    root_a: Path
    root_b: Path
    configuration_service: WorkflowConfigurationService
    user: UserTicketContext
    registered_sessions: set[str]
    job_store: JobStore
    team_id: str = "newsletter"
    workflow_id: str = "board-a"
    blueprint_id: str = "delivery"
    _seq: int = field(default=0)
    _session_seq: int = field(default=0)

    def _clock(self) -> datetime:
        return SEED_TIME

    def seed_ticket(self, state_id: str = "review", values=None) -> TicketRef:
        """Create a ticket through the real provider; fixture seeding only."""
        self._seq += 1
        ticket_id = f"ticket-{self._seq}"
        ref = TicketRef.from_binding(self.binding, ticket_id)
        record = TicketRecord(
            id=ticket_id,
            number=0,
            title=f"Ticket {self._seq}",
            description="Body text.",
            state_id=state_id,
            field_values=dict(values) if values is not None else {"summary": "hello"},
            field_provenance={},
            revision=1,
            events=(
                TicketEvent(kind="opened", actor="system", summary="Ticket created"),
            ),
            receipts=(),
            created_at=SEED_TIME,
            updated_at=SEED_TIME,
            ref=ref,
        )
        operation = TicketOperation(
            operation_id=f"seed-{ticket_id}", request_digest=f"seed-{ticket_id}"
        )
        self.provider.create(record, operation)
        return ref

    def register_session(self, session_id: str) -> None:
        self.registered_sessions.add(session_id)

    def revoke_session(self, session_id: str) -> None:
        self.registered_sessions.discard(session_id)

    def agent(self, name: str, job_id: str) -> AgentTicketContext:
        return self.agent_for_team(self.team_id, name, job_id)

    def agent_for_team(self, team_id: str, name: str, job_id: str) -> AgentTicketContext:
        self._session_seq += 1
        session_id = f"session-{name}-{job_id}-{self._session_seq}"
        self.register_session(session_id)
        return AgentTicketContext(
            job_id=job_id,
            team_id=team_id,
            agent_name=name,
            session_id=session_id,
        )

    def operation(self, label: str, actor_name: str = "local-user") -> TicketOperation:
        digest = hashlib.sha256(
            f"{self.team_id}:{self.workflow_id}:{actor_name}:{label}".encode("utf-8")
        ).hexdigest()
        return TicketOperation(
            operation_id=f"op-{actor_name}-{label}",
            request_digest=digest,
        )

    def create(
        self,
        title: str = "Sample",
        values: dict | None = None,
        actor: UserTicketContext | None = None,
    ) -> TicketView:
        caller = actor or self.user
        result = self.service.create(
            caller,
            self.workflow_id,
            title,
            "Body text.",
            dict(values) if values is not None else {"summary": "hello"},
            self.operation(f"create-{title}", actor_name=caller.actor_name),
        )
        assert result.ticket.ref is not None
        return self.service.inspect(caller, result.ticket.ref)

    def read(
        self,
        ref: TicketRef,
        actor: UserTicketContext | AgentTicketContext | None = None,
    ) -> TicketView:
        return self.service.inspect(actor or self.user, ref)

    def write_blueprint(self, blueprint_id: str, definition: dict) -> None:
        """Author an additional blueprint source in the library, fixture-only."""
        directory = self.library.root / blueprint_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "workflow.yaml").write_text(
            yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def set_storage_root(self, root: Path) -> None:
        snapshot = self.store.load()
        self.configuration_service.save_instance(
            snapshot.revision,
            self.team_id,
            self.workflow_id,
            WorkflowInstancePatch(
                name="Board A",
                blueprint=self.blueprint_id,
                integration="local",
                integration_config={"root": str(root)},
            ),
        )

    def current_provider(self) -> LocalTicketStorage:
        snapshot = self.store.load()
        binding = resolve_workflow_binding(
            snapshot, self.team_id, self.workflow_id
        ).storage
        return resolve_storage(binding, clock=self._clock)

    def running_job(self, agent: str, job_id: str) -> JobAuthorityRef:
        snapshot = self.store.load()
        team = snapshot.config.teams[self.team_id]
        spec = JobSpec(
            schema_version=5,
            job_id=job_id,
            config_path=str(self.store.path.resolve()),
            config_revision=snapshot.revision,
            team_key=self.team_id,
            workspace_root=str(team.workspace_path.resolve()),
            team_root=str(team.path.resolve()),
            agent_name=agent,
            trigger="manual_prompt",
            integration_name="claude-code",
            integration_config={},
            blueprint=BlueprintRef(
                key=f"{agent}-blueprint",
                source_digest="source-digest",
                integration="claude-code",
                projector_version="p1",
                cache_path=str((self.tmp_path / "cache" / "claude-code" / "p1" / "source-digest").resolve()),
            ),
            routine_id=None,
            skill=None,
            skill_arguments=(),
            task_input="Run task safely",
            runtime_policy=RuntimePolicySnapshot(timeout=30, mode="restricted"),
            memory=MemoryBinding(
                selector={"scope": "agent"},
                canonical_json='{"agent":"%s","scope":"agent","team":"%s","version":1}' % (agent, self.team_id),
                memory_hash="a" * 64,
                path=str((self.tmp_path / "memory" / ("a" * 64)).resolve()),
            ),
            trigger_context=None,
            prompt_source={
                "type": "instance_prompt",
                "scope": "instance",
                "name": "manual",
                "source_path": "manual.prompt.md",
                "source_digest": "source-digest",
            },
            timeout_override=None,
            created_at=SEED_TIME.isoformat(),
        )
        record = JobRecord.from_spec(spec)
        authority = self.job_store.create(record)
        running = replace(
            record,
            status="running",
            worker_pid=123,
            started_at=SEED_TIME.isoformat(),
            launched_at=SEED_TIME.isoformat(),
            session_id=f"job-session-{agent}-{job_id}",
        )
        write_job(authority.path, running)
        return authority

    @property
    def access_registry(self):
        from flowgency.tickets.access import TicketAccessRegistry

        if not hasattr(self, "_access_registry"):
            self._access_registry = TicketAccessRegistry(self.job_store)
            self.service.validate_agent_context = self._access_registry.validate_context
        return self._access_registry

    @contextmanager
    def broker_for(self, authority: JobAuthorityRef):
        from flowgency.tickets.broker import TicketBroker, TicketToolClient

        with TicketBroker(self.service, self.access_registry, authority=authority) as broker:
            endpoint = broker.endpoint
            yield TicketToolClient(endpoint.url, endpoint.grant.token)

    @contextmanager
    def broker_session(self, authority: JobAuthorityRef):
        from flowgency.tickets.broker import TicketBroker, TicketToolClient

        with TicketBroker(self.service, self.access_registry, authority=authority) as broker:
            endpoint = broker.endpoint
            yield endpoint.grant.context, TicketToolClient(endpoint.url, endpoint.grant.token)

    def transition_request(
        self,
        *,
        transition_id: str = "complete",
        inputs: dict | None = None,
        outputs: dict | None = None,
        assessments=(),
    ) -> TransitionRequest:
        return TransitionRequest(
            transition_id=transition_id,
            inputs=dict(inputs or {}),
            outputs=dict(outputs or {}),
            assessments=tuple(assessments),
        )

    def ticket_report(self, message: str, assessments=()) -> TicketReport:
        return TicketReport(message=message, assessments=tuple(assessments))

    def rename_transition(self, transition_id: str, new_name: str) -> None:
        source = self.library.inspect(self.blueprint_id)
        transitions = tuple(
            transition.model_copy(update={"name": new_name})
            if transition.id == transition_id
            else transition
            for transition in source.definition.transitions
        )
        candidate = source.definition.model_copy(update={"transitions": transitions})
        self.configuration_service.save_blueprint(
            self.store.load().revision,
            self.blueprint_id,
            source.digest,
            candidate,
        )

    def publish_artifact_field_workflow(self) -> None:
        source = self.library.inspect(self.blueprint_id)
        fields = source.definition.fields
        if all(field.id != "evidence" for field in fields):
            fields = fields + (
                source.definition.field("summary").model_copy(
                    update={"id": "evidence", "label": "Evidence", "type": "artifact"}
                ),
            )
        transitions = tuple(
            transition.model_copy(
                update={
                    "outputs": transition.outputs + (
                        transition.outputs[0].model_copy(
                            update={"field_id": "evidence", "required": True}
                        ),
                    )
                }
            )
            if transition.id == "complete"
            and all(use.field_id != "evidence" for use in transition.outputs)
            else transition
            for transition in source.definition.transitions
        )
        candidate = source.definition.model_copy(
            update={"fields": fields, "transitions": transitions}
        )
        self.configuration_service.save_blueprint(
            self.store.load().revision,
            self.blueprint_id,
            source.digest,
            candidate,
        )

    def publish_criteria_workflow(self) -> None:
        source = self.library.inspect(self.blueprint_id)
        transitions = tuple(
            transition.model_copy(
                update={
                    "criteria": transition.criteria
                    + (
                        AgentCriterion(
                            id="evidence-reviewed",
                            description="Evidence was reviewed",
                        ),
                    )
                }
            )
            if transition.id == "complete"
            and all(criterion.id != "evidence-reviewed" for criterion in transition.criteria)
            else transition
            for transition in source.definition.transitions
        )
        candidate = source.definition.model_copy(update={"transitions": transitions})
        self.configuration_service.save_blueprint(
            self.store.load().revision,
            self.blueprint_id,
            source.digest,
            candidate,
        )


def make_workflow_environment(tmp_path: Path, raw_config: dict) -> WorkflowTestEnv:
    """Build a real-filesystem workflow environment from an existing raw config."""
    raw = deepcopy(raw_config)

    library_root = tmp_path / "workflow-library"
    (library_root / "delivery").mkdir(parents=True)
    (library_root / "delivery" / "workflow.yaml").write_text(
        yaml.safe_dump(delivery_definition(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    root_a = tmp_path / "tickets-a"
    root_b = tmp_path / "tickets-b"
    workspace_support = tmp_path / "workspace-support"
    team_support = tmp_path / "teams" / "support"
    root_a.mkdir()
    root_b.mkdir()
    workspace_support.mkdir()
    team_support.mkdir(parents=True)

    raw["flowgency"]["workflow_library"] = str(library_root)
    team = raw["teams"]["newsletter"]
    team["workflows"] = {
        "board-a": {
            "name": "Board A",
            "blueprint": "delivery",
            "integration": "local",
            "integration_config": {"root": str(root_a)},
        }
    }
    team["agents"].append(
        {
            "name": "observer",
            "blueprint": "observer-blueprint",
            "integration": "claude-code",
            "permissions": {
                "mode": "restricted",
                "rules": [{"tools": ["read", "search"]}],
            },
        }
    )
    raw["teams"]["support"] = {
        "name": "Support",
        "workspace_path": str(workspace_support),
        "path": str(team_support),
        "default_integration": "claude-code",
        "permissions": {
            "mode": "unrestricted",
            "rules": [{"tools": None}],
        },
        "runtime": {},
        "agents": [
            {
                "name": "builder",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "prompts": [],
                "routines": [],
            }
        ],
        "workspaces": [],
        "workflows": {
            "board-a": {
                "name": "Board A",
                "blueprint": "delivery",
                "integration": "local",
                "integration_config": {"root": str(root_b)},
            }
        },
    }

    store = ConfigStore(tmp_path / "config.yaml")
    store.create(raw)

    library = WorkflowLibrary(library_root)
    binding = storage_binding(root_a, team_id="newsletter", workflow_id="board-a")
    provider = LocalTicketStorage(root_a, clock=lambda: SEED_TIME)
    configuration_service = WorkflowConfigurationService(
        store, library, lambda b: resolve_storage(b)
    )
    registered_sessions: set[str] = set()
    job_store = JobStore(Path(raw["flowgency"]["memory_store"]))

    def validate_agent_context(context: AgentTicketContext) -> None:
        if context.session_id not in registered_sessions:
            raise PermissionError(f"Unknown test session: {context.session_id}")

    service = TicketService(
        store,
        library,
        lambda storage: resolve_storage(storage, clock=lambda: SEED_TIME),
        validate_agent_context,
        clock=lambda: SEED_TIME,
    )
    return WorkflowTestEnv(
        tmp_path=tmp_path,
        store=store,
        library=library,
        binding=binding,
        provider=provider,
        service=service,
        root_a=root_a,
        root_b=root_b,
        configuration_service=configuration_service,
        user=UserTicketContext(team_id="newsletter"),
        registered_sessions=registered_sessions,
        job_store=job_store,
    )


@dataclass
class TicketJobTestEnv(WorkflowTestEnv):
    coordinator: object | None = None
    jobs: DurableJobProbe | None = None
    launcher: Mock | None = None
    generation: str | None = None

    def create_assigned(self, agent: str, title: str = "Assigned") -> TicketView:
        ticket = self.create(title)
        self.service.assign(
            self.user,
            ticket.version,
            agent,
            self.operation(f"assign-{ticket.ref.ticket_id}"),
        )
        return self.read(ticket.ref)

    def assign_idle(self, ref: TicketRef, agent: str | None) -> TicketView:
        current = self.read(ref)
        self.service.assign(
            self.user,
            current.version,
            agent,
            self.operation(f"reassign-{ref.ticket_id}"),
        )
        return self.read(ref)

    def start_two_tickets(
        self,
        *,
        agent: str,
        job_id: str,
    ) -> tuple[JobAuthorityRef, tuple[TicketRef, TicketRef]]:
        first = self.create_assigned(agent, "First")
        second = self.create_assigned(agent, "Second")
        authority = self.running_job(agent, job_id)
        with self.broker_session(authority) as (context, client):
            self.generation = context.session_id
            for index, ticket in enumerate((first, second), start=1):
                looked_up = client.call(
                    "get_ticket",
                    {"ref": ticket.ref.model_dump(mode="json")},
                )
                assert looked_up["ok"] is True
                started = client.call(
                    "start_work",
                    {
                        "version": ticket.version.model_dump(mode="json"),
                        "operation_id": f"start-{job_id}-{index}",
                    },
                )
                assert started["ok"] is True
        return authority, (first.ref, second.ref)


def make_ticket_job_environment(tmp_path: Path, raw_config: dict, monkeypatch) -> TicketJobTestEnv:
    env = make_workflow_environment(tmp_path, raw_config)
    snapshot = env.store.load()
    library_root = snapshot.config.flowgency.agent_library
    for blueprint_root, title in (
        (library_root / "builder-blueprint", "Builder"),
        (library_root / "observer-blueprint", "Observer"),
    ):
        blueprint_root.mkdir(parents=True, exist_ok=True)
        (blueprint_root / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")

    import flowgency.jobs.submission as submission_module

    monkeypatch.setattr(
        submission_module,
        "REGISTRY",
        {"claude-code": TicketRuntimeIntegration()},
    )

    from flowgency.jobs.submission import submit_job_request
    from flowgency.jobs.tickets import TicketJobCoordinator

    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)
    coordinator = TicketJobCoordinator(
        service=env.service,
        job_store=env.job_store,
        config_store=env.store,
        submitter=lambda request: submit_job_request(request, launcher),
    )
    return TicketJobTestEnv(
        **env.__dict__,
        coordinator=coordinator,
        jobs=DurableJobProbe(env.job_store, env.team_id),
        launcher=launcher,
    )

