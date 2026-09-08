"""Compatibility and non-transfer guards for blueprint/storage changes."""

from __future__ import annotations

import pytest

from flowgency.configuration.issues import ValidationFailed
from flowgency.configuration.store import ConfigConflictError
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.configuration import (
    WorkflowConfigurationService,
    WorkflowInstancePatch,
)
from flowgency.workflows.models import ContractError


def _settings_patch(flowgency, **overrides):
    from flowgency.configuration.patches import FlowgencySettingsPatch

    values = dict(
        title=flowgency.title,
        default_team=flowgency.default_team,
        ai_backend=flowgency.ai_backend,
        theme="",
        dispatch_interval=15,
        agent_library=str(flowgency.agent_library),
        compilation_cache=str(flowgency.compilation_cache),
        memory_store=str(flowgency.memory_store),
        prompt_store=str(flowgency.prompt_store),
        workflow_library=str(flowgency.workflow_library),
    )
    values.update(overrides)
    return FlowgencySettingsPatch(**values)



def _narrow_blueprint() -> dict:
    """A valid blueprint that no longer declares the seeded ``review`` state."""
    return {
        "schema_version": 1,
        "id": "narrow",
        "name": "Narrow",
        "description": "Only the terminal state",
        "initial_state": "done",
        "states": [{"id": "done", "name": "Done", "color": "#7ad7bf"}],
        "fields": [],
        "transitions": [],
    }


def test_state_removal_cannot_rewrite_existing_ticket(workflow_env):
    env = workflow_env
    ref = env.seed_ticket(state_id="review")
    original = env.provider.read(ref)
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(
        update={
            "initial_state": "done",
            "states": tuple(
                state for state in source.definition.states if state.id != "review"
            ),
            "transitions": (),
        }
    )
    with pytest.raises(ValidationFailed):
        env.configuration_service.save_blueprint(
            env.store.load().revision, "delivery", source.digest, candidate
        )
    assert env.provider.read(ref) == original
    assert env.library.inspect("delivery").digest == source.digest


def test_field_type_change_is_incompatible_with_existing_value(workflow_env):
    env = workflow_env
    env.seed_ticket(state_id="review", values={"summary": "hello"})
    source = env.library.inspect("delivery")
    fields = tuple(
        field.model_copy(update={"type": "number"})
        if field.id == "summary"
        else field
        for field in source.definition.fields
    )
    # Drop the transition that references summary so only the field type changes.
    candidate = source.definition.model_copy(
        update={"fields": fields, "transitions": ()}
    )
    with pytest.raises(ValidationFailed):
        env.configuration_service.save_blueprint(
            env.store.load().revision, "delivery", source.digest, candidate
        )


def test_unreferenced_historical_value_is_preserved(workflow_env):
    env = workflow_env
    env.seed_ticket(state_id="review", values={"retired_note": "kept"})
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(update={"description": "no retired field"})
    published = env.configuration_service.save_blueprint(
        env.store.load().revision, "delivery", source.digest, candidate
    )
    assert published.definition.description == "no retired field"


def test_unavailable_storage_blocks_publication(workflow_env):
    import shutil

    env = workflow_env
    source = env.library.inspect("delivery")
    # A referencing storage that cannot be read prevents proof of compatibility.
    shutil.rmtree(env.root_a)
    candidate = source.definition.model_copy(update={"description": "unprovable"})
    with pytest.raises(ValidationFailed):
        env.configuration_service.save_blueprint(
            env.store.load().revision, "delivery", source.digest, candidate
        )


def test_storage_switch_is_not_a_transfer(workflow_env):
    env = workflow_env
    old_ref = env.seed_ticket()
    old_record = env.provider.read(old_ref)
    env.set_storage_root(env.root_b)
    assert env.current_provider().list("newsletter", "board-a") == ()
    assert env.provider.read(old_ref) == old_record
    env.set_storage_root(env.root_a)
    assert env.current_provider().read(old_ref) == old_record


