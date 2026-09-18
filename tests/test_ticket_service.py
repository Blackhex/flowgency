from __future__ import annotations

import hashlib

import pytest
import yaml
from pydantic import ValidationError

from flowgency.tickets.models import TicketOperation, TicketPatch, UserTicketContext
from flowgency.tickets.errors import OperationConflict, TicketConflict, TicketForbidden, WorkflowUnavailable
from flowgency.tickets.storages.local import LocalTicketStorage
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.configuration import WorkflowInstancePatch, patch_workflow_instance
from flowgency.workflows.models import ArtifactRef
from tests._git_evidence_helpers import requires_git
from tests._ticket_helpers import storage_binding, ticket_record


class _BoundaryHookStorage:
    def __init__(self, inner, hooks: dict[str, object | None]) -> None:
        self._inner = inner
        self._hooks = hooks

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def create(self, record, operation):
        hook = self._hooks.get("create")
        if hook is not None:
            self._hooks["create"] = None
            hook()
        return self._inner.create(record, operation)

    def apply(self, ref, expected_revision, operation, mutate):
        hook = self._hooks.get("apply")
        if hook is not None:
            self._hooks["apply"] = None
            hook()
        return self._inner.apply(ref, expected_revision, operation, mutate)


def _install_boundary_hooks(
    workflow_env,
    *,
    before_factory=None,
    before_create=None,
    before_apply=None,
) -> None:
    original_factory = workflow_env.service.storage_factory
    hooks: dict[str, object | None] = {
        "factory": before_factory,
        "create": before_create,
        "apply": before_apply,
    }

    def factory(storage):
        hook = hooks.get("factory")
        if hook is not None:
            hooks["factory"] = None
            hook()
        return _BoundaryHookStorage(original_factory(storage), hooks)

    workflow_env.service.storage_factory = factory


def test_create_assigns_initial_state_and_version(workflow_env):
    env = workflow_env
    ticket = env.create("Created", values={"summary": "hello"})
    assert ticket.record.state_id == "review"
    assert ticket.version.ref == ticket.ref
    assert ticket.definition is not None
    assert ticket.issues == ()


def test_create_same_content_with_different_operation_ids_creates_distinct_tickets(workflow_env):
    env = workflow_env
    digest = hashlib.sha256(b"same-content").hexdigest()
    first = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        TicketOperation(operation_id="op-user-first", request_digest=digest),
    )
    second = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        TicketOperation(operation_id="op-user-second", request_digest=digest),
    )
    assert first.ticket.ref is not None
    assert second.ticket.ref is not None
    assert first.ticket.ref.ticket_id != second.ticket.ref.ticket_id


def test_agent_created_event_records_originating_job(workflow_env):
    env = workflow_env
    actor = env.agent("builder", "activity-run")
    created = env.service.create(
        actor,
        env.workflow_id,
        "Activity provenance",
        "Body",
        {"summary": "ready"},
        env.operation("activity-create", actor_name=actor.agent_name),
    )
    stored = env.provider.read(created.ticket.ref)
    assert stored.events[-1].data["job_id"] == actor.job_id
    assert stored.events[-1].actor == actor.agent_name


def test_create_reused_operation_id_with_changed_digest_conflicts_without_extra_ticket(workflow_env):
    env = workflow_env
    first = TicketOperation(
        operation_id="op-user-reused",
        request_digest=hashlib.sha256(b"first-digest").hexdigest(),
    )
    second = TicketOperation(
        operation_id="op-user-reused",
        request_digest=hashlib.sha256(b"second-digest").hexdigest(),
    )
    created = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        first,
    )
    assert created.ticket.ref is not None
    with pytest.raises(OperationConflict):
        env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            second,
        )
    replayed = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        first,
    )
    assert replayed.ticket.ref == created.ticket.ref
    assert replayed.replayed is True


def test_create_same_operation_and_digest_replays_original_ticket(workflow_env):
    env = workflow_env
    operation = TicketOperation(
        operation_id="op-user-replay",
        request_digest=hashlib.sha256(b"replay").hexdigest(),
    )
    first = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        operation,
    )
    second = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        operation,
    )
    assert first.ticket.ref == second.ticket.ref
    assert second.replayed is True


