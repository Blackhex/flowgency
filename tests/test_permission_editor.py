from __future__ import annotations

from copy import deepcopy
import threading

import pytest


def _catalog():
    from flowgency.integrations.tool_catalog import ToolCatalog, ToolDescriptor

    return ToolCatalog(
        "claude-code",
        "fixture-v1",
        (
            ToolDescriptor("read"),
            ToolDescriptor("search"),
            ToolDescriptor("write"),
        ),
        False,
    )


def _seed_store(config_paths, raw_config, *, integration: str = "claude-code"):
    from flowgency.configuration.store import ConfigStore

    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["default_integration"] = integration
    team["agents"][0]["integration"] = integration
    store = ConfigStore(config_paths["config_path"])
    snapshot = store.create(raw)
    return store, snapshot


def test_preview_does_not_write(raw_config, config_paths, monkeypatch):
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import catalog_id
    from flowgency.permissions.editor import EditorRequest, load_editor, prepare_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store, snapshot = _seed_store(config_paths, raw_config)
    before = store.path.read_bytes()
    monkeypatch.setattr(
        "flowgency.configuration.store.initialize_storage_directories",
        lambda config: (_ for _ in ()).throw(AssertionError("preview attempted a write")),
    )

    loaded = load_editor(snapshot, "newsletter", "builder")
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id=catalog_id(catalog),
        draft_version=1,
        draft=loaded.form.draft,
    )

    prepared = prepare_permissions(snapshot, "newsletter", "builder", request, catalog)

    assert prepared.summary is not None
    assert prepared.summary.mode == "unrestricted"
    assert prepared.catalog == catalog
    assert store.path.read_bytes() == before


def test_load_editor_returns_form_and_issues_when_saved_policy_is_now_invalid(
    raw_config, config_paths, monkeypatch
):
    from flowgency.configuration.store import ConfigStore
    from flowgency.integrations import BaseIntegration
    from flowgency.permissions.editor import load_editor

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    raw = deepcopy(raw_config)
    raw["teams"]["newsletter"]["permissions"] = {
        "mode": "restricted",
        "rules": [{"path": ".", "tools": ["read"]}],
    }
    raw["teams"]["newsletter"]["agents"][0]["integration"] = "claude-code"
    store = ConfigStore(config_paths["config_path"])
    snapshot = store.create(raw)

    loaded = load_editor(snapshot, "newsletter", "builder")

    assert loaded.summary is None
    assert [issue.code for issue in loaded.issues] == ["unsupported-permission-mode"]
    assert loaded.form.draft.mode == "inherit"
    assert loaded.catalog == catalog


def test_prepare_permissions_rejects_catalog_drift(raw_config, config_paths, monkeypatch):
    from flowgency.integrations import BaseIntegration
    from flowgency.permissions.editor import CatalogConflictError, EditorRequest, load_editor, prepare_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    _, snapshot = _seed_store(config_paths, raw_config)
    loaded = load_editor(snapshot, "newsletter", "builder")
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id="0" * 64,
        draft_version=1,
        draft=loaded.form.draft,
    )

    with pytest.raises(CatalogConflictError, match="Permission tool catalog changed; reload before saving"):
        prepare_permissions(snapshot, "newsletter", "builder", request, catalog)


def test_save_permissions_noop_preserves_current_bytes(raw_config, config_paths, monkeypatch):
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import catalog_id
    from flowgency.permissions.editor import EditorRequest, load_editor, save_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store, snapshot = _seed_store(config_paths, raw_config)
    before = store.path.read_bytes()
    loaded = load_editor(snapshot, "newsletter", "builder")
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id=catalog_id(catalog),
        draft_version=1,
        draft=loaded.form.draft,
    )

    updated = save_permissions(store, "newsletter", "builder", request)

    assert store.path.read_bytes() == before
    assert "permissions" not in updated.raw["teams"]["newsletter"]["agents"][0]


