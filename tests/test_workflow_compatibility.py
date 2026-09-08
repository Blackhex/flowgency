"""Compatibility and non-transfer guards for blueprint/storage changes."""

from __future__ import annotations

import pytest

from flowgency.configuration.issues import ValidationFailed


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
    env.seed_ticket(state_id="review", values={"legacy_note": "kept"})
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(update={"description": "no legacy field"})
    published = env.configuration_service.save_blueprint(
        env.store.load().revision, "delivery", source.digest, candidate
    )
    assert published.definition.description == "no legacy field"


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
