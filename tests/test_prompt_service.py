from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agency.blueprints.library import BlueprintLibrary
from agency.configuration import ConfigConflictError, ConfigStore, ValidationFailed
from agency.prompts import (
    PromptConflictError,
    PromptNotFoundError,
    PromptService,
    PromptStore,
)


def local_triage_source(body: str = "Review local work.\n") -> bytes:
    return (
        "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
        + body
    ).encode("utf-8")


@pytest.fixture
def prompt_service_env(tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = Path(raw["agency"]["agent_library"])
    blueprint = library_root / "reviewer"
    blueprint.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Reviewer\n", encoding="utf-8")
    (blueprint / ".agents" / "prompts").mkdir(parents=True, exist_ok=True)
    (
        blueprint / ".agents" / "prompts" / "shared-triage.prompt.md"
    ).write_text(
        "---\nname: shared-triage\ndescription: Shared triage.\n---\n\nReview shared work.\n",
        encoding="utf-8",
    )
    agent = raw["teams"]["newsletter"]["agents"][0]
    agent["name"] = "reviewer"
    agent["blueprint"] = "reviewer"
    agent["prompts"] = []
    agent["routines"] = []
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    config_store = ConfigStore(config_path)
    store = PromptStore(Path(raw["agency"]["prompt_store"]))
    service = PromptService(
        config_store=config_store,
        library=BlueprintLibrary(library_root),
        store=store,
    )
    return SimpleNamespace(config_store=config_store, store=store, service=service)


def test_create_private_publishes_then_registers(prompt_service_env):
    snapshot = prompt_service_env.config_store.load()

    result = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=snapshot.revision,
    )

    assert result.document.name == "local-triage"
    assert (
        result.snapshot.config.teams["newsletter"].agents["reviewer"].prompts
        == ("local-triage",)
    )


def test_create_private_rolls_back_file_on_config_conflict(prompt_service_env):
    snapshot = prompt_service_env.config_store.load()
    prompt_service_env.config_store.patch(
        snapshot.revision,
        lambda raw: raw["agency"].update(title="Updated title"),
    )

    with pytest.raises(ConfigConflictError):
        prompt_service_env.service.create_private(
            "newsletter",
            "reviewer",
            "local-triage",
            local_triage_source(),
            expected_revision=snapshot.revision,
        )

    with pytest.raises(PromptNotFoundError):
        prompt_service_env.store.read("newsletter", "reviewer", "local-triage")


def test_create_private_preserves_changed_file_when_cleanup_guard_trips(
    prompt_service_env,
    monkeypatch,
):
    created_path = prompt_service_env.store.path(
        "newsletter", "reviewer", "local-triage"
    )
    original_patch = prompt_service_env.config_store.patch

    def fail_after_external_change(expected_revision, patcher):
        created_path.write_bytes(local_triage_source("Externally changed.\n"))
        raise ConfigConflictError("config.yaml changed; reload before saving")

    monkeypatch.setattr(prompt_service_env.config_store, "patch", fail_after_external_change)

    with pytest.raises(ConfigConflictError):
        prompt_service_env.service.create_private(
            "newsletter",
            "reviewer",
            "local-triage",
            local_triage_source(),
            expected_revision=prompt_service_env.config_store.load().revision,
        )

    monkeypatch.setattr(prompt_service_env.config_store, "patch", original_patch)
    stored = prompt_service_env.store.read("newsletter", "reviewer", "local-triage")
    assert stored.document.body == "Externally changed.\n"


def test_update_private_rejects_stale_digest(prompt_service_env):
    created = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=prompt_service_env.config_store.load().revision,
    )

    with pytest.raises(PromptConflictError, match="changed; reload"):
        prompt_service_env.service.update_private(
            "newsletter",
            "reviewer",
            "local-triage",
            local_triage_source("Changed body.\n"),
            expected_digest="0" * 64,
        )

    assert (
        prompt_service_env.store.read(
            "newsletter", "reviewer", "local-triage"
        ).document.digest
        == created.document.digest
    )


def test_delete_private_rejects_referenced_routine(prompt_service_env):
    created = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=prompt_service_env.config_store.load().revision,
    )
    prompt_service_env.config_store.patch(
        created.snapshot.revision,
        lambda raw: raw["teams"]["newsletter"]["agents"][0].update(
            routines=[
                {
                    "id": "morning-triage",
                    "prompt": {"scope": "instance", "name": "local-triage"},
                    "schedule": {"at": "09:00"},
                }
            ]
        ),
    )

    with pytest.raises(ValidationFailed) as excinfo:
        prompt_service_env.service.delete_private(
            "newsletter",
            "reviewer",
            "local-triage",
            expected_revision=prompt_service_env.config_store.load().revision,
            expected_digest=prompt_service_env.store.read(
                "newsletter", "reviewer", "local-triage"
            ).document.digest,
        )

    assert excinfo.value.issues[0].code == "prompt-in-use"


def test_delete_private_reports_orphan_when_source_cleanup_fails(
    prompt_service_env,
    monkeypatch,
):
    created = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=prompt_service_env.config_store.load().revision,
    )

    def fail_delete(team, instance, name, *, expected_digest):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(prompt_service_env.store, "delete", fail_delete)

    result = prompt_service_env.service.delete_private(
        "newsletter",
        "reviewer",
        "local-triage",
        expected_revision=created.snapshot.revision,
        expected_digest=created.document.digest,
    )

    assert (
        result.snapshot.config.teams["newsletter"].agents["reviewer"].prompts
        == ()
    )
    assert result.orphaned_path == prompt_service_env.store.path(
        "newsletter", "reviewer", "local-triage"
    )


def test_catalog_includes_shared_and_private_prompts(prompt_service_env):
    created = prompt_service_env.service.create_private(
        "newsletter",
        "reviewer",
        "local-triage",
        local_triage_source(),
        expected_revision=prompt_service_env.config_store.load().revision,
    )

    catalog = prompt_service_env.service.catalog(
        created.snapshot,
        "newsletter",
        "reviewer",
    )

    assert {(item.scope, item.document.name) for item in catalog} == {
        ("blueprint", "shared-triage"),
        ("instance", "local-triage"),
    }