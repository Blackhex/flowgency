from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from flowgency.configuration.store import ConfigStore
from flowgency.tickets.models import (
    ActiveTicketRun,
    AgentTicketContext,
    FieldProvenance,
    StorageBinding,
    TicketEvent,
    TicketOperation,
    TicketRecord,
    TicketRef,
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
from flowgency.workflows.models import ArtifactRef

SEED_TIME = datetime(2026, 9, 8, tzinfo=timezone.utc)


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
    )