def test_save_instance_rejects_incompatible_blueprint_change(workflow_env):
    env = workflow_env
    ref = env.seed_ticket(state_id="review")
    original = env.provider.read(ref)
    env.write_blueprint("narrow", _narrow_blueprint())
    before = env.store.load()
    with pytest.raises(ValidationFailed):
        env.configuration_service.save_instance(
            before.revision,
            "newsletter",
            "board-a",
            WorkflowInstancePatch(
                name="Board A",
                blueprint="narrow",
                integration="local",
                integration_config={"root": str(env.root_a)},
            ),
        )
    after = env.store.load()
    assert after.revision == before.revision
    assert env.provider.read(ref) == original
    assert (
        after.config.teams["newsletter"].workflows["board-a"].blueprint == "delivery"
    )


def test_save_instance_allows_compatible_blueprint_change(workflow_env):
    env = workflow_env
    env.seed_ticket(state_id="review")
    env.write_blueprint("narrow", _narrow_blueprint())
    # Move the ticket out of ``review`` first so ``narrow`` can interpret it.
    published = _narrow_blueprint()
    published["states"].append({"id": "review", "name": "Review", "color": "#ebc77c"})
    env.write_blueprint("narrow", published)
    before = env.store.load()
    env.configuration_service.save_instance(
        before.revision,
        "newsletter",
        "board-a",
        WorkflowInstancePatch(
            name="Board A",
            blueprint="narrow",
            integration="local",
            integration_config={"root": str(env.root_a)},
        ),
    )
    after = env.store.load()
    assert after.config.teams["newsletter"].workflows["board-a"].blueprint == "narrow"


def test_create_with_missing_blueprint_leaves_no_root(workflow_env):
    env = workflow_env
    fresh_root = env.tmp_path / "tickets-c"
    before = env.store.load()
    with pytest.raises(ContractError):
        env.configuration_service.save_instance(
            before.revision,
            "newsletter",
            "board-c",
            WorkflowInstancePatch(
                name="Board C",
                blueprint="ghost",
                integration="local",
                integration_config={"root": str(fresh_root)},
            ),
            create=True,
        )
    after = env.store.load()
    assert after.revision == before.revision
    assert "board-c" not in after.config.teams["newsletter"].workflows
    assert not fresh_root.exists()


def test_rebind_to_unavailable_destination_is_rejected(workflow_env):
    env = workflow_env
    env.seed_ticket(state_id="review")
    missing = env.tmp_path / "tickets-missing"
    before = env.store.load()
    with pytest.raises(ValidationFailed):
        env.configuration_service.save_instance(
            before.revision,
            "newsletter",
            "board-a",
            WorkflowInstancePatch(
                name="Board A",
                blueprint="delivery",
                integration="local",
                integration_config={"root": str(missing)},
            ),
        )
    after = env.store.load()
    assert after.revision == before.revision
    assert (
        after.config.teams["newsletter"].workflows["board-a"].integration_config[
            "root"
        ]
        == str(env.root_a)
    )
    assert not missing.exists()


def test_create_prepares_root_after_validation(workflow_env):
    env = workflow_env
    fresh_root = env.tmp_path / "tickets-c"
    before = env.store.load()
    env.configuration_service.save_instance(
        before.revision,
        "newsletter",
        "board-c",
        WorkflowInstancePatch(
            name="Board C",
            blueprint="delivery",
            integration="local",
            integration_config={"root": str(fresh_root)},
        ),
        create=True,
    )
    after = env.store.load()
    assert "board-c" in after.config.teams["newsletter"].workflows
    assert fresh_root.is_dir()


def test_save_blueprint_rejects_config_change_after_compatibility(workflow_env):
    env = workflow_env
    env.seed_ticket(state_id="review")
    source = env.library.inspect("delivery")
    original_digest = source.digest
    from flowgency.configuration.patches import patch_flowgency_settings

    def racing_factory(binding):
        storage = resolve_storage(binding)

        class Racing:
            def list(self, team_id, workflow_id):
                records = storage.list(team_id, workflow_id)
                # An external writer advances the config after the compat read.
                snap = env.store.load()
                patch_flowgency_settings(
                    env.store,
                    snap.revision,
                    _settings_patch(snap.config.flowgency, title="Raced"),
                )
                return records

        return Racing()

    service = WorkflowConfigurationService(env.store, env.library, racing_factory)
    candidate = source.definition.model_copy(update={"description": "raced"})
    with pytest.raises(ConfigConflictError):
        service.save_blueprint(
            env.store.load().revision, "delivery", source.digest, candidate
        )
    assert env.library.inspect("delivery").digest == original_digest


