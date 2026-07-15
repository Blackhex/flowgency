from __future__ import annotations

from copy import deepcopy
from multiprocessing import Event, Process
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.configuration import ConfigStore
from agency.configuration.models import MemorySelector
from agency.memory import resolve_memory_selector


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _write_blueprint(root: Path, key: str, title: str) -> None:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _seed_app(monkeypatch, tmp_path, canonical_raw_config):
    raw = deepcopy(canonical_raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    group_root = tmp_path / "groups" / "newsletter"
    (group_root / "shared" / "jobs").mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "logs").mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "observations").mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "proposals").mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "decisions").mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "memory.md").write_text("# Shared\n", encoding="utf-8")
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["groups"]["newsletter"]["name"] = "Newsletter"
    raw["groups"]["newsletter"]["path"] = str(group_root)
    raw["groups"]["newsletter"]["default_integration"] = "copilot"
    raw["groups"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "sandbox": {
            "mode": "restricted",
            "roots": [str((tmp_path / "Research" / "shared").resolve())],
        },
        "tools": {"mode": "allowlist", "names": ["shell", "write"]},
    }
    raw["groups"]["newsletter"]["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "identity": {
                "display_name": "Advisor",
                "title": "Blueprint Librarian",
                "emoji": ":)",
            },
            "capabilities": {"write": True},
            "runtime": {
                "timeout": 1200,
                "sandbox": {
                    "additional_roots": [
                        str((tmp_path / "Research" / "editorial").resolve())
                    ]
                },
                "tools": {"mode": "allowlist", "names": ["shell"]},
            },
            "default_memory": {"scope": "agent"},
            "routines": [
                {
                    "id": "daily-review",
                    "skill": "daily-review",
                    "arguments": ["--brief"],
                    "schedule": {"at": "09:00"},
                    "memory": {"scope": "routine"},
                }
            ],
        }
    ]

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.reload_groups()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path


def _revision(config_path: Path) -> str:
    return ConfigStore(config_path).load().revision


def _hold_lock(lock_path: str, acquired: Event, release: Event) -> None:
    from agency.fs.locks import exclusive_lock

    with exclusive_lock(Path(lock_path), wait=True):
        acquired.set()
        release.wait(5)


