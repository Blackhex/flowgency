from __future__ import annotations

import os
import threading
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.web.routes import admin_library as admin_library_routes
from agency.configuration import ConfigStore
from agency.fs.snapshot import compute_source_digest
from tests._team_helpers import apply_team_paths, create_team_environment


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
    (blueprint / "AGENTS.md").write_text(
        f"# {title}\n\nShared instructions.\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Review pull requests\nargument-hint: PR URL\n---\n\nReview this pull request in detail.\n",
        encoding="utf-8",
    )
    (skill / "checklist.md").write_text("- one\n", encoding="utf-8")


def _seed_library_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    prompt_root = tmp_path / "prompts"
    newsletter_paths = create_team_environment(tmp_path, "newsletter")
    product_paths = create_team_environment(tmp_path, "product")
    for group_root in (newsletter_paths.state_root, product_paths.state_root):
        (group_root / "logs").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "observations").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "proposals").mkdir(
            parents=True,
            exist_ok=True,
        )
        (group_root / "decisions").mkdir(
            parents=True,
            exist_ok=True,
        )
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["agency"]["prompt_store"] = str(prompt_root)
    raw["teams"] = {
        "newsletter": apply_team_paths({
            "name": "Newsletter",
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
                            "prompt": {"scope": "blueprint", "name": "pr-review"},
                            "schedule": {"at": "09:00"},
                            "memory": {"scope": "routine"},
                        }
                    ],
                }
            ],
            "workspaces": [],
        }, newsletter_paths),
        "product": apply_team_paths({
            "name": "Product",
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
        }, product_paths),
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


@pytest.mark.skipif(os.name != "nt", reason="Windows path-budget regression")
def test_library_source_write_supports_long_windows_path(
    monkeypatch,
    tmp_path,
    raw_config,
):
    target_root_length = 90
    name_prefix = f"lp-{tmp_path.name[-5:]}-"
    component_length = target_root_length - len(str(tmp_path.parent)) - 1
    assert component_length >= len(name_prefix)
    long_tmp_path = tmp_path.parent / (
        name_prefix + "x" * (component_length - len(name_prefix))
    )
    assert len(str(long_tmp_path)) == target_root_length

    full_hash_stage_directory = (
        long_tmp_path
        / ".agency-agent-library"
        / ("a" * 64)
        / "staging"
        / f".{('b' * 64)}.stage-12345678"
        / "advisor"
        / ".agents"
        / "skills"
    )
    compact_stage_file = (
        long_tmp_path
        / ".agency-agent-library"
        / ("a" * 24)
        / "staging"
        / f".{('b' * 24)}.stage-12345678"
        / "advisor"
        / ".agents"
        / "skills"
        / "daily-review"
        / "checklist.md"
    )
    assert len(str(full_hash_stage_directory)) >= 248
    assert len(str(compact_stage_file)) < 248

    client = None
    try:
        client, _, library_root, _ = _seed_library_app(
            monkeypatch,
            long_tmp_path,
            raw_config,
        )
        inspection = app_mod.build_services(
            long_tmp_path / "config.yaml"
        ).blueprint_library.inspect("advisor")

        response = client.post(
            "/admin/agent-library/blueprints/advisor/source",
            data={
                "path": "AGENTS.md",
                "expected_digest": inspection.snapshot.digest,
                "content": "# Advisor\n\nLong path update.\n",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (library_root / "advisor" / "AGENTS.md").read_text(
            encoding="utf-8"
        ) == "# Advisor\n\nLong path update.\n"
        infrastructure_roots = list(
            (long_tmp_path / ".agency-agent-library").iterdir()
        )
        assert len(infrastructure_roots) == 1
        assert not any((infrastructure_roots[0] / "staging").iterdir())
        assert not any((infrastructure_roots[0] / "backups").iterdir())
    finally:
        if client is not None:
            client.close()
        shutil.rmtree(long_tmp_path, ignore_errors=True)


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


def test_blueprint_prompts_page_lists_and_edits_canonical_source(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )

    response = client.get("/admin/agent-library/blueprints/advisor/prompts")
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest

    assert response.status_code == 200
    assert "pr-review.prompt.md" in response.text
    assert ".agents/prompts/pr-review.prompt.md" in response.text
    assert "Source digest" in response.text
    assert digest in response.text

    inspection = app_mod.build_services(config_path).blueprint_library.inspect("advisor")
    save_response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": ".agents/prompts/pr-review.prompt.md",
            "expected_digest": inspection.snapshot.digest,
            "content": "---\nname: pr-review\ndescription: Updated\n---\n\nUpdated body.\n",
        },
        follow_redirects=False,
    )

    assert save_response.status_code == 303
    assert save_response.headers["location"] == (
        "/admin/agent-library/blueprints/advisor/prompts?"
        "path=.agents/prompts/pr-review.prompt.md"
    )


def test_blueprint_prompt_create_from_slug(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    inspection = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    )

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "slug": "retro",
            "expected_digest": inspection.snapshot.digest,
            "content": "---\nname: retro\ndescription: Sprint retro\n---\n\nDiscuss wins and risks.\n",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        library_root / "advisor" / ".agents" / "prompts" / "retro.prompt.md"
    ).is_file()