def test_create_scopes_ticket_identity_by_actor_and_binding(workflow_env):
    env = workflow_env
    digest = hashlib.sha256(b"same-content").hexdigest()
    user_ticket = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        TicketOperation(operation_id="op-shared", request_digest=digest),
    )
    other_actor = UserTicketContext(team_id=env.team_id, actor_name="other-user")
    other_actor_ticket = env.service.create(
        other_actor,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        TicketOperation(operation_id="op-shared", request_digest=digest),
    )
    env.set_storage_root(env.root_b)
    other_binding_ticket = env.service.create(
        env.user,
        env.workflow_id,
        "Created",
        "Body text.",
        {"summary": "hello"},
        TicketOperation(operation_id="op-shared", request_digest=digest),
    )
    assert user_ticket.ticket.ref is not None
    assert other_actor_ticket.ticket.ref is not None
    assert other_binding_ticket.ticket.ref is not None
    assert user_ticket.ticket.ref.ticket_id != other_actor_ticket.ticket.ref.ticket_id
    assert user_ticket.ticket.ref.ticket_id != other_binding_ticket.ticket.ref.ticket_id


def test_stale_revision_is_rejected(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.service.update(
        env.user,
        ticket.version,
        ticket.patch(description="Changed once"),
        env.operation("first-update"),
    )
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Stale update"),
            env.operation("stale-update"),
        )


def test_binding_change_invalidates_old_version(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.set_storage_root(env.root_b)
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Wrong board"),
            env.operation("moved-binding"),
        )


def test_changed_source_digest_invalidates_old_version_without_ticket_changes(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "hello"})
    workflow_file = env.library.root / env.blueprint_id / "workflow.yaml"
    definition = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    definition["description"] = "Digest changed but binding stayed fixed"
    workflow_file.write_text(
        yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Stale source update"),
            env.operation("stale-source-version"),
        )
    stored = env.read(ticket.ref).record
    assert stored.description == ticket.record.description
    assert stored.revision == ticket.record.revision
    assert len(stored.events) == len(ticket.record.events)


def test_missing_blueprint_returns_readable_unavailable_view(workflow_env):
    env = workflow_env
    ticket = env.create()
    workflow_file = env.library.root / env.blueprint_id / "workflow.yaml"
    workflow_file.unlink()
    view = env.read(ticket.ref)
    assert view.definition is None
    assert view.version is None
    assert view.issues
    with pytest.raises(WorkflowUnavailable):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="No blueprint"),
            env.operation("missing-blueprint"),
        )


def test_inspect_rejects_revoked_agent_before_unavailable_view(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.revoke_session(actor.session_id)
    workflow_file = env.library.root / env.blueprint_id / "workflow.yaml"
    workflow_file.unlink()
    with pytest.raises(TicketForbidden):
        env.service.inspect(actor, ticket.ref)


def test_inspect_rejects_unknown_configured_agent(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    snapshot = env.store.load()

    def patch(raw):
        raw["teams"][env.team_id]["agents"] = [
            agent
            for agent in raw["teams"][env.team_id]["agents"]
            if agent["name"] != actor.agent_name
        ]

    env.store.patch(snapshot.revision, patch)
    with pytest.raises(TicketForbidden):
        env.service.inspect(actor, ticket.ref)


def test_update_rechecks_changed_source_inside_locked_callback(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before"})

    def change_source() -> None:
        workflow_file = env.library.root / env.blueprint_id / "workflow.yaml"
        definition = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
        definition["description"] = "Changed after service precheck"
        workflow_file.write_text(
            yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    _install_boundary_hooks(env, before_apply=change_source)
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Should fail"),
            env.operation("source-change-before-apply"),
        )
    stored = env.read(ticket.ref).record
    assert stored.description == ticket.record.description
    assert stored.revision == ticket.record.revision
    assert len(stored.events) == len(ticket.record.events)


def test_update_rechecks_changed_binding_inside_locked_callback(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before"})

    def change_binding() -> None:
        # Direct config-lock patch: env.set_storage_root re-enters the team lock already held by TicketService.update.
        revision = env.store.load().revision
        patch_workflow_instance(
            env.store,
            revision,
            env.team_id,
            env.workflow_id,
            WorkflowInstancePatch(
                name="Board A",
                blueprint=env.blueprint_id,
                integration="local",
                integration_config={"root": str(env.root_b)},
            ),
        )

    _install_boundary_hooks(env, before_apply=change_binding)
    with pytest.raises(TicketConflict):
        env.service.update(
            env.user,
            ticket.version,
            ticket.patch(description="Should fail"),
            env.operation("binding-change-before-apply"),
        )
    provider = resolve_storage(storage_binding(env.root_a, env.team_id, env.workflow_id), clock=env._clock)
    stored = provider.read(ticket.ref)
    assert stored.description == ticket.record.description
    assert stored.revision == ticket.record.revision
    assert len(stored.events) == len(ticket.record.events)


def test_create_rechecks_changed_source_before_provider_create(workflow_env):
    env = workflow_env

    def change_source() -> None:
        workflow_file = env.library.root / env.blueprint_id / "workflow.yaml"
        definition = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
        definition["description"] = "Changed before provider create"
        workflow_file.write_text(
            yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    _install_boundary_hooks(env, before_factory=change_source)
    with pytest.raises(TicketConflict):
        env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            env.operation("create-boundary"),
        )
    assert resolve_storage(storage_binding(env.root_a, env.team_id, env.workflow_id), clock=env._clock).list(env.team_id, env.workflow_id) == ()


def test_unknown_agent_session_is_rejected(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.revoke_session(actor.session_id)
    with pytest.raises(TicketForbidden):
        env.service.start_work(
            actor,
            ticket.version,
            env.operation("untrusted", actor_name=actor.agent_name),
        )


def test_registered_unknown_agent_is_rejected(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent_for_team(env.team_id, "ghost", "run-a")
    with pytest.raises(TicketForbidden):
        env.service.start_work(
            actor,
            ticket.version,
            env.operation("unknown-agent", actor_name=actor.agent_name),
        )


def test_patch_rejects_state_and_actor_injection():
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"state_id": "done"})
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"actor_name": "spoofed"})
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"assignee": "builder"})
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"active_run": {"job_id": "run-a", "session_id": "s", "started_at": "2026-09-08T00:00:00Z"}})
    with pytest.raises(ValidationError):
        TicketPatch.model_validate({"provenance": {"summary": "spoofed"}})