def test_agent_detail_base_redirects_to_profile(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/newsletter/agents/advisor", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents/advisor/profile"


def test_agent_detail_tabs_have_stable_urls(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, canonical_raw_config)

    for tab, label in [
        ("profile", "Profile"),
        ("blueprint", "Blueprint"),
        ("runtime", "Runtime"),
        ("routines", "Routines"),
        ("memory", "Memory"),
        ("activity", "Activity"),
    ]:
        response = client.get(f"/newsletter/agents/advisor/{tab}")
        assert response.status_code == 200
        assert f'aria-current="page">{label}' in response.text


def test_profile_tab_uses_config_identity_and_capability(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    revision = _revision(config_path)

    response = client.get("/newsletter/agents/advisor/profile")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Blueprint Librarian" in response.text
    assert "Write capability" in response.text
    assert revision in response.text
    assert "Headshot" not in response.text
    assert "Subagent" not in response.text


def test_runtime_tab_separates_inherited_and_additive_roots(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    assert "Group default" in response.text
    assert "Agent addition" in response.text
    assert "Research/shared" in response.text.replace("\\", "/")
    assert "Research/editorial" in response.text.replace("\\", "/")


def test_blueprint_tab_is_read_only(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/newsletter/agents/advisor/blueprint")

    assert response.status_code == 200
    assert "daily-review" in response.text
    assert "cache" in response.text.lower()
    assert "Edit blueprint" in response.text
    assert '<form' not in response.text


def test_memory_tab_shows_selector_without_hash(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/newsletter/agents/advisor/memory")

    assert response.status_code == 200
    assert "Default memory" in response.text
    assert "Agent memory" in response.text
    assert "memory.md" in response.text
    assert "sha256" not in response.text.lower()
    assert "a" * 64 not in response.text


def test_activity_tab_is_read_only(monkeypatch, tmp_path, canonical_raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, canonical_raw_config)

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    assert "Recent activity" in response.text
    assert '<form' not in response.text


def test_profile_post_updates_config_revision_owned_fields(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/profile",
        data={
            "revision": revision,
            "display_name": "Senior Advisor",
            "title": "Runtime Curator",
            "emoji": ":D",
            "can_write": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    agent = saved["groups"]["newsletter"]["agents"][0]
    assert agent["identity"]["display_name"] == "Senior Advisor"
    assert agent["identity"]["title"] == "Runtime Curator"
    assert agent["capabilities"]["write"] is True


def test_runtime_post_updates_override_and_effective_preview(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
            "tool_mode": "allowlist",
            "tool_names": "shell\nwrite",
            "additional_roots": "C:/Research/editorial\nC:/Research/reports",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    runtime = saved["groups"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1801
    assert runtime["tools"]["mode"] == "allowlist"
    assert runtime["tools"]["names"] == ["shell", "write"]
    assert runtime["sandbox"]["additional_roots"] == [
        "C:/Research/editorial",
        "C:/Research/reports",
    ]


def test_runtime_post_surfaces_unsupported_capability_issue(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["integration"] = "script"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.reload_groups()
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "",
            "tool_mode": "allowlist",
            "tool_names": "shell",
            "additional_roots": "C:/Research/editorial",
        },
    )

    assert response.status_code == 409
    assert "cannot enforce sandbox mode" in response.text


def test_routines_post_replaces_ordered_list(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    revision = _revision(config_path)
    routines_yaml = yaml.safe_dump(
        [
            {
                "id": "triage",
                "skill": "daily-review",
                "arguments": ["--triage"],
                "schedule": {"every": "6h"},
                "memory": {"scope": "routine"},
            },
            {
                "id": "digest",
                "skill": "daily-review",
                "arguments": ["--digest"],
                "schedule": {"at": "17:30"},
            },
        ],
        sort_keys=False,
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"revision": revision, "routines_json": routines_yaml},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    routines = saved["groups"]["newsletter"]["agents"][0]["routines"]
    assert [routine["id"] for routine in routines] == ["triage", "digest"]
    assert routines[0]["memory"] == {"scope": "routine"}
    assert routines[1]["schedule"] == {"at": "17:30"}


def test_routines_post_rejects_duplicate_ids(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    revision = _revision(config_path)
    routines_yaml = yaml.safe_dump(
        [
            {"id": "dup", "skill": "daily-review", "arguments": [], "schedule": {"at": "09:00"}},
            {"id": "dup", "skill": "daily-review", "arguments": [], "schedule": {"every": "6h"}},
        ],
        sort_keys=False,
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"revision": revision, "routines_json": routines_yaml},
    )

    assert response.status_code == 409
    assert "Duplicate routine id" in response.text


def test_memory_post_updates_selector_and_content(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    app_mod.app.state.services.memory_store.ensure(resolved)
    revision = snapshot.revision
    content_revision = app_mod.app.state.services.memory_store.read(resolved).revision

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "revision": revision,
            "content_revision": content_revision,
            "default_memory_scope": "group",
            "default_memory_channel": "",
            "filename": "memory.md",
            "content": "Updated memory",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["groups"]["newsletter"]["agents"][0]["default_memory"] == {"scope": "group"}


def test_memory_post_returns_409_for_stale_content_revision(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    seeded = memory_store.ensure(resolved)
    current = memory_store.try_save(resolved, seeded.revision, {"memory.md": b"server"})
    revision = snapshot.revision

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "revision": revision,
            "content_revision": seeded.revision,
            "default_memory_scope": "agent",
            "default_memory_channel": "",
            "filename": "memory.md",
            "content": "client",
        },
    )

    assert response.status_code == 409
    assert current.revision in response.text
    assert seeded.revision in response.text
    assert "server" in response.text
    assert "client" in response.text


def test_memory_post_returns_423_when_memory_is_busy(monkeypatch, tmp_path, canonical_raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, canonical_raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    seeded = memory_store.ensure(resolved)
    lock_path = memory_store._lock_path(resolved)
    acquired, release = Event(), Event()
    process = Process(target=_hold_lock, args=(str(lock_path), acquired, release))
    process.start()
    assert acquired.wait(5)

    try:
        response = client.post(
            "/newsletter/agents/advisor/memory",
            data={
                "revision": snapshot.revision,
                "content_revision": seeded.revision,
                "default_memory_scope": "agent",
                "default_memory_channel": "",
                "filename": "memory.md",
                "content": "blocked",
            },
            follow_redirects=False,
        )
    finally:
        release.set()
        process.join(5)

    assert response.status_code == 423
    assert "Memory is busy" in response.text