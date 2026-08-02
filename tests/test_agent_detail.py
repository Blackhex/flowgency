from __future__ import annotations

from copy import deepcopy
from multiprocessing import Event, Process
from pathlib import Path
import re

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
    prompt_dir = blueprint / ".agents" / "prompts"
    skill.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _local_triage_source(body: str = "Review local work.\n") -> str:
    return (
        "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
        + body
    )


def _seed_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    prompt_root = tmp_path / "prompts"
    group_root = tmp_path / "groups" / "newsletter"
    (tmp_path / "Research" / "editorial").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Research" / "additional").mkdir(parents=True, exist_ok=True)
    (group_root / "logs").mkdir(parents=True, exist_ok=True)
    (group_root / "observations").mkdir(parents=True, exist_ok=True)
    (group_root / "proposals").mkdir(parents=True, exist_ok=True)
    (group_root / "decisions").mkdir(parents=True, exist_ok=True)
    (group_root / "locks").mkdir(parents=True, exist_ok=True)
    _write_blueprint(library_root, "advisor", "Advisor")
    local_prompt_dir = prompt_root / "newsletter" / "advisor"
    local_prompt_dir.mkdir(parents=True, exist_ok=True)
    (local_prompt_dir / "local-triage.prompt.md").write_text(
        _local_triage_source(),
        encoding="utf-8",
    )

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["agency"]["prompt_store"] = str(prompt_root)
    raw["groups"]["newsletter"]["name"] = "Newsletter"
    raw["groups"]["newsletter"]["path"] = str(group_root)
    raw["groups"]["newsletter"]["default_integration"] = "copilot"
    raw["groups"]["newsletter"]["runtime"] = {
        "timeout": 2400,
        "permissions": {
            "mode": "restricted",
            "rules": [
                {"path": str((tmp_path / "Research" / "editorial").resolve()), "tools": ["read", "shell", "write"]},
            ],
        },
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
            "runtime": {
                "timeout": 1200,
                "permissions": {
                    "rules": [
                        {"path": str((tmp_path / "Research" / "additional").resolve()), "tools": ["read", "shell", "write"]},
                        {"path": str(raw_config["groups"]["newsletter"]["workspace_path"]), "tools": ["read", "shell", "write"]},
                    ],
                },
            },
            "default_memory": {"scope": "agent"},
            "routines": [
                {
                    "id": "daily-review",
                    "prompt": {"scope": "blueprint", "name": "pr-review"},
                    "arguments": ["--brief"],
                    "schedule": {"at": "09:00"},
                    "memory": {"scope": "routine"},
                }
            ],
            "prompts": ["local-triage"],
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
        ("prompts", "Prompts"),
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
    # Effective preview shows rules as "Rule" source labels
    assert "Rule" in response.text
    assert "Research/editorial" in response.text.replace("\\", "/")
    assert "Research/additional" in response.text.replace("\\", "/")


def test_runtime_tab_deduplicates_effective_roots_and_labels_sources(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    default_root = str((tmp_path / "Research" / "editorial").resolve())
    additional_root = str((tmp_path / "Research" / "additional").resolve())
    # Add a duplicate rule path that appears in both group and agent (uniform tools)
    raw["groups"]["newsletter"]["agents"][0]["runtime"]["permissions"]["rules"] = [
        {"path": default_root, "tools": ["read", "shell", "write"]},
        {"path": additional_root, "tools": ["read", "shell", "write"]},
    ]
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    body = response.text.replace("\\", "/")
    # The effective preview dedups the same path (group + agent merged)
    assert body.count(f"Rule: <span class=\"font-mono break-all\">{default_root.replace(chr(92), '/')}</span>") == 1
    assert f"Rule: <span class=\"font-mono break-all\">{additional_root.replace(chr(92), '/')}</span>" in body


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


def test_agent_prompts_tab_separates_shared_and_private(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/prompts")

    assert response.status_code == 200
    assert "Shared from blueprint" in response.text
    assert "Private to this instance" in response.text
    assert "pr-review" in response.text
    assert "local-triage" in response.text
    assert "/admin/agent-library/blueprints/advisor/prompts" in response.text


def test_agent_prompts_create_registers_private_prompt(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/prompts/create",
        data={
            "revision": revision,
            "name": "daily-triage",
            "source": "---\nname: daily-triage\ndescription: Daily triage.\n---\n\nTriage now.\n",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    agent = saved["groups"]["newsletter"]["agents"][0]
    assert "daily-triage" in agent["prompts"]


def test_agent_prompts_create_stale_revision_preserves_source(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    stale_revision = _revision(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["agency"]["title"] = "Changed elsewhere"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    source = "---\nname: stale-check\ndescription: stale\n---\n\nKeep me\n"
    response = client.post(
        "/newsletter/agents/advisor/prompts/create",
        data={"revision": stale_revision, "name": "stale-check", "source": source},
    )

    assert response.status_code == 409
    assert "config.yaml changed" in response.text
    assert source in response.text


def test_agent_prompts_save_rejects_stale_digest_and_preserves_source(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    source = _local_triage_source("Updated body.\n")
    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/save",
        data={"digest": "0" * 64, "source": source},
    )

    assert response.status_code == 409
    assert "prompt changed; reload and retry" in response.text
    assert source in response.text


def test_agent_prompts_delete_rejects_prompt_in_use(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "local-review",
            "prompt": {"scope": "instance", "name": "local-triage"},
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()
    source = client.get("/newsletter/agents/advisor/prompts").text
    match = re.search(r'name="digest" value="([0-9a-f]{64})"', source)
    assert match is not None

    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/delete",
        data={"revision": revision, "digest": match.group(1)},
    )

    assert response.status_code == 409
    assert "local-triage" in response.text
    assert "local-review" in response.text


def test_agent_prompts_unknown_agent_and_prompt(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    missing_agent = client.get("/newsletter/agents/missing/prompts")
    assert missing_agent.status_code == 404

    missing_prompt = client.post(
        "/newsletter/agents/advisor/prompts/missing/save",
        data={"digest": "0" * 64, "source": "---\nname: missing\ndescription: missing\n---\n\nMissing\n"},
    )
    assert missing_prompt.status_code == 404

    missing_delete = client.post(
        "/newsletter/agents/advisor/prompts/missing/delete",
        data={"revision": revision, "digest": "0" * 64},
    )
    assert missing_delete.status_code == 404


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


def test_runtime_post_updates_override_and_effective_preview(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    runtime = saved["groups"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1801


def test_runtime_post_surfaces_unsupported_capability_issue(monkeypatch, tmp_path, raw_config):
    """Switching to an integration that can't enforce the mode returns 409."""
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # script integration only supports unrestricted; group mode is restricted
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
        },
    )

    assert response.status_code == 409
    assert "cannot enforce permission mode" in response.text


def test_routines_post_replaces_ordered_list(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    routines_yaml = yaml.safe_dump(
        [
            {
                "id": "triage",
                "prompt": {"scope": "blueprint", "name": "pr-review"},
                "enabled": False,
                "arguments": ["--triage"],
                "schedule": {"every": "6h"},
                "memory": {"scope": "routine"},
            },
            {
                "id": "digest",
                "prompt": {"scope": "instance", "name": "local-triage"},
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


def test_routines_post_keeps_the_recovery_bound(monkeypatch, tmp_path, raw_config):
    """Saving a routine must not silently drop its catch_up."""
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    routines_yaml = yaml.safe_dump(
        [
            {
                "id": "triage",
                "prompt": {"scope": "blueprint", "name": "pr-review"},
                "arguments": [],
                "schedule": {"at": "09:00", "catch_up": "48h"},
            }
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
    assert routines[0]["schedule"] == {"at": "09:00", "catch_up": "48h"}

    reloaded = client.get("/newsletter/agents/advisor/routines")
    assert "catch_up: 48h" in reloaded.text


def test_routines_post_rejects_an_unusable_recovery_bound(
    monkeypatch, tmp_path, raw_config
):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    routines_yaml = yaml.safe_dump(
        [
            {
                "id": "triage",
                "prompt": {"scope": "blueprint", "name": "pr-review"},
                "arguments": [],
                "schedule": {"at": "09:00", "catch_up": "sometimes"},
            }
        ],
        sort_keys=False,
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"revision": revision, "routines_json": routines_yaml},
    )

    assert response.status_code == 409
    assert "Recovery bound" in response.text


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
            {"id": "dup", "prompt": {"scope": "blueprint", "name": "pr-review"}, "arguments": [], "schedule": {"at": "09:00"}},
            {"id": "dup", "prompt": {"scope": "instance", "name": "local-triage"}, "arguments": [], "schedule": {"every": "6h"}},
        ],
        sort_keys=False,
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"revision": revision, "routines_json": routines_yaml},
    )

    assert response.status_code == 409
    assert "Duplicate routine id" in response.text


def test_routine_editor_accepts_explicit_blueprint_and_instance_prompts(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={
            "revision": revision,
            "routines_json": yaml.safe_dump(
                [
                    {
                        "id": "morning-review",
                        "prompt": {"scope": "blueprint", "name": "pr-review"},
                        "schedule": {"at": "09:00"},
                    },
                    {
                        "id": "local-review",
                        "prompt": {"scope": "instance", "name": "local-triage"},
                        "enabled": False,
                        "arguments": ["--focused"],
                        "schedule": {"every": "6h"},
                        "memory": {"scope": "channel", "channel": "support"},
                    },
                ],
                sort_keys=False,
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_routine_editor_rejects_unknown_scope_name_and_shorthand(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    payload = yaml.safe_dump(
        [
            {
                "id": "bad-scope",
                "prompt": {"scope": "group", "name": "pr-review"},
                "schedule": {"at": "09:00"},
            },
            {
                "id": "bad-name",
                "prompt": {"scope": "blueprint", "name": "unknown"},
                "schedule": {"every": "6h"},
            },
            {
                "id": "string-prompt",
                "prompt": "pr-review",
                "schedule": {"every": "2h"},
            },
        ],
        sort_keys=False,
    )

    response = client.post(
        "/newsletter/agents/advisor/routines",
        data={"revision": revision, "routines_json": payload},
    )

    assert response.status_code == 409
    assert "Routine prompt must be selected from the effective prompt catalog" in response.text
    assert "bad-scope" in response.text
    assert "bad-name" in response.text
    assert "string-prompt" in response.text


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


def test_routines_get_lists_schedule_status(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "Schedule status" in response.text
    assert "Last fired" in response.text
    assert "Next due" in response.text
    assert "at 09:00" in response.text
    assert "never" in response.text


def test_routines_get_disabled_routine_shows_dash_for_next_due(monkeypatch, tmp_path, raw_config):
    """Disabled routines must show '—' for next_due, not a computed schedule."""
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "weekly-digest",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "enabled": False,
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "weekly-digest" in response.text
    assert "—" in response.text


def test_routines_get_fired_routine_shows_timestamp_and_next_occurrence(monkeypatch, tmp_path, raw_config):
    """A routine whose marker file exists shows a timestamp and a relative next-due time."""
    import os
    from datetime import datetime
    from agency.dispatch.schedule import every_marker_path

    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["dispatch"] = {"enabled": True}
    raw["groups"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "hourly-check",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    logs_root = tmp_path / "groups" / "newsletter" / "logs"
    marker = every_marker_path(logs_root, "advisor", "hourly-check")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    # set mtime to now so next occurrence is 6h from now
    stamp = datetime.now().timestamp()
    os.utime(marker, (stamp, stamp))

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "hourly-check" in response.text
    assert "on schedule" not in response.text
    # a relative future string should appear (e.g. "6h away" or "5h away" etc.)
    assert "away" in response.text or "due now" in response.text
    assert datetime.now().strftime("%Y-%m-%d") in response.text


def test_routines_dispatch_disabled_shows_dispatch_disabled(monkeypatch, tmp_path, raw_config):
    """When dispatch is disabled, Next due reads 'dispatch disabled' for every routine."""
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # ensure dispatch is explicitly disabled
    raw["groups"]["newsletter"]["dispatch"] = {"enabled": False}
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "dispatch disabled" in response.text
    assert "overdue" not in response.text


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