def test_save_permissions_updates_only_target_agent_policy(raw_config, config_paths, monkeypatch):
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import catalog_id
    from flowgency.permissions.forms import RuleDraft
    from flowgency.permissions.editor import EditorRequest, load_editor, save_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store, snapshot = _seed_store(config_paths, raw_config)
    loaded = load_editor(snapshot, "newsletter", "builder")
    draft = loaded.form.draft.model_copy(deep=True)
    draft.mode = "unrestricted"
    draft.rules = [
        RuleDraft(source_index=None, target="no_path", path=None, selected=["read"])
    ]
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id=catalog_id(catalog),
        draft_version=2,
        draft=draft,
    )

    updated = save_permissions(store, "newsletter", "builder", request)

    agent = updated.raw["teams"]["newsletter"]["agents"][0]
    assert agent["permissions"] == {"mode": "unrestricted", "rules": [{"tools": ["read"]}]}
    assert agent["blueprint"] == raw_config["teams"]["newsletter"]["agents"][0]["blueprint"]
    assert agent["prompts"] == raw_config["teams"]["newsletter"]["agents"][0]["prompts"]
    assert agent["routines"] == raw_config["teams"]["newsletter"]["agents"][0]["routines"]
    assert updated.raw["teams"]["newsletter"]["workspaces"] == raw_config["teams"]["newsletter"]["workspaces"]


def test_save_permissions_rejects_stale_revision(raw_config, config_paths, monkeypatch):
    from flowgency.configuration.store import ConfigConflictError
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import catalog_id
    from flowgency.permissions.editor import EditorRequest, load_editor, save_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store, snapshot = _seed_store(config_paths, raw_config)
    loaded = load_editor(snapshot, "newsletter", "builder")
    before = store.path.read_bytes()
    store.patch(
        snapshot.revision,
        lambda raw: raw["teams"]["newsletter"]["permissions"].__setitem__(
            "rules", [{"tools": []}]
        ),
    )
    newer = store.path.read_bytes()
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id=catalog_id(catalog),
        draft_version=1,
        draft=loaded.form.draft,
    )

    with pytest.raises(ConfigConflictError, match="config.yaml changed; reload before saving"):
        save_permissions(store, "newsletter", "builder", request)

    assert before != newer
    assert store.path.read_bytes() == newer


def test_save_permissions_validation_failure_leaves_file_unchanged(
    raw_config, config_paths, monkeypatch
):
    from flowgency.configuration import ValidationFailed
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import catalog_id
    from flowgency.permissions.editor import EditorRequest, load_editor, save_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store, snapshot = _seed_store(config_paths, raw_config)
    loaded = load_editor(snapshot, "newsletter", "builder")
    draft = loaded.form.draft.model_copy(deep=True)
    draft.mode = "restricted"
    before = store.path.read_bytes()
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id=catalog_id(catalog),
        draft_version=3,
        draft=draft,
    )

    with pytest.raises(ValidationFailed):
        save_permissions(store, "newsletter", "builder", request)

    assert store.path.read_bytes() == before


def test_concurrent_same_revision_saves_conflict_instead_of_losing_changes(
    raw_config, config_paths, monkeypatch
):
    from flowgency.configuration.store import ConfigConflictError, ConfigStore
    from flowgency.integrations import BaseIntegration
    from flowgency.integrations.tool_catalog import catalog_id
    from flowgency.permissions.forms import RuleDraft
    from flowgency.permissions.editor import EditorRequest, load_editor, save_permissions

    catalog = _catalog()
    monkeypatch.setattr(BaseIntegration, "permission_tool_catalog", lambda self: catalog)
    store, snapshot = _seed_store(config_paths, raw_config)
    loaded = load_editor(snapshot, "newsletter", "builder")
    draft = loaded.form.draft.model_copy(deep=True)
    draft.mode = "unrestricted"
    draft.rules = [
        RuleDraft(source_index=None, target="no_path", path=None, selected=["read"])
    ]
    request = EditorRequest(
        revision=snapshot.revision,
        catalog_id=catalog_id(catalog),
        draft_version=1,
        draft=draft,
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker() -> None:
        local_store = ConfigStore(config_paths["config_path"])
        barrier.wait()
        try:
            save_permissions(local_store, "newsletter", "builder", request)
        except ConfigConflictError:
            outcomes.append("conflict")
            return
        outcomes.append("saved")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict", "saved"]