def test_save_instance_rejects_blueprint_change_after_compatibility(workflow_env):
    env = workflow_env
    env.seed_ticket(state_id="review")
    before = env.store.load()

    def racing_factory(binding):
        storage = resolve_storage(binding)

        class Racing:
            def list(self, team_id, workflow_id):
                records = storage.list(team_id, workflow_id)
                # An external editor changes the blueprint source mid-flight.
                path = env.library.source_path("delivery")
                path.write_text(
                    path.read_text(encoding="utf-8") + "\n# edited\n",
                    encoding="utf-8",
                )
                return records

        return Racing()

    service = WorkflowConfigurationService(env.store, env.library, racing_factory)
    with pytest.raises(ConfigConflictError):
        service.save_instance(
            before.revision,
            "newsletter",
            "board-a",
            WorkflowInstancePatch(
                name="Board A",
                blueprint="delivery",
                integration="local",
                integration_config={"root": str(env.root_a)},
            ),
        )
    assert env.store.load().revision == before.revision


def test_blueprint_rename_roundtrip_preserves_ids_and_tickets(workflow_env):
    env = workflow_env
    ref = env.seed_ticket(state_id="review")
    original_ticket = env.provider.read(ref)
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(update={"name": "Renamed Delivery"})
    published = env.configuration_service.save_blueprint(
        env.store.load().revision, "delivery", source.digest, candidate
    )
    assert published.definition.name == "Renamed Delivery"
    assert published.digest != source.digest
    assert published.definition.id == source.definition.id == "delivery"
    assert published.definition.initial_state == source.definition.initial_state
    assert [s.id for s in published.definition.states] == [
        s.id for s in source.definition.states
    ]
    assert [f.id for f in published.definition.fields] == [
        f.id for f in source.definition.fields
    ]
    assert [t.id for t in published.definition.transitions] == [
        t.id for t in source.definition.transitions
    ]
    assert env.provider.read(ref) == original_ticket
    assert env.library.inspect("delivery").digest == published.digest


def test_state_rename_roundtrip_preserves_references(workflow_env):
    from flowgency.workflows.editing import rename_state

    env = workflow_env
    ref = env.seed_ticket(state_id="review")
    original_ticket = env.provider.read(ref)
    source = env.library.inspect("delivery")
    renamed = rename_state(source.definition, "review", "In review").definition
    published = env.configuration_service.save_blueprint(
        env.store.load().revision, "delivery", source.digest, renamed
    )
    assert published.definition.state("review").name == "In review"
    assert published.definition.initial_state == "review"
    assert published.digest != source.digest
    assert [t.from_state for t in published.definition.transitions] == [
        t.from_state for t in source.definition.transitions
    ]
    assert [t.to_state for t in published.definition.transitions] == [
        t.to_state for t in source.definition.transitions
    ]
    assert env.provider.read(ref) == original_ticket


def test_transition_rename_roundtrip_preserves_references(workflow_env):
    env = workflow_env
    ref = env.seed_ticket(state_id="review")
    original_ticket = env.provider.read(ref)
    source = env.library.inspect("delivery")
    renamed_transitions = tuple(
        t.model_copy(update={"name": "Finish"}) if t.id == "complete" else t
        for t in source.definition.transitions
    )
    candidate = source.definition.model_copy(
        update={"transitions": renamed_transitions}
    )
    published = env.configuration_service.save_blueprint(
        env.store.load().revision, "delivery", source.digest, candidate
    )
    complete = published.definition.transition("complete")
    assert complete.name == "Finish"
    assert complete.from_state == "review"
    assert complete.to_state == "done"
    assert [u.field_id for u in complete.inputs] == ["verdict"]
    assert [u.field_id for u in complete.outputs] == ["summary"]
    assert complete.preconditions[0].field_id == "verdict"
    assert published.digest != source.digest
    assert env.provider.read(ref) == original_ticket



