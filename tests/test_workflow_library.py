"""Current-source blueprint inspection and publication guards."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from flowgency.configuration.store import ConfigConflictError
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.configuration import WorkflowConfigurationService
from flowgency.workflows.library import WorkflowLibrary
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
