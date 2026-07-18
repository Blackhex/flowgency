from __future__ import annotations

from copy import deepcopy
from multiprocessing import Event, Process
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi.testclient import TestClient
import pytest

from agency import app as app_mod
from agency.configuration import ConfigConflictError, ConfigStore
from agency.configuration.models import MemorySelector
from agency.jobs.authority import JobStore
from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)
from agency.jobs.store import read_job, write_job
from dataclasses import replace
from agency.memory import resolve_memory_selector
from tests._lock_helpers import hold_exclusive_lock


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _write_blueprint(root: Path, key: str) -> None:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Advisor\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _seed_memory_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    _write_blueprint(library_root, "advisor")
    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["memory"] = {
        "channels": {
            "brand-strategy": {"display_name": "Brand Strategy"},
            "support": {"display_name": "Support"},
        }
    }
    groups = {}
    for key, group_name, agent_name, display_name in [
        ("newsletter", "Newsletter", "advisor", "Advisor"),
        ("product", "Product", "strategist", "Strategist"),
    ]:
        group_root = tmp_path / "groups" / key
        (group_root / "shared" / "jobs").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "shared" / "logs").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "shared" / "observations").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "shared" / "proposals").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "shared" / "decisions").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "shared" / "memory.md").write_text(
            "# Shared\n",
            encoding="utf-8",
        )
        groups[key] = {
            "name": group_name,
            "path": str(group_root),
            "default_integration": "copilot",
            "agents": [
                {
                    "name": agent_name,
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "identity": {"display_name": display_name},
                    "default_memory": {
                        "scope": "channel",
                        "channel": "brand-strategy",
                    },
                }
            ],
            "workspaces": [],
        }
    raw["groups"] = groups

    authority = JobStore(memory_root)
    authority.group_root("newsletter").mkdir(parents=True, exist_ok=True)
    authority.group_root("product").mkdir(parents=True, exist_ok=True)

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    services = app_mod.app.state.services
    snapshot = services.config_store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="channel", channel="brand-strategy"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )
    services.memory_store.ensure(resolved)
    services.memory_store.try_save(
        resolved,
        services.memory_store.read(resolved).revision,
        {"memory.md": b"# Brand\n"},
    )
    return TestClient(app_mod.app), config_path, resolved


def _config_revision(config_path: Path) -> str:
    return ConfigStore(config_path).load().revision


def _write_channel_job(
    config_path: Path,
    channel_key: str,
    *,
    status: str,
    job_id: str | None = None,
) -> Path:
    snapshot = ConfigStore(config_path).load()
    group = snapshot.config.groups["newsletter"]
    authority = JobStore(snapshot.config.agency.memory_store)
    authority.group_root("newsletter").mkdir(parents=True, exist_ok=True)
    resolved = resolve_memory_selector(
        MemorySelector(scope="channel", channel=channel_key),
        job_id=job_id or uuid4().hex,
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=snapshot.config.agency.memory_store,
    )
    spec = JobSpec(
        schema_version=2,
        job_id=job_id or uuid4().hex,
        config_path=str(config_path.resolve()),
        config_revision=snapshot.revision,
        group_key="newsletter",
        group_path=str(group.path.resolve()),
        agent_name="advisor",
        workspace_dir=str(group.path.resolve()),
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v-test",
            cache_path=str(
                (
                    config_path.parent
                    / "compiled-agents"
                    / "copilot"
                    / "v-test"
                    / "digest-1"
                ).resolve()
            ),
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=(),
        task_input="Run it",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "channel", "channel": channel_key},
            canonical_json=resolved.canonical_json,
            memory_hash=resolved.memory_hash,
            path=str(resolved.directory.resolve()),
        ),
        trigger_context=None,
        prompt_source={
            "type": "saved_prompt",
            "path": "shared/prompts/daily-review.md",
        },
        timeout_override=None,
        created_at="2026-07-16T00:00:00+00:00",
    )
    path = authority.path("newsletter", spec.job_id)
    write_job(path, replace(JobRecord.from_spec(spec), status=status))
    assert read_job(path).status == status
    return path


def test_channel_is_global_across_groups(monkeypatch, tmp_path, raw_config):
    client, _, _ = _seed_memory_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/memory-channels/brand-strategy")

    assert response.status_code == 200
    assert "Newsletter / Advisor" in response.text
    assert "Product / Strategist" in response.text
    assert "Internal hash" not in response.text


