"""Current-source blueprint inspection and publication guards."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from flowgency.configuration.store import ConfigConflictError
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.configuration import WorkflowConfigurationService
from flowgency.workflows.library import WorkflowLibrary
from flowgency.workflows.locking import blueprint_lock_paths
from flowgency.workflows.models import ContractError, MAX_BLUEPRINT_SOURCE_BYTES

from tests._ticket_helpers import delivery_definition


def _write_blueprint(library_root: Path, blueprint_id: str, definition: dict) -> Path:
    directory = library_root / blueprint_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "workflow.yaml"
    path.write_text(
        yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def library_root(tmp_path):
    root = tmp_path / "workflow-library"
    root.mkdir()
    return root


def test_inspect_returns_current_validated_snapshot(library_root):
    _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")
    assert snapshot.definition.id == "delivery"
    assert snapshot.source_path == library_root / "delivery" / "workflow.yaml"
    assert len(snapshot.digest) == 64


def test_inspect_missing_blueprint_raises(library_root):
    library = WorkflowLibrary(library_root)
    with pytest.raises(ContractError):
        library.inspect("delivery")


def test_inspect_rejects_unsafe_blueprint_id(library_root):
    library = WorkflowLibrary(library_root)
    with pytest.raises(ContractError):
        library.inspect("../escape")


def test_inspect_rejects_oversized_source(library_root):
    definition = delivery_definition()
    definition["description"] = "x" * (MAX_BLUEPRINT_SOURCE_BYTES + 1)
    _write_blueprint(library_root, "delivery", definition)
    library = WorkflowLibrary(library_root)
    with pytest.raises(ContractError):
        library.inspect("delivery")


def test_list_isolates_one_invalid_blueprint(library_root):
    _write_blueprint(library_root, "delivery", delivery_definition())
    broken = library_root / "broken"
    broken.mkdir()
    (broken / "workflow.yaml").write_text("not: [a, valid, workflow", encoding="utf-8")
    library = WorkflowLibrary(library_root)
    listing = {item.blueprint_id: item for item in library.list()}
    assert listing["delivery"].snapshot is not None
    assert listing["delivery"].issues == ()
    assert listing["broken"].snapshot is None
    assert listing["broken"].issues


def test_list_isolates_unreadable_blueprint_entry(library_root):
    _write_blueprint(library_root, "delivery", delivery_definition())
    unreadable = library_root / "unreadable"
    unreadable.mkdir()
    # A directory where the source file is expected raises OSError on read.
    (unreadable / "workflow.yaml").mkdir()
    library = WorkflowLibrary(library_root)
    listing = {item.blueprint_id: item for item in library.list()}
    assert listing["delivery"].snapshot is not None
    assert listing["unreadable"].snapshot is None
    assert listing["unreadable"].issues
    # The isolated issue must not leak the private filesystem path.
    assert str(library_root) not in listing["unreadable"].issues[0]


def test_library_for_follows_changed_root(workflow_env, tmp_path):
    env = workflow_env
    from flowgency.configuration.patches import (
        FlowgencySettingsPatch,
        patch_flowgency_settings,
    )

    new_root = tmp_path / "workflow-library-next"
    (new_root / "delivery").mkdir(parents=True)
    (new_root / "delivery" / "workflow.yaml").write_text(
        yaml.safe_dump(delivery_definition(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    snapshot = env.store.load()
    flowgency = snapshot.config.flowgency
    patch_flowgency_settings(
        env.store,
        snapshot.revision,
        FlowgencySettingsPatch(
            title=flowgency.title,
            default_team=flowgency.default_team,
            ai_backend=flowgency.ai_backend,
            theme="",
            dispatch_interval=15,
            agent_library=str(flowgency.agent_library),
            compilation_cache=str(flowgency.compilation_cache),
            memory_store=str(flowgency.memory_store),
            prompt_store=str(flowgency.prompt_store),
            workflow_library=str(new_root),
        ),
    )
    changed = env.store.load()
    library = env.configuration_service.library_for(changed)
    assert Path(library.root).resolve() == new_root.resolve()



def test_write_candidate_rejects_external_change_at_final_check(library_root):
    path = _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")

    changed = delivery_definition()
    changed["description"] = "edited by an external tool"
    path.write_text(
        yaml.safe_dump(changed, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    candidate = snapshot.definition.model_copy(update={"description": "mine"})
    with pytest.raises(ConfigConflictError):
        library.write_candidate("delivery", snapshot.digest, candidate)


def test_write_candidate_publishes_when_bytes_unchanged(library_root):
    _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")
    candidate = snapshot.definition.model_copy(update={"description": "republished"})
    published = library.write_candidate("delivery", snapshot.digest, candidate)
    assert published.definition.description == "republished"
    assert library.inspect("delivery").digest == published.digest


def test_write_candidate_rejects_oversized_candidate(library_root):
    path = _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")
    before = path.read_bytes()
    oversized = snapshot.definition.model_copy(
        update={"description": "x" * (MAX_BLUEPRINT_SOURCE_BYTES + 1)}
    )
    with pytest.raises(ContractError):
        library.write_candidate("delivery", snapshot.digest, oversized)
    assert path.read_bytes() == before


def test_write_candidate_rejects_invalid_candidate(library_root):
    path = _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")
    before = path.read_bytes()
    # Drop a field still referenced by the ``complete`` transition input.
    invalid = snapshot.definition.model_copy(
        update={
            "fields": tuple(
                f for f in snapshot.definition.fields if f.id != "verdict"
            )
        }
    )
    with pytest.raises(ValidationError):
        library.write_candidate("delivery", snapshot.digest, invalid)
    assert path.read_bytes() == before


def test_write_candidate_rejects_identity_change(library_root):
    path = _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")
    before = path.read_bytes()
    renamed = snapshot.definition.model_copy(update={"id": "other"})
    with pytest.raises(ContractError):
        library.write_candidate("delivery", snapshot.digest, renamed)
    assert path.read_bytes() == before


def test_write_candidate_no_byte_change_returns_committed_snapshot(library_root):
    _write_blueprint(library_root, "delivery", delivery_definition())
    library = WorkflowLibrary(library_root)
    snapshot = library.inspect("delivery")
    first = library.write_candidate("delivery", snapshot.digest, snapshot.definition)
    second = library.write_candidate("delivery", first.digest, first.definition)
    assert second.digest == first.digest
    assert library.inspect("delivery").digest == second.digest



def test_service_publishes_compatible_change(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(update={"description": "clarified"})
    published = env.configuration_service.save_blueprint(
        env.store.load().revision, "delivery", source.digest, candidate
    )
    assert published.definition.description == "clarified"
    assert env.library.inspect("delivery").digest == published.digest


def test_service_rejects_stale_expected_digest(workflow_env):
    env = workflow_env
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(update={"description": "clarified"})
    with pytest.raises(ConfigConflictError):
        env.configuration_service.save_blueprint(
            env.store.load().revision, "delivery", "deadbeef", candidate
        )


def test_blueprint_lock_paths_rejects_traversal(library_root):
    with pytest.raises(ContractError):
        blueprint_lock_paths(library_root, ("../evil",))


def test_blueprint_lock_paths_missing_library_is_not_created(tmp_path):
    missing = tmp_path / "no-such-library"
    with pytest.raises(ContractError):
        blueprint_lock_paths(missing, ("delivery",))
    assert not missing.exists()


def test_blueprint_lock_paths_rejects_reparse_lock_dir(library_root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    lock_dir = library_root / ".locks"
    try:
        lock_dir.symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted in this environment")
    with pytest.raises(ContractError):
        blueprint_lock_paths(library_root, ("delivery",))
