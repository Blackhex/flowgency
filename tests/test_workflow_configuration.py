from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from flowgency.configuration.models import validate_config
from flowgency.configuration.store import ConfigConflictError, ConfigStore


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def workflow_paths(config_paths):
    library = config_paths["config_dir"] / "workflow-library"
    tickets = config_paths["config_dir"] / "tickets"
    library.mkdir(parents=True, exist_ok=True)
    tickets.mkdir(parents=True, exist_ok=True)
    return {"library": library, "tickets": tickets}


@pytest.fixture
def config_store(tmp_path, raw_config, workflow_paths):
    raw = deepcopy(raw_config)
    raw["flowgency"]["workflow_library"] = str(workflow_paths["library"])
    return ConfigStore(_write_yaml(tmp_path / "config.yaml", raw))


@pytest.fixture
def configured_store(config_store, workflow_paths):
    from flowgency.workflows.configuration import (
        WorkflowInstancePatch,
        patch_workflow_instance,
    )

    snapshot = config_store.load()
    patch_workflow_instance(
        config_store,
        snapshot.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch(
            name="Delivery",
            blueprint="blueprint-one",
            integration="local",
            integration_config={"root": str(workflow_paths["tickets"])},
        ),
        create=True,
    )
    return config_store


@pytest.fixture
def configured_snapshot(configured_store):
    return configured_store.load()


def test_workflow_patch_preserves_agents_and_rejects_stale_revision(
    config_store, workflow_paths
):
    from flowgency.workflows.configuration import (
        WorkflowInstancePatch,
        patch_workflow_instance,
    )

    snapshot = config_store.load()
    patch = WorkflowInstancePatch(
        name="Delivery",
        blueprint="blueprint-one",
        integration="local",
        integration_config={"root": str(workflow_paths["tickets"])},
    )
    updated = patch_workflow_instance(
        config_store,
        snapshot.revision,
        "newsletter",
        "workflow-one",
        patch,
        create=True,
    )
    assert (
        updated.raw["teams"]["newsletter"]["agents"]
        == snapshot.raw["teams"]["newsletter"]["agents"]
    )
    with pytest.raises(ConfigConflictError):
        patch_workflow_instance(
            config_store, snapshot.revision, "newsletter", "workflow-one", patch
        )


def test_display_name_is_not_storage_identity(configured_snapshot):
    from flowgency.workflows.configuration import (
        WorkflowInstancePatch,
        patch_workflow_instance,
        resolve_workflow_binding,
    )

    def rename_workflow_fixture(snapshot, new_name):
        workflow = snapshot.config.teams["newsletter"].workflows["workflow-one"]
        return patch_workflow_instance(
            ConfigStore(snapshot.path),
            snapshot.revision,
            "newsletter",
            "workflow-one",
            WorkflowInstancePatch(
                name=new_name,
                blueprint=workflow.blueprint,
                integration=workflow.integration,
                integration_config=dict(workflow.integration_config),
            ),
        )

    before = resolve_workflow_binding(configured_snapshot, "newsletter", "workflow-one")
    renamed_snapshot = rename_workflow_fixture(configured_snapshot, "Renamed")
    after = resolve_workflow_binding(renamed_snapshot, "newsletter", "workflow-one")
    assert after.storage.binding_id == before.storage.binding_id
    assert after.context_digest == before.context_digest


def test_provider_switch_and_return_restores_binding_but_not_context(
    configured_snapshot, tmp_path
):
    from flowgency.workflows.configuration import (
        WorkflowInstancePatch,
        patch_workflow_instance,
        resolve_workflow_binding,
    )

    before = resolve_workflow_binding(configured_snapshot, "newsletter", "workflow-one")
    original_root = configured_snapshot.config.teams["newsletter"].workflows[
        "workflow-one"
    ].integration_config["root"]
    other_root = tmp_path / "tickets-other"
    other_root.mkdir()
    store = ConfigStore(configured_snapshot.path)

    switched = patch_workflow_instance(
        store,
        configured_snapshot.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch(
            name="Delivery",
            blueprint="blueprint-one",
            integration="local",
            integration_config={"root": str(other_root)},
        ),
    )
    returned = patch_workflow_instance(
        store,
        switched.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch(
            name="Delivery",
            blueprint="blueprint-one",
            integration="local",
            integration_config={"root": str(original_root)},
        ),
    )
    after = resolve_workflow_binding(returned, "newsletter", "workflow-one")
    assert after.storage.binding_id == before.storage.binding_id
    assert after.context_digest != before.context_digest


