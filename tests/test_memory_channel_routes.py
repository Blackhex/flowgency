from __future__ import annotations

from copy import deepcopy
from multiprocessing import Event, Process
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.configuration import ConfigStore
from agency.configuration.models import MemorySelector
from agency.fs.locks import exclusive_lock
from agency.memory import resolve_memory_selector


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


def _seed_memory_app(monkeypatch, tmp_path, canonical_raw_config):
    raw = deepcopy(canonical_raw_config)
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

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.reload_groups()
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


def _hold_lock(lock_path: str, acquired: Event, release: Event) -> None:
    with exclusive_lock(Path(lock_path), wait=True):
        acquired.set()
        release.wait(5)


def test_channel_is_global_across_groups(monkeypatch, tmp_path, canonical_raw_config):
    client, _, _ = _seed_memory_app(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/admin/memory-channels/brand-strategy")

    assert response.status_code == 200
    assert "Newsletter / Advisor" in response.text
    assert "Product / Strategist" in response.text
    assert "Internal hash" not in response.text


def test_unknown_channel_read_never_creates(
    monkeypatch,
    tmp_path,
    canonical_raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
    )
    before = sorted(resolved.directory.parent.iterdir())

    response = client.get("/admin/memory-channels/missing")

    assert response.status_code == 404
    assert sorted(resolved.directory.parent.iterdir()) == before


def test_channel_markdown_save_rejects_stale_revision(
    monkeypatch,
    tmp_path,
    canonical_raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
    canonical_raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
    )
    services = app_mod.app.state.services
    snapshot = services.memory_store.read(resolved)
    acquired = Event()
    release = Event()
    process = Process(
        target=_hold_lock,
        args=(
            str(services.memory_store._lock_path(resolved)),
            acquired,
            release,
        ),
    )
    process.start()
    acquired.wait(5)
    try:
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
        process.join(5)

    assert response.status_code == 423


def test_channel_delete_blocks_when_referenced(
    monkeypatch,
    tmp_path,
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
    )

    response = client.post(
        "/admin/memory-channels/brand-strategy/delete",
        data={"revision": _config_revision(config_path)},
    )

    assert response.status_code == 409
    assert "referenced" in response.text.lower()
    assert "Newsletter / Advisor" in response.text


def test_channel_rename_updates_display_name_only(
    monkeypatch,
    tmp_path,
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
    )
    snapshot = ConfigStore(config_path).load()
    before = deepcopy(snapshot.raw)

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
    assert refreshed.config.memory.channels["brand-ops"].display_name == "Support"


def test_channel_rekey_blocks_when_referenced(
    monkeypatch,
    tmp_path,
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
    canonical_raw_config,
):
    client, config_path, _ = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
    canonical_raw_config,
):
    client, _, resolved = _seed_memory_app(
        monkeypatch,
        tmp_path,
        canonical_raw_config,
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