def test_unknown_channel_read_never_creates(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    before = sorted(resolved.directory.parent.iterdir())

    response = client.get("/admin/memory-channels/missing")

    assert response.status_code == 404
    assert sorted(resolved.directory.parent.iterdir()) == before


def test_channel_markdown_save_rejects_stale_revision(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    current = (resolved.directory / "memory.md").read_text(encoding="utf-8")

    response = client.post(
        "/admin/memory-channels/brand-strategy/content",
        data={
            "filename": "memory.md",
            "content_revision": "0" * 64,
            "content": current + "Updated\n",
        },
    )

    assert response.status_code == 409
    assert (
        resolved.directory / "memory.md"
    ).read_text(encoding="utf-8") == current


def test_channel_markdown_save_returns_423_when_locked(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    services = app_mod.app.state.services
    snapshot = services.memory_store.read(resolved)
    acquired = Event()
    release = Event()
    process = Process(
        target=hold_exclusive_lock,
        args=(
            str(services.memory_store._lock_path(resolved)),
            acquired,
            release,
            30,
        ),
    )
    process.start()
    try:
        assert acquired.wait(15)
        response = client.post(
            "/admin/memory-channels/brand-strategy/content",
            data={
                "filename": "memory.md",
                "content_revision": snapshot.revision,
                "content": "# Locked\n",
            },
        )
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(15)
        assert not process.is_alive()
        assert process.exitcode == 0

    assert response.status_code == 423


def test_channel_delete_blocks_when_referenced(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )

    response = client.post(
        "/admin/memory-channels/brand-strategy/delete",
        data={"revision": _config_revision(config_path)},
    )

    assert response.status_code == 409
    assert "referenced" in response.text.lower()
    assert "Newsletter / Advisor" in response.text


@pytest.mark.parametrize("status", ["queued", "waiting_for_memory", "running"])
def test_channel_delete_blocks_when_active_job_targets_channel(
    monkeypatch,
    tmp_path,
    raw_config,
    status,
):
    client, config_path, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    _write_channel_job(
        config_path,
        "support",
        status=status,
        job_id=f"job-{status}",
    )
    assert not _channel_references_for_test(config_path, "support")
    services = app_mod.app.state.services
    support = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=ConfigStore(config_path).load().config.memory.channels,
        store_root=services.memory_store.root,
    )
    services.memory_store.ensure(support)

    response = client.post(
        "/admin/memory-channels/support/delete",
        data={"revision": _config_revision(config_path)},
    )

    assert response.status_code == 409
    assert "active job" in response.text.lower()
    assert f"job-{status}" in response.text
    assert support.directory.exists()


def test_channel_delete_ignores_terminal_jobs(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    _write_channel_job(
        config_path,
        "support",
        status="failed",
        job_id="job-failed",
    )
    services = app_mod.app.state.services
    support = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=ConfigStore(config_path).load().config.memory.channels,
        store_root=services.memory_store.root,
    )
    services.memory_store.ensure(support)
    (support.directory / "memory.md").write_text(
        "# Support\n",
        encoding="utf-8",
    )

    response = client.post(
        "/admin/memory-channels/support/delete",
        data={"revision": _config_revision(config_path)},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_channel_delete_returns_423_when_memory_is_busy(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    services = app_mod.app.state.services
    support = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=ConfigStore(config_path).load().config.memory.channels,
        store_root=services.memory_store.root,
    )
    snapshot = services.memory_store.ensure(support)
    acquired = Event()
    release = Event()
    process = Process(
        target=hold_exclusive_lock,
        args=(
            str(services.memory_store._lock_path(support)),
            acquired,
            release,
            30,
        ),
    )
    process.start()
    try:
        assert acquired.wait(15)
        response = client.post(
            "/admin/memory-channels/support/delete",
            data={"revision": _config_revision(config_path)},
        )
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(15)
        assert process.exitcode == 0

    assert response.status_code == 423
    assert ConfigStore(config_path).load().config.memory.channels["support"]
    assert support.directory.exists()
    assert snapshot.revision


def test_channel_delete_restores_archive_when_config_replace_conflicts(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    services = app_mod.app.state.services
    support = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=ConfigStore(config_path).load().config.memory.channels,
        store_root=services.memory_store.root,
    )
    services.memory_store.ensure(support)
    (support.directory / "memory.md").write_text(
        "# Restore me\n",
        encoding="utf-8",
    )

    def fail_replace(expected_revision, raw):
        raise ConfigConflictError("config.yaml changed; reload before saving")

    monkeypatch.setattr(services.config_store, "replace", fail_replace)

    response = client.post(
        "/admin/memory-channels/support/delete",
        data={"revision": _config_revision(config_path)},
    )

    assert response.status_code == 409
    assert support.directory.exists()
    assert (
        support.directory / "memory.md"
    ).read_text(encoding="utf-8") == "# Restore me\n"
    assert "support" in ConfigStore(config_path).load().config.memory.channels


def test_channel_delete_archives_canonical_and_recreation_starts_fresh(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    services = app_mod.app.state.services
    support = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=ConfigStore(config_path).load().config.memory.channels,
        store_root=services.memory_store.root,
    )
    services.memory_store.ensure(support)
    services.memory_store.try_save(
        support,
        services.memory_store.read(support).revision,
        {"memory.md": b"# Archived support\n"},
    )

    response = client.post(
        "/admin/memory-channels/support/delete",
        data={"revision": _config_revision(config_path)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    snapshot = ConfigStore(config_path).load()
    assert "support" not in snapshot.config.memory.channels
    assert not support.directory.exists()
    archives = list(
        (services.memory_store.root / ".deleted").glob(
            f"{support.memory_hash}-*"
        )
    )
    assert len(archives) == 1
    assert (
        archives[0] / "memory.md"
    ).read_text(encoding="utf-8") == "# Archived support\n"

    create = client.post(
        "/admin/memory-channels/create",
        data={
            "revision": snapshot.revision,
            "channel_key": "support",
            "display_name": "Support",
        },
        follow_redirects=False,
    )

    assert create.status_code == 303
    refreshed = ConfigStore(config_path).load()
    recreated = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=refreshed.config.memory.channels,
        store_root=services.memory_store.root,
    )
    recreated_snapshot = services.memory_store.ensure(recreated)
    assert recreated_snapshot.files == {"memory.md": b""}


def _channel_references_for_test(
    config_path: Path,
    channel_key: str,
) -> list[str]:
    snapshot = ConfigStore(config_path).load()
    refs: list[str] = []
    for group in snapshot.config.groups.values():
        for agent in group.agents.values():
            if (
                agent.default_memory is not None
                and agent.default_memory.scope == "channel"
                and agent.default_memory.channel == channel_key
            ):
                refs.append(agent.name)
    return refs


def test_channel_rename_updates_display_name_only(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )

    response = client.post(
        "/admin/memory-channels/brand-strategy",
        data={
            "revision": _config_revision(config_path),
            "display_name": "Brand Planning",
            "channel_key": "brand-strategy",
            "new_key": "brand-strategy",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    snapshot = ConfigStore(config_path).load()
    assert (
        snapshot.config.memory.channels[
            "brand-strategy"
        ].display_name
        == "Brand Planning"
    )


def test_channel_rekey_rejects_forged_current_key(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    snapshot = ConfigStore(config_path).load()
    before = deepcopy(snapshot.raw)

    response = client.post(
        "/admin/memory-channels/brand-strategy",
        data={
            "revision": snapshot.revision,
            "display_name": "Brand Strategy",
            "channel_key": "unreferenced",
            "new_key": "brand-ops",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert ConfigStore(config_path).load().raw == before


def test_channel_rekey_rejects_forged_referenced_current_key(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    snapshot = ConfigStore(config_path).load()
    response = client.post(
        "/admin/memory-channels/support",
        data={
            "revision": snapshot.revision,
            "display_name": "Support",
            "channel_key": "brand-strategy",
            "new_key": "brand-ops",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    refreshed = ConfigStore(config_path).load()
    assert "support" not in refreshed.config.memory.channels
    assert (
        refreshed.config.memory.channels["brand-ops"].display_name
        == "Support"
    )


def test_channel_rekey_blocks_when_referenced(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )

    response = client.post(
        "/admin/memory-channels/brand-strategy",
        data={
            "revision": _config_revision(config_path),
            "display_name": "Brand Strategy",
            "channel_key": "brand-strategy",
            "new_key": "brand-ops",
        },
    )

    assert response.status_code == 409
    assert "rekey" in response.text.lower()
    assert "referenced" in response.text.lower()


def test_channel_rekey_allows_unreferenced_channel(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    snapshot = ConfigStore(config_path).load()

    response = client.post(
        "/admin/memory-channels/support",
        data={
            "revision": snapshot.revision,
            "display_name": "Support Desk",
            "new_key": "support-ops",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    refreshed = ConfigStore(config_path).load()
    assert "support" not in refreshed.config.memory.channels
    assert (
        refreshed.config.memory.channels["support-ops"].display_name
        == "Support Desk"
    )


def test_channel_rekey_rejects_destination_collision(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    snapshot = ConfigStore(config_path).load()

    response = client.post(
        "/admin/memory-channels/support",
        data={
            "revision": snapshot.revision,
            "display_name": "Support Desk",
            "new_key": "brand-strategy",
        },
    )

    assert response.status_code == 409
    assert "already exists" in response.text.lower()


def test_channel_content_save_binds_url_identity(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    snapshot = app_mod.app.state.services.memory_store.read(resolved)
    current = (resolved.directory / "memory.md").read_text(encoding="utf-8")

    response = client.post(
        "/admin/memory-channels/brand-strategy/content",
        data={
            "filename": "memory.md",
            "content_revision": snapshot.revision,
            "content": current + "Updated\n",
            "channel_key": "support",
            "selector": "support",
            "hash": "deadbeef",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert (
        resolved.directory / "memory.md"
    ).read_text(encoding="utf-8") == current