def test_blueprint_change_updates_context_not_binding(configured_snapshot):
    from flowgency.workflows.configuration import (
        WorkflowInstancePatch,
        patch_workflow_instance,
        resolve_workflow_binding,
    )

    before = resolve_workflow_binding(configured_snapshot, "newsletter", "workflow-one")
    workflow = configured_snapshot.config.teams["newsletter"].workflows["workflow-one"]
    updated = patch_workflow_instance(
        ConfigStore(configured_snapshot.path),
        configured_snapshot.revision,
        "newsletter",
        "workflow-one",
        WorkflowInstancePatch(
            name="Delivery",
            blueprint="blueprint-two",
            integration="local",
            integration_config=dict(workflow.integration_config),
        ),
    )
    after = resolve_workflow_binding(updated, "newsletter", "workflow-one")
    assert after.storage.binding_id == before.storage.binding_id
    assert after.context_digest != before.context_digest


def test_current_shape_without_workflows_loads_without_library(tmp_path, raw_config):
    store = ConfigStore(_write_yaml(tmp_path / "config.yaml", deepcopy(raw_config)))
    snapshot = store.load()
    assert snapshot.config.flowgency.workflow_library is None
    for team in snapshot.config.teams.values():
        assert team.workflows == {}


def _workflow_config(raw_config, workflow_paths, *, workflow=None, library=True):
    raw = deepcopy(raw_config)
    if library:
        raw["flowgency"]["workflow_library"] = str(workflow_paths["library"])
    entry = {
        "name": "Delivery",
        "blueprint": "blueprint-one",
        "integration": "local",
        "integration_config": {"root": str(workflow_paths["tickets"])},
    }
    if workflow:
        entry.update(workflow)
    raw["teams"]["newsletter"]["workflows"] = {"workflow-one": entry}
    return raw


def test_workflow_without_library_is_rejected(tmp_path, raw_config, workflow_paths):
    raw = _workflow_config(raw_config, workflow_paths, library=False)
    issues = validate_config(raw, tmp_path / "config.yaml")
    assert any(issue.field == "workflow_library" for issue in issues)


def test_workflow_with_wrong_provider_is_rejected(tmp_path, raw_config, workflow_paths):
    raw = _workflow_config(
        raw_config, workflow_paths, workflow={"integration": "github"}
    )
    issues = validate_config(raw, tmp_path / "config.yaml")
    assert any(issue.code == "invalid-workflow-provider" for issue in issues)


def test_workflow_with_unknown_provider_key_is_rejected(
    tmp_path, raw_config, workflow_paths
):
    raw = _workflow_config(
        raw_config,
        workflow_paths,
        workflow={
            "integration_config": {
                "root": str(workflow_paths["tickets"]),
                "token": "secret",
            }
        },
    )
    issues = validate_config(raw, tmp_path / "config.yaml")
    assert any(issue.code == "invalid-workflow-provider" for issue in issues)


def test_workflow_root_overlapping_workspace_is_rejected(
    tmp_path, raw_config, workflow_paths, config_paths
):
    from flowgency.configuration.models import parse_config
    from flowgency.configuration.paths import validate_resolved_paths

    raw = _workflow_config(
        raw_config,
        workflow_paths,
        workflow={"integration_config": {"root": str(config_paths["workspace_path"])}},
    )
    parsed = parse_config(raw, tmp_path / "config.yaml")
    issues = validate_resolved_paths(parsed.resolved)
    assert any(issue.code == "unsafe-path-overlap" for issue in issues)


