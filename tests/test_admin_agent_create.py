from copy import deepcopy

import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.configuration import ConfigStore
from tests._group_helpers import apply_group_paths, create_group_environment


def _write_blueprint(root, key, title):
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _seed_client(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    config_path = tmp_path / "config.yaml"
    paths = create_group_environment(tmp_path, "grp")
    group_root = paths.state_root
    library_root = tmp_path / "library"
    cache_root = tmp_path / "cache"
    memory_root = tmp_path / "memory"
    _write_blueprint(library_root, "advisor", "Advisor")
    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["groups"] = {
        "grp": apply_group_paths({
            "name": "Group",
            "default_integration": "copilot",
            "agents": [],
            "workspaces": [],
        }, paths)
    }
    raw["agency"]["default_group"] = "grp"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), config_path, group_root


def _revision(config_path):
    return ConfigStore(config_path).load().revision


def test_roster_create_adds_config_instance_without_scaffolding(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_client(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/grp/agents/create",
        data={
            "revision": revision,
            "name": "reviewer",
            "blueprint": "advisor",
            "integration": "copilot",
            "display_name": "Reviewer",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    created = saved["groups"]["grp"]["agents"][0]
    assert created["name"] == "reviewer"
    assert created["blueprint"] == "advisor"
    assert created["integration"] == "copilot"
    assert created["identity"]["display_name"] == "Reviewer"
    assert not (group_root / "reviewer").exists()


def test_roster_create_rejects_invalid_blueprint_without_partial_files(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_client(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/grp/agents/create",
        data={
            "revision": revision,
            "name": "reviewer",
            "blueprint": "missing-blueprint",
            "integration": "copilot",
            "display_name": "Reviewer",
        },
    )

    assert response.status_code == 409
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["groups"]["grp"]["agents"] == []
    assert not (group_root / "reviewer").exists()