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
from tests._lock_helpers import hold_exclusive_lock


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


def _seed_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    group_root = tmp_path / "groups" / "newsletter"
    (tmp_path / "Research" / "editorial").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Research" / "additional").mkdir(parents=True, exist_ok=True)
    (group_root / "logs").mkdir(parents=True, exist_ok=True)
    (group_root / "observations").mkdir(parents=True, exist_ok=True)
    (group_root / "proposals").mkdir(parents=True, exist_ok=True)
    (group_root / "decisions").mkdir(parents=True, exist_ok=True)
    (group_root / "locks").mkdir(parents=True, exist_ok=True)
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
            "roots": [str((tmp_path / "Research" / "editorial").resolve())],
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
                        str((tmp_path / "Research" / "additional").resolve())
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
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path


def _seed_activity_app(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    group_root = tmp_path / "groups" / "newsletter-workspace"
    raw["agency"]["default_group"] = "newsletter-prod"
    raw["groups"] = {
        "newsletter-prod": {
            **raw["groups"]["newsletter"],
            "path": str(group_root),
            "name": "Newsletter Prod",
            "agents": [
                {
                    **raw["groups"]["newsletter"]["agents"][0],
                    "name": "advisor",
                }
            ],
        }
    }
    raw["groups"]["newsletter-prod"]["agents"][0]["name"] = "advisor"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    group_root.joinpath("logs", "2026-07-16").mkdir(parents=True, exist_ok=True)
    group_root.joinpath("observations").mkdir(parents=True, exist_ok=True)
    group_root.joinpath("proposals").mkdir(parents=True, exist_ok=True)
    group_root.joinpath("decisions").mkdir(parents=True, exist_ok=True)
    group_root.joinpath("locks").mkdir(parents=True, exist_ok=True)
    group_root.joinpath("observations", "status.md").write_text(
        "---\nagent: advisor\nstatus: open\n---\n\nObservation.\n",
        encoding="utf-8",
    )
    log_file = group_root.joinpath("logs", "2026-07-16", "advisor-run.out")
    log_file.write_text("# log\n", encoding="utf-8")
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path, log_file


def _revision(config_path: Path) -> str:
    return ConfigStore(config_path).load().revision


def test_agent_detail_base_redirects_to_profile(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents/advisor/profile"


def test_agent_detail_tabs_have_stable_urls(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

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


def test_profile_tab_uses_config_identity_and_capability(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.get("/newsletter/agents/advisor/profile")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Blueprint Librarian" in response.text
    assert "Write capability" in response.text
    assert revision in response.text
    assert "Headshot" not in response.text
    assert "Subagent" not in response.text


def test_runtime_tab_separates_inherited_and_additive_roots(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    assert "Group default" in response.text
    assert "Agent addition" in response.text
    assert "Research/editorial" in response.text.replace("\\", "/")
    assert "Research/additional" in response.text.replace("\\", "/")


def test_runtime_tab_deduplicates_effective_roots_and_labels_sources(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    default_root = str((tmp_path / "Research" / "editorial").resolve())
    additional_root = str((tmp_path / "Research" / "additional").resolve())
    raw["groups"]["newsletter"]["agents"][0]["runtime"]["sandbox"]["additional_roots"] = [
        default_root,
        additional_root,
    ]
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    body = response.text.replace("\\", "/")
    assert body.count(f"Group default: <span class=\"font-mono break-all\">{default_root.replace('\\', '/')}</span>") == 2
    assert f"Agent addition: <span class=\"font-mono break-all\">{default_root.replace('\\', '/')}</span>" not in body
    assert f"Agent addition: <span class=\"font-mono break-all\">{additional_root.replace('\\', '/')}</span>" in body


def test_blueprint_tab_is_read_only(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/blueprint")

    assert response.status_code == 200
    assert "daily-review" in response.text
    assert "cache" in response.text.lower()
    assert "Open in Agent Library" in response.text
    assert "View skills in Agent Library" in response.text
    assert "/admin/agent-library/blueprints/advisor" in response.text
    assert "/admin/agent-library/blueprints/advisor/skills" in response.text
    assert '<form' not in response.text


def test_memory_tab_shows_selector_without_hash(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/memory")

    assert response.status_code == 200
    assert "Default memory" in response.text
    assert "Agent memory" in response.text
    assert "memory.md" in response.text
    assert "sha256" not in response.text.lower()
    assert "a" * 64 not in response.text


def test_activity_tab_is_read_only(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    assert "Recent activity" in response.text
    assert '<form' not in response.text


def test_activity_links_use_routed_group_key_and_round_trip(monkeypatch, tmp_path, raw_config):
    client, _, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter-prod/agents/advisor/activity")

    assert response.status_code == 200
    body = response.text
    assert "/newsletter-workspace/" not in body
    assert "/newsletter-prod/observations/status" in body
    assert "/newsletter-prod/proposals/" not in body
    log_href_match = __import__("re").search(r'href="([^"]+/logs/view\?path=[^"]+)"', body)
    assert log_href_match is not None
    log_href = log_href_match.group(1)
    assert log_href.startswith("/newsletter-prod/logs/view?path=")
    assert "%3A" in log_href or "%5C" in log_href

    log_response = client.get(log_href)
    assert log_response.status_code == 200
    assert log_file.name in log_response.text


def test_profile_post_updates_config_revision_owned_fields(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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


def test_runtime_post_updates_override_and_effective_preview(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    reports_root = (tmp_path / "Research" / "reports").resolve()
    reports_root.mkdir(parents=True, exist_ok=True)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
            "tool_mode": "allowlist",
            "tool_names": "shell\nwrite",
            "additional_roots": f"{(tmp_path / 'Research' / 'editorial').resolve()}\n{reports_root}",
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
        str((tmp_path / "Research" / "editorial").resolve()),
        str(reports_root),
    ]


def test_runtime_post_surfaces_unsupported_capability_issue(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["integration"] = "script"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
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


def test_routines_post_replaces_ordered_list(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    routines_yaml = yaml.safe_dump(
        [
            {
                "id": "triage",
                "skill": "daily-review",
                "enabled": False,
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
    assert routines[0]["enabled"] is False
    assert routines[1]["enabled"] is True
    assert routines[0]["memory"] == {"scope": "routine"}
    assert routines[1]["schedule"] == {"at": "17:30"}


def test_routines_get_preserves_disabled_state(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["routines"][0]["enabled"] = False
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "enabled: false" in response.text


def test_routines_post_rejects_duplicate_ids(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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


def test_memory_post_selector_updates_only_config(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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
    before = memory_store.ensure(resolved)
    revision = snapshot.revision

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "selector",
            "revision": revision,
            "default_memory_scope": "group",
            "default_memory_channel": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["groups"]["newsletter"]["agents"][0]["default_memory"] == {"scope": "group"}
    after = memory_store.read(resolved)
    assert after.revision == before.revision
    assert after.files == before.files


def test_memory_post_content_updates_only_selected_memory(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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
    before_config_bytes = config_path.read_bytes()
    seeded = memory_store.ensure(resolved)

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "content",
            "content_revision": seeded.revision,
            "selector_token": "agent",
            "filename": "memory.md",
            "content": "Updated memory",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_path.read_bytes() == before_config_bytes
    current = memory_store.read(resolved)
    assert current.files["memory.md"] == b"Updated memory"


def test_memory_post_returns_409_for_stale_content_revision(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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
    before_config_bytes = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "content",
            "content_revision": seeded.revision,
            "selector_token": "agent",
            "filename": "memory.md",
            "content": "client",
        },
    )

    assert response.status_code == 409
    assert current.revision in response.text
    assert seeded.revision in response.text
    assert "server" in response.text
    assert "client" in response.text
    assert config_path.read_bytes() == before_config_bytes


def test_memory_post_selector_returns_409_for_stale_config_without_mutating_memory(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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
    stale_revision = snapshot.revision

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["agency"]["title"] = "Changed elsewhere"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "selector",
            "revision": stale_revision,
            "default_memory_scope": "group",
            "default_memory_channel": "",
        },
    )

    assert response.status_code == 409
    current = memory_store.read(resolved)
    assert current.revision == seeded.revision
    assert current.files == seeded.files


def test_memory_post_returns_423_when_memory_is_busy(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
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
    before_config_bytes = config_path.read_bytes()
    lock_path = memory_store._lock_path(resolved)
    acquired, release = Event(), Event()
    process = Process(
        target=hold_exclusive_lock,
        args=(str(lock_path), acquired, release, 30),
    )
    process.start()

    try:
        assert acquired.wait(15)
        response = client.post(
            "/newsletter/agents/advisor/memory",
            data={
                "action": "content",
                "content_revision": seeded.revision,
                "selector_token": "agent",
                "filename": "memory.md",
                "content": "blocked",
            },
            follow_redirects=False,
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
    assert "Memory is busy" in response.text
    assert config_path.read_bytes() == before_config_bytes