def test_workflow_traversal_id_is_rejected(tmp_path, raw_config, workflow_paths):
    raw = deepcopy(raw_config)
    raw["flowgency"]["workflow_library"] = str(workflow_paths["library"])
    raw["teams"]["newsletter"]["workflows"] = {
        "../evil": {
            "name": "Delivery",
            "blueprint": "blueprint-one",
            "integration": "local",
            "integration_config": {"root": str(workflow_paths["tickets"])},
        }
    }
    issues = validate_config(raw, tmp_path / "config.yaml")
    assert any(issue.code == "invalid-workflow-name" for issue in issues)


def test_settings_save_preserves_workflow_library_and_generation(configured_store):
    from flowgency.configuration.patches import (
        FlowgencySettingsPatch,
        patch_flowgency_settings,
    )

    snapshot = configured_store.load()
    library = snapshot.config.flowgency.workflow_library
    generation = snapshot.config.teams["newsletter"].workflows[
        "workflow-one"
    ].context_generation
    updated = patch_flowgency_settings(
        configured_store,
        snapshot.revision,
        FlowgencySettingsPatch(
            title="Renamed App",
            default_team="newsletter",
            ai_backend="claude-code",
            theme="",
            dispatch_interval=15,
            agent_library=str(snapshot.config.flowgency.agent_library),
            compilation_cache=str(snapshot.config.flowgency.compilation_cache),
            memory_store=str(snapshot.config.flowgency.memory_store),
            prompt_store=str(snapshot.config.flowgency.prompt_store),
        ),
    )
    assert updated.config.flowgency.workflow_library == library
    assert (
        updated.config.teams["newsletter"].workflows["workflow-one"].context_generation
        == generation
    )


def test_settings_library_change_advances_generation(configured_store, config_paths):
    from flowgency.configuration.patches import (
        FlowgencySettingsPatch,
        patch_flowgency_settings,
    )

    snapshot = configured_store.load()
    before = snapshot.config.teams["newsletter"].workflows[
        "workflow-one"
    ].context_generation
    new_library = config_paths["config_dir"] / "workflow-library-next"
    new_library.mkdir()
    updated = patch_flowgency_settings(
        configured_store,
        snapshot.revision,
        FlowgencySettingsPatch(
            title="Flowgency",
            default_team="newsletter",
            ai_backend="claude-code",
            theme="",
            dispatch_interval=15,
            agent_library=str(snapshot.config.flowgency.agent_library),
            compilation_cache=str(snapshot.config.flowgency.compilation_cache),
            memory_store=str(snapshot.config.flowgency.memory_store),
            prompt_store=str(snapshot.config.flowgency.prompt_store),
            workflow_library=str(new_library),
        ),
    )
    assert updated.config.flowgency.workflow_library == new_library.resolve()
    assert (
        updated.config.teams["newsletter"].workflows["workflow-one"].context_generation
        == before + 1
    )


def test_stale_global_setting_change_cannot_race_ticket_mutation(configured_store):
    from flowgency.configuration.patches import (
        FlowgencySettingsPatch,
        patch_flowgency_settings,
    )
    from flowgency.jobs.store import revision_bound_team_operation

    snapshot = configured_store.load()
    stale_revision = snapshot.revision
    patch_flowgency_settings(
        configured_store,
        snapshot.revision,
        FlowgencySettingsPatch(
            title="Bumped",
            default_team="newsletter",
            ai_backend="claude-code",
            theme="",
            dispatch_interval=15,
            agent_library=str(snapshot.config.flowgency.agent_library),
            compilation_cache=str(snapshot.config.flowgency.compilation_cache),
            memory_store=str(snapshot.config.flowgency.memory_store),
            prompt_store=str(snapshot.config.flowgency.prompt_store),
        ),
    )
    with pytest.raises(ConfigConflictError):
        with revision_bound_team_operation(
            configured_store, all_teams=True, expected_revision=stale_revision
        ):
            pass
