from __future__ import annotations

import threading
import shutil
from copy import deepcopy
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.configuration import ConfigStore
from agency.fs.snapshot import compute_source_digest


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
    (blueprint / "AGENTS.md").write_text(
        f"# {title}\n\nShared instructions.\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (skill / "checklist.md").write_text("- one\n", encoding="utf-8")


def _seed_library_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    newsletter_root = tmp_path / "groups" / "newsletter"
    product_root = tmp_path / "groups" / "product"
    for group_root in (newsletter_root, product_root):
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
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["groups"] = {
        "newsletter": {
            "name": "Newsletter",
            "workspace_path": str(newsletter_root),
            "path": str(newsletter_root),
            "default_integration": "copilot",
            "agents": [
                {
                    "name": "advisor",
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "identity": {"display_name": "Advisor"},
                    "routines": [
                        {
                            "id": "daily-review",
                            "skill": "daily-review",
                            "schedule": {"at": "09:00"},
                            "memory": {"scope": "routine"},
                        }
                    ],
                }
            ],
            "workspaces": [],
        },
        "product": {
            "name": "Product",
            "workspace_path": str(product_root),
            "path": str(product_root),
            "default_integration": "copilot",
            "agents": [
                {
                    "name": "strategist",
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "identity": {"display_name": "Strategist"},
                }
            ],
            "workspaces": [],
        },
    }

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path, library_root, cache_root


def _config_revision(config_path: Path) -> str:
    return ConfigStore(config_path).load().revision


def test_library_detail_shows_standard_files_and_users(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, _, _ = _seed_library_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/agent-library/blueprints/advisor")

    assert response.status_code == 200
    assert "AGENTS.md" in response.text
    assert "daily-review" in response.text
    assert "Used by 2 instances" in response.text
    assert "checklist.md" in response.text


def test_library_skill_alias_route_resolves(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, _, _ = _seed_library_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/agent-library/blueprints/advisor/skills")

    assert response.status_code == 200
    assert "daily-review" in response.text
    assert "SKILL.md" in response.text


def test_library_list_handles_missing_root_actionably(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    shutil.rmtree(library_root)

    response = client.get("/admin/agent-library")

    assert response.status_code == 409
    assert "Agent Library root does not exist" in response.text


def test_library_source_write_rejects_stale_digest(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    blueprint_root = library_root / "advisor"
    stale_digest = compute_source_digest(())
    current_content = (blueprint_root / "AGENTS.md").read_text(
        encoding="utf-8",
    )

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": "AGENTS.md",
            "expected_digest": stale_digest,
            "content": current_content + "Updated\n",
        },
    )

    assert response.status_code == 409
    assert (
        blueprint_root / "AGENTS.md"
    ).read_text(encoding="utf-8") == current_content


def test_library_source_write_keeps_infra_outside_source_root(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    blueprint_root = library_root / "advisor"
    digest = compute_source_digest(
        app_mod.build_services(tmp_path / "config.yaml")
        .blueprint_library.inspect("advisor")
        .snapshot.files,
    )

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": "AGENTS.md",
            "expected_digest": digest,
            "content": "# Advisor\n\nUpdated.\n",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert not any(child.name == "_locks" for child in library_root.iterdir())
    assert not any(child.name.startswith(".agency-agent-library") for child in library_root.iterdir())
    assert (blueprint_root / "AGENTS.md").read_text(encoding="utf-8") == "# Advisor\n\nUpdated.\n"
    assert compute_source_digest(
        app_mod.build_services(tmp_path / "config.yaml")
        .blueprint_library.inspect("advisor")
        .snapshot.files,
    ) != digest


def test_library_skill_write_rejects_nonstandard_path(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    inspection = app_mod.build_services(
        tmp_path / "config.yaml"
    ).blueprint_library.inspect("advisor")

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": ".agents/skills/daily-review/../../escape.md",
            "expected_digest": inspection.snapshot.digest,
            "content": "bad\n",
        },
    )

    assert response.status_code == 409
    assert not (library_root / "advisor" / "escape.md").exists()


def test_library_source_write_serializes_concurrent_saves(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    inspection = app_mod.build_services(tmp_path / "config.yaml").blueprint_library.inspect("advisor")
    digest = inspection.snapshot.digest
    first_done = threading.Event()
    second_done = threading.Event()
    responses: list[int] = []

    def save(content: str, done: threading.Event) -> None:
        response = client.post(
            "/admin/agent-library/blueprints/advisor/source",
            data={
                "path": "AGENTS.md",
                "expected_digest": digest,
                "content": content,
            },
            follow_redirects=False,
        )
        responses.append(response.status_code)
        done.set()

    first = threading.Thread(target=save, args=("# Advisor\n\nFirst.\n", first_done))
    second = threading.Thread(target=save, args=("# Advisor\n\nSecond.\n", second_done))

    first.start()
    second.start()
    assert first_done.wait(10)
    assert second_done.wait(10)
    first.join(10)
    second.join(10)

    assert sorted(responses) == [303, 409]
    assert not any(child.name == "_locks" for child in library_root.iterdir())


def test_integrations_page_shows_projector_compatibility(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, _, _ = _seed_library_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/integrations")

    assert response.status_code == 200
    assert "Projector version" in response.text
    assert "Instruction target" in response.text
    assert "Skills target" in response.text
    assert "Routine compatibility" in response.text