def test_blueprint_prompt_create_rejects_invalid_path(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    inspection = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    )

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": ".agents/prompts/retro.md",
            "expected_digest": inspection.snapshot.digest,
            "content": "---\nname: retro\ndescription: Retro\n---\n\nBody\n",
        },
    )

    assert response.status_code == 409
    assert not (library_root / "advisor" / ".agents" / "prompts" / "retro.md").exists()


def test_blueprint_prompt_create_rejects_path_traversal(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    inspection = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    )

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": ".agents/prompts/../../escape.prompt.md",
            "expected_digest": inspection.snapshot.digest,
            "content": "---\nname: escape\ndescription: Escape\n---\n\nEscape\n",
        },
    )

    assert response.status_code == 409
    assert not (library_root / "advisor" / "escape.prompt.md").exists()


def test_blueprint_prompt_save_rejects_invalid_frontmatter_and_keeps_source(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    inspection = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    )
    prompt_path = (
        library_root / "advisor" / ".agents" / "prompts" / "pr-review.prompt.md"
    )
    previous = prompt_path.read_text(encoding="utf-8")

    response = client.post(
        "/admin/agent-library/blueprints/advisor/source",
        data={
            "path": ".agents/prompts/pr-review.prompt.md",
            "expected_digest": inspection.snapshot.digest,
            "content": "---\nname: wrong-name\ndescription: Bad\n---\n\nBody\n",
        },
    )

    assert response.status_code == 409
    assert "must exactly match" in response.text
    assert prompt_path.read_text(encoding="utf-8") == previous


def test_blueprint_prompt_delete_blocks_configured_routine(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest

    response = client.post(
        "/admin/agent-library/blueprints/advisor/prompts/pr-review/delete",
        data={"expected_digest": digest, "confirmation": "pr-review"},
    )

    assert response.status_code == 409
    assert "prompt is used by" in response.text.lower()
    assert (
        library_root
        / "advisor"
        / ".agents"
        / "prompts"
        / "pr-review.prompt.md"
    ).is_file()


def test_blueprint_prompt_delete_rejects_stale_digest(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, _, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )

    response = client.post(
        "/admin/agent-library/blueprints/advisor/prompts/daily-review/delete",
        data={
            "expected_digest": compute_source_digest(()),
            "confirmation": "daily-review",
        },
    )

    assert response.status_code == 409
    assert (
        library_root
        / "advisor"
        / ".agents"
        / "prompts"
        / "daily-review.prompt.md"
    ).is_file()


def test_blueprint_prompt_delete_removes_unreferenced_prompt(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest

    response = client.post(
        "/admin/agent-library/blueprints/advisor/prompts/daily-review/delete",
        data={"expected_digest": digest, "confirmation": "daily-review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/agent-library/blueprints/advisor/prompts"
    assert not (
        library_root
        / "advisor"
        / ".agents"
        / "prompts"
        / "daily-review.prompt.md"
    ).exists()


def test_blueprint_prompt_delete_requires_confirmation(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest

    response = client.post(
        "/admin/agent-library/blueprints/advisor/prompts/daily-review/delete",
        data={"expected_digest": digest},
    )

    assert response.status_code == 409
    assert "type daily-review to confirm" in response.text.lower()
    assert (
        library_root
        / "advisor"
        / ".agents"
        / "prompts"
        / "daily-review.prompt.md"
    ).is_file()


def test_blueprint_prompt_delete_rejects_wrong_confirmation(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest

    response = client.post(
        "/admin/agent-library/blueprints/advisor/prompts/daily-review/delete",
        data={"expected_digest": digest, "confirmation": "wrong"},
    )

    assert response.status_code == 409
    assert "type daily-review to confirm" in response.text.lower()
    assert (
        library_root
        / "advisor"
        / ".agents"
        / "prompts"
        / "daily-review.prompt.md"
    ).is_file()


def test_blueprint_prompt_delete_rolls_back_full_tree_on_publish_failure(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, library_root, _ = _seed_library_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    blueprint_root = library_root / "advisor"
    digest = app_mod.build_services(config_path).blueprint_library.inspect(
        "advisor"
    ).snapshot.digest
    original_tree = {
        path.relative_to(blueprint_root).as_posix(): path.read_bytes()
        for path in blueprint_root.rglob("*")
        if path.is_file()
    }
    real_replace = admin_library_routes.os.replace

    def failing_replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path.name == "advisor"
            and destination_path == blueprint_root
            and "staging" in source_path.as_posix()
        ):
            raise PermissionError("simulated publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(
        admin_library_routes.os,
        "replace",
        failing_replace,
    )

    with pytest.raises(PermissionError, match="simulated publish failure"):
        client.post(
            "/admin/agent-library/blueprints/advisor/prompts/daily-review/delete",
            data={"expected_digest": digest, "confirmation": "daily-review"},
        )

    restored_tree = {
        path.relative_to(blueprint_root).as_posix(): path.read_bytes()
        for path in blueprint_root.rglob("*")
        if path.is_file()
    }
    assert restored_tree == original_tree
    assert (
        blueprint_root / ".agents" / "prompts" / "daily-review.prompt.md"
    ).is_file()
    assert (
        blueprint_root / ".agents" / "prompts" / "pr-review.prompt.md"
    ).is_file()