def test_user_assign_rejects_unknown_assignee(workflow_env):
    env = workflow_env
    ticket = env.create()
    with pytest.raises(TicketForbidden):
        env.service.assign(
            env.user,
            ticket.version,
            "ghost",
            env.operation("assign-ghost"),
        )


def test_agent_cannot_update_without_matching_active_run(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    with pytest.raises(TicketForbidden):
        env.service.update(
            actor,
            ticket.version,
            ticket.patch(description="Agent edit"),
            env.operation("agent-update", actor_name=actor.agent_name),
        )


def test_user_may_edit_during_active_work_and_provenance_is_trusted(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before"})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    updated = env.service.update(
        env.user,
        active.version,
        active.patch(field_values={"summary": "after"}),
        env.operation("user-edit"),
    )
    assert updated.ticket.assignee == "builder"
    assert updated.ticket.active_run is not None
    provenance = updated.ticket.field_provenance["summary"]
    assert provenance.actor_kind == "user"
    assert provenance.actor_name == env.user.actor_name
    assert provenance.event_id == updated.event_id
    assert "job_id" not in env.read(ticket.ref).record.events[-1].data


def test_agent_update_records_originating_job(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before"})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start-update", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    updated = env.service.update(
        actor,
        active.version,
        active.patch(field_values={"summary": "after"}),
        env.operation("agent-edit", actor_name=actor.agent_name),
    )
    persisted = env.read(ticket.ref).record
    event = persisted.events[-1]
    assert updated.ticket.assignee == "builder"
    assert updated.ticket.active_run is not None
    assert event.kind == "updated"
    assert event.actor == actor.agent_name
    assert event.data["job_id"] == actor.job_id


def test_transition_ready_update_retains_assignment(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "before", "verdict": False})
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("start-transition", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    updated = env.service.update(
        env.user,
        active.version,
        active.patch(field_values={"verdict": True}),
        env.operation("make-ready"),
    )
    assert updated.ticket.assignee == "builder"
    assert updated.ticket.active_run is not None
    assert updated.ticket.field_values["verdict"] is True


def test_two_runs_of_same_agent_conflict(workflow_env):
    env = workflow_env
    ticket = env.create()
    first = env.agent("builder", "run-a")
    second = env.agent("builder", "run-b")
    env.service.start_work(
        first,
        ticket.version,
        env.operation("first-start", actor_name=first.agent_name),
    )
    with pytest.raises(TicketConflict):
        env.service.start_work(
            second,
            env.read(ticket.ref).version,
            env.operation("second-start", actor_name=second.agent_name),
        )


def test_later_session_of_same_job_conflicts(workflow_env):
    env = workflow_env
    ticket = env.create()
    first = env.agent("builder", "run-a")
    second = env.agent("builder", "run-a")
    env.service.start_work(
        first,
        ticket.version,
        env.operation("first-session", actor_name=first.agent_name),
    )
    with pytest.raises(TicketConflict):
        env.service.start_work(
            second,
            env.read(ticket.ref).version,
            env.operation("second-session", actor_name=second.agent_name),
        )


def test_older_generation_cannot_clear_later_marker(workflow_env):
    env = workflow_env
    ticket = env.create()
    first = env.agent("builder", "run-a")
    second = env.agent("builder", "run-b")
    env.service.start_work(
        first,
        ticket.version,
        env.operation("start-a", actor_name=first.agent_name),
    )
    env.service.end_work(
        first,
        env.read(ticket.ref).version,
        env.operation("end-a", actor_name=first.agent_name),
    )
    env.service.start_work(
        second,
        env.read(ticket.ref).version,
        env.operation("start-b", actor_name=second.agent_name),
    )
    latest = env.read(ticket.ref).version
    with pytest.raises(TicketForbidden):
        env.service.end_work(
            first,
            latest,
            env.operation("old-end", actor_name=first.agent_name),
        )
    with pytest.raises(TicketForbidden):
        env.service.sign_off(
            first,
            latest,
            env.operation("old-signoff", actor_name=first.agent_name),
        )


def test_deleted_configured_agent_is_rejected_before_receipt_replay(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    operation = env.operation("start", actor_name=actor.agent_name)
    env.service.start_work(actor, ticket.version, operation)
    snapshot = env.store.load()

    def patch(raw):
        raw["teams"][env.team_id]["agents"] = [
            agent
            for agent in raw["teams"][env.team_id]["agents"]
            if agent["name"] != actor.agent_name
        ]

    env.store.patch(snapshot.revision, patch)
    with pytest.raises(TicketForbidden):
        env.service.start_work(actor, ticket.version, operation)


def test_sign_off_clears_assignment_and_active_run_with_one_event(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("signoff-start", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    assert active.record.events[-1].data["job_id"] == actor.job_id
    before_event_ids = [event.id for event in active.record.events]
    result = env.service.sign_off(
        actor,
        active.version,
        env.operation("signoff", actor_name=actor.agent_name),
    )
    assert result.ticket.assignee is None
    assert result.ticket.active_run is None
    after_event_ids = [event.id for event in result.ticket.events]
    assert after_event_ids[: len(before_event_ids)] == before_event_ids
    assert len(after_event_ids) == len(before_event_ids) + 1


def test_end_work_retains_assignment(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("end-start", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    result = env.service.end_work(
        actor,
        active.version,
        env.operation("end", actor_name=actor.agent_name),
    )
    persisted = env.read(ticket.ref).record
    assert result.ticket.assignee == actor.agent_name
    assert result.ticket.active_run is None
    assert persisted.events[-1].data["job_id"] == actor.job_id


def test_sign_off_replay_returns_original_result_without_second_event(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("replay-signoff-start", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    operation = env.operation("replay-signoff", actor_name=actor.agent_name)
    first = env.service.sign_off(actor, active.version, operation)
    replay = env.service.sign_off(actor, active.version, operation)
    assert replay.replayed is True
    assert replay.ticket == first.ticket
    assert replay.event_id == first.event_id
    stored = env.read(ticket.ref).record
    assert stored.assignee is None
    assert stored.active_run is None
    assert stored.revision == first.ticket.revision
    assert stored.events == first.ticket.events


def _assert_replayed_mutation_preserves_current_record(
    env,
    first_result,
    replay_result,
    current_view,
):
    assert replay_result.replayed is True
    assert replay_result.ticket == first_result.ticket
    assert replay_result.event_id == first_result.event_id
    assert first_result.ticket.ref is not None
    assert replay_result.ticket.ref is not None
    assert env.read(first_result.ticket.ref).record == current_view.record
    assert current_view.record != first_result.ticket
    assert current_view.record.ref == first_result.ticket.ref


def test_assign_replay_returns_original_result_after_legitimate_change(workflow_env):
    env = workflow_env
    ticket = env.create()
    operation = env.operation("replay-assign")
    first = env.service.assign(env.user, ticket.version, "builder", operation)
    env.service.assign(
        env.user,
        env.read(ticket.ref).version,
        None,
        env.operation("advance-assign-clear"),
    )
    current = env.read(ticket.ref)
    replay = env.service.assign(env.user, ticket.version, "builder", operation)
    _assert_replayed_mutation_preserves_current_record(env, first, replay, current)


def test_start_work_replay_returns_original_result_after_legitimate_change(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    operation = env.operation("replay-start", actor_name=actor.agent_name)
    first = env.service.start_work(actor, ticket.version, operation)
    env.service.end_work(
        actor,
        env.read(ticket.ref).version,
        env.operation("advance-start-end", actor_name=actor.agent_name),
    )
    current = env.read(ticket.ref)
    replay = env.service.start_work(actor, ticket.version, operation)
    _assert_replayed_mutation_preserves_current_record(env, first, replay, current)


def test_end_work_replay_returns_original_result_after_legitimate_change(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("end-replay-seed", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    operation = env.operation("replay-end", actor_name=actor.agent_name)
    first = env.service.end_work(actor, active.version, operation)
    env.service.assign(
        env.user,
        env.read(ticket.ref).version,
        "observer",
        env.operation("advance-end-assign"),
    )
    observer = env.agent("observer", "run-b")
    env.service.start_work(
        observer,
        env.read(ticket.ref).version,
        env.operation("advance-end-restart", actor_name=observer.agent_name),
    )
    current = env.read(ticket.ref)
    replay = env.service.end_work(actor, active.version, operation)
    _assert_replayed_mutation_preserves_current_record(env, first, replay, current)


def test_sign_off_replay_returns_original_result_after_legitimate_change(workflow_env):
    env = workflow_env
    ticket = env.create()
    actor = env.agent("builder", "run-a")
    env.service.start_work(
        actor,
        ticket.version,
        env.operation("signoff-replay-seed", actor_name=actor.agent_name),
    )
    active = env.read(ticket.ref)
    operation = env.operation("replay-signoff-after-change", actor_name=actor.agent_name)
    first = env.service.sign_off(actor, active.version, operation)
    env.service.assign(
        env.user,
        env.read(ticket.ref).version,
        "observer",
        env.operation("advance-signoff-assign"),
    )
    current = env.read(ticket.ref)
    replay = env.service.sign_off(actor, active.version, operation)
    _assert_replayed_mutation_preserves_current_record(env, first, replay, current)


def test_update_replay_returns_original_result_after_legitimate_change(workflow_env):
    env = workflow_env
    ticket = env.create(values={"summary": "hello"})
    operation = env.operation("replay-update")
    first = env.service.update(
        env.user,
        ticket.version,
        ticket.patch(description="first update"),
        operation,
    )
    env.service.update(
        env.user,
        env.read(ticket.ref).version,
        env.read(ticket.ref).patch(description="later update"),
        env.operation("advance-update"),
    )
    current = env.read(ticket.ref)
    replay = env.service.update(
        env.user,
        ticket.version,
        ticket.patch(description="first update"),
        operation,
    )
    _assert_replayed_mutation_preserves_current_record(env, first, replay, current)


def test_same_ticket_id_in_another_team_is_distinct(workflow_env):
    env = workflow_env
    support_provider = LocalTicketStorage(env.root_b, clock=env._clock)
    ref = support_provider._ref_for("support", env.workflow_id, "ticket-shared")
    support_provider.create(
        ticket_record(ticket_id="ticket-shared").with_ref(ref),
        env.operation("support-seed", actor_name="support"),
    )
    view = env.service.inspect(UserTicketContext(team_id="support"), ref)
    assert view.ref.team_id == "support"
    assert view.ref.ticket_id == "ticket-shared"
    with pytest.raises(TicketForbidden):
        env.service.inspect(env.user, ref)


# -- trusted Git evidence ---------------------------------------------------


def _git_ticket(env, name: str = "source", policy=None):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

    fixture = create_git_repository(env.tmp_path / name)
    actor, ticket = configure_git_ticket(
        env, fixture, policy or GitPublicationPolicy(mode="local")
    )
    return fixture, actor, ticket


@requires_git
def test_captured_evidence_completes_transition_with_transition_provenance(workflow_env):
    from tests._git_evidence_helpers import capture_request

    env = workflow_env
    fixture, actor, ticket = _git_ticket(env)
    result = env.service.capture_git_evidence(
        actor,
        ticket.version,
        capture_request(fixture),
        env.operation("capture", actor_name="builder"),
    )

    accepted = env.service.transition(
        actor,
        result.version,
        env.transition_request(
            outputs={"summary": "Committed result", "evidence": result.artifact}
        ),
        env.operation("complete-with-evidence", actor_name="builder"),
    )

    assert accepted.ticket.state_id == "done"
    assert accepted.ticket.field_values["evidence"] == result.artifact
    assert accepted.ticket.field_provenance["evidence"].job_id == actor.job_id


@requires_git
def test_later_review_job_may_reuse_the_original_agents_evidence(workflow_env):
    from flowgency.tickets.access import TicketAccessRegistry
    from flowgency.tickets.git_evidence import GitEvidenceManifest
    from tests._git_evidence_helpers import (
        capture_request,
        launch_policy_for,
    )

    env = workflow_env
    fixture, actor, ticket = _git_ticket(env)
    result = env.service.capture_git_evidence(
        actor,
        ticket.version,
        capture_request(fixture),
        env.operation("capture", actor_name="builder"),
    )
    env.service.end_work(
        actor, result.version, env.operation("end", actor_name="builder")
    )

    authority = env.running_job(
        "builder", "review-run", runtime_policy=launch_policy_for(env, "builder")
    )
    registry = TicketAccessRegistry(env.job_store)
    reviewer = registry.open(authority).context
    env.service.start_work(
        reviewer,
        env.read(ticket.ref).version,
        env.operation("review-start", actor_name="reviewer"),
    )

    accepted = env.service.transition(
        reviewer,
        env.read(ticket.ref).version,
        env.transition_request(
            outputs={"summary": "Reviewed result", "evidence": result.artifact}
        ),
        env.operation("review-complete", actor_name="reviewer"),
    )

    assert accepted.ticket.state_id == "done"
    assert accepted.ticket.field_provenance["evidence"].job_id == reviewer.job_id
    retained = env.current_provider().read_artifact(ticket.ref, result.artifact.value)
    manifest = GitEvidenceManifest.model_validate_json(retained.content)
    assert manifest.job_id == actor.job_id
    assert reviewer.job_id != actor.job_id


def _publish_second_evidence_field(env) -> None:
    """Declare a second Git-change field, written only by a later transition."""
    from flowgency.workflows.models import WorkflowDefinition

    source = env.library.inspect(env.blueprint_id)
    document = source.definition.model_dump(mode="json")
    evidence = next(field for field in document["fields"] if field["id"] == "evidence")
    document["fields"].append(
        dict(evidence, id="review_evidence", label="Review evidence")
    )
    document["states"].append(
        {"id": "verified", "name": "Verified", "color": "#7ad7bf"}
    )
    complete = next(
        transition for transition in document["transitions"] if transition["id"] == "complete"
    )
    document["transitions"].append(
        dict(
            complete,
            id="verify",
            name="Verify",
            from_state="done",
            to_state="verified",
            inputs=[],
            outputs=[
                dict(complete["outputs"][0], field_id="review_evidence", required=True)
            ],
            preconditions=[],
            criteria=[],
        )
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        WorkflowDefinition.model_validate(document),
    )


@requires_git
def test_captured_evidence_is_reusable_on_another_field_and_later_transition(
    workflow_env,
):
    from flowgency.tickets.access import TicketAccessRegistry
    from flowgency.tickets.errors import TicketEvidenceInvalid
    from flowgency.tickets.git_evidence import (
        GIT_EVIDENCE_FILENAME,
        GIT_EVIDENCE_MEDIA_TYPE,
        GitEvidenceManifest,
    )
    from tests._git_evidence_helpers import capture_request, launch_policy_for

    env = workflow_env
    fixture, actor, ticket = _git_ticket(env)
    _publish_second_evidence_field(env)
    result = env.service.capture_git_evidence(
        actor,
        env.read(ticket.ref).version,
        capture_request(fixture),
        env.operation("capture", actor_name="builder"),
    )
    env.service.transition(
        actor,
        result.version,
        env.transition_request(
            outputs={"summary": "Committed result", "evidence": result.artifact}
        ),
        env.operation("complete-with-evidence", actor_name="builder"),
    )
    env.service.end_work(
        actor, env.read(ticket.ref).version, env.operation("end", actor_name="builder")
    )

    authority = env.running_job(
        "builder", "verify-run", runtime_policy=launch_policy_for(env, "builder")
    )
    reviewer = TicketAccessRegistry(env.job_store).open(authority).context
    env.service.start_work(
        reviewer,
        env.read(ticket.ref).version,
        env.operation("verify-start", actor_name="reviewer"),
    )

    accepted = env.service.transition(
        reviewer,
        env.read(ticket.ref).version,
        env.transition_request(
            transition_id="verify", outputs={"review_evidence": result.artifact}
        ),
        env.operation("verify", actor_name="reviewer"),
    )

    assert accepted.ticket.state_id == "verified"
    assert accepted.ticket.field_values["review_evidence"] == result.artifact
    assert accepted.ticket.field_provenance["review_evidence"].job_id == reviewer.job_id
    assert accepted.ticket.field_provenance["evidence"].job_id == actor.job_id
    assert reviewer.job_id != actor.job_id
    captured = next(
        event
        for event in accepted.ticket.events
        if event.kind == "git-evidence-captured"
    )
    assert captured.data["capture"]["transition_id"] == "complete"
    assert captured.data["capture"]["field_id"] == "evidence"
    assert captured.data["capture"]["job_id"] == actor.job_id
    content = env.current_provider().read_artifact(
        ticket.ref, result.artifact.value
    ).content
    assert GitEvidenceManifest.model_validate_json(content).job_id == actor.job_id

    foreign = env.create(title="Foreign", values={"verdict": True, "summary": "Foreign"})
    env.service.start_work(
        reviewer, foreign.version, env.operation("foreign-start", actor_name="reviewer")
    )
    uploaded = env.service.publish_artifact(
        reviewer,
        env.read(foreign.ref).version,
        GIT_EVIDENCE_FILENAME,
        GIT_EVIDENCE_MEDIA_TYPE,
        content,
    )
    before = env.read(foreign.ref).record
    with pytest.raises(TicketEvidenceInvalid):
        env.service.transition(
            reviewer,
            env.read(foreign.ref).version,
            env.transition_request(
                outputs={"summary": "Borrowed", "evidence": uploaded}
            ),
            env.operation("foreign-transition", actor_name="reviewer"),
        )
    assert env.read(foreign.ref).record == before


@requires_git
def test_uploaded_evidence_without_a_receipt_is_rejected_on_every_write_path(
    workflow_env,
):
    from flowgency.tickets.errors import TicketEvidenceInvalid
    from flowgency.tickets.git_evidence import (
        GIT_EVIDENCE_FILENAME,
        GIT_EVIDENCE_MEDIA_TYPE,
    )
    from tests._git_evidence_helpers import capture_request

    env = workflow_env
    fixture, actor, ticket = _git_ticket(env)
    result = env.service.capture_git_evidence(
        actor,
        ticket.version,
        capture_request(fixture),
        env.operation("capture", actor_name="builder"),
    )
    content = env.current_provider().read_artifact(
        ticket.ref, result.artifact.value
    ).content

    other = env.create(title="Other", values={"verdict": True, "summary": "Other"})
    env.service.start_work(
        actor, other.version, env.operation("other-start", actor_name="builder")
    )
    uploaded = env.service.publish_artifact(
        actor,
        env.read(other.ref).version,
        GIT_EVIDENCE_FILENAME,
        GIT_EVIDENCE_MEDIA_TYPE,
        content,
    )
    before = env.read(other.ref).record

    with pytest.raises(TicketEvidenceInvalid):
        env.service.transition(
            actor,
            env.read(other.ref).version,
            env.transition_request(
                outputs={"summary": "Borrowed", "evidence": uploaded}
            ),
            env.operation("borrow-transition", actor_name="builder"),
        )
    assert env.read(other.ref).record == before

    with pytest.raises(TicketEvidenceInvalid):
        env.service.update(
            actor,
            env.read(other.ref).version,
            TicketPatch(field_values={"evidence": uploaded}),
            env.operation("borrow-update", actor_name="builder"),
        )
    assert env.read(other.ref).record == before

    with pytest.raises(TicketEvidenceInvalid):
        env.service.update(
            actor,
            env.read(other.ref).version,
            TicketPatch(
                field_values={
                    "evidence": ArtifactRef(
                        kind="url", value="https://example.invalid/patch"
                    )
                }
            ),
            env.operation("borrow-url", actor_name="builder"),
        )
    assert env.read(other.ref).record == before


@requires_git
def test_creation_cannot_cite_git_evidence(workflow_env):
    from flowgency.tickets.errors import TicketEvidenceInvalid

    env = workflow_env
    _git_ticket(env)

    with pytest.raises(TicketEvidenceInvalid):
        env.create(
            values={
                "verdict": True,
                "evidence": ArtifactRef(kind="id", value="a" * 64),
            }
        )


def _stored_artifacts(env, ref) -> list:
    directory = env.current_provider()._artifact_path(ref, "a" * 64).parent
    return sorted(directory.glob("*")) if directory.exists() else []


def _assert_capture_left_nothing(env, ticket, before, operation, workspace_snapshot):
    from tests._git_evidence_helpers import snapshot_repository

    record = env.read(ticket.ref).record
    assert record == before
    assert all(event.kind != "git-evidence-captured" for event in record.events)
    assert env.current_provider().receipt(ticket.ref, operation) is None
    assert _stored_artifacts(env, ticket.ref) == []
    workspace = env.store.load().config.teams[env.team_id].workspace_path
    assert snapshot_repository(workspace) == workspace_snapshot


@requires_git
def test_capture_is_denied_when_the_launch_policy_cannot_read_a_selected_path(
    workflow_env,
):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.jobs.models import RuntimePolicySnapshot
    from flowgency.tickets.errors import TicketForbidden
    from tests._git_evidence_helpers import (
        capture_request,
        configure_git_ticket,
        create_git_repository,
        snapshot_repository,
    )

    env = workflow_env
    fixture = create_git_repository(env.tmp_path / "source")
    denied_launch = RuntimePolicySnapshot(
        timeout=30,
        mode="unrestricted",
        rules=(
            {"path": None, "tools": None},
            {"path": str(fixture.root / "result.txt"), "tools": ["search"]},
        ),
    )
    actor, ticket = configure_git_ticket(
        env,
        fixture,
        GitPublicationPolicy(mode="local"),
        launch_policy=denied_launch,
    )
    before = env.read(ticket.ref).record
    before_source = snapshot_repository(fixture.root)
    operation = env.operation("denied-launch-capture", actor_name="builder")

    with pytest.raises(TicketForbidden) as denied:
        env.service.capture_git_evidence(
            actor, ticket.version, capture_request(fixture), operation
        )

    assert denied.value.code == "git-evidence-path-denied"
    _assert_capture_left_nothing(env, ticket, before, operation, before_source)


@requires_git
def test_capture_is_denied_when_the_current_policy_cannot_read_a_selected_path(
    workflow_env,
):
    from flowgency.tickets.errors import TicketForbidden
    from tests._git_evidence_helpers import (
        GitTestRepository,
        capture_request,
        git_command,
        snapshot_repository,
    )

    env = workflow_env
    fixture, actor, ticket = _git_ticket(env)
    nested = fixture.root / "docs"
    nested.mkdir()
    (nested / "note.txt").write_bytes(b"noted\n")
    git_command(fixture.root, "add", "--", "docs/note.txt")
    git_command(fixture.root, "commit", "-m", "test: add a nested note")
    end_commit = git_command(fixture.root, "rev-parse", "HEAD").decode().strip()
    ranged = GitTestRepository(fixture.root, fixture.base_commit, end_commit)

    snapshot = env.store.load()
    env.store.patch(
        snapshot.revision,
        lambda raw: raw["teams"][env.team_id].__setitem__(
            "permissions",
            {
                "mode": "unrestricted",
                "rules": [{"tools": None}, {"path": str(nested), "tools": ["search"]}],
            },
        ),
    )
    before = env.read(ticket.ref).record
    before_source = snapshot_repository(fixture.root)
    operation = env.operation("denied-current-capture", actor_name="builder")

    with pytest.raises(TicketForbidden) as refused:
        env.service.capture_git_evidence(
            actor, ticket.version, capture_request(ranged), operation
        )

    assert refused.value.code == "git-evidence-path-denied"
    _assert_capture_left_nothing(env, ticket, before, operation, before_source)