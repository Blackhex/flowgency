from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from agency.jobs.authority import JobStore
from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)
from agency.jobs.store import write_job
from tests._group_helpers import apply_group_paths, create_group_environment
from agency.configuration import ConfigStore
from agency import app as app_mod


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
    newsletter_paths = create_group_environment(tmp_path, "newsletter")
    research_paths = create_group_environment(tmp_path, "research")
    group_root = newsletter_paths.state_root
    target_root = research_paths.state_root
    _write_blueprint(library_root, "advisor", "Advisor")
    _write_blueprint(library_root, "builder-blueprint", "Builder")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["groups"]["newsletter"]["name"] = "Newsletter"
    apply_group_paths(raw["groups"]["newsletter"], newsletter_paths)
    raw["groups"]["newsletter"]["default_integration"] = "copilot"
    raw["groups"]["newsletter"]["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "identity": {"display_name": "Advisor", "title": "Blueprint Librarian"},
        }
    ]
    raw["groups"]["research"] = {
        **apply_group_paths({}, research_paths),
        "name": "Research",
        "default_integration": "copilot",
        "agents": [],
    }

    authority = JobStore(memory_root)
    authority.group_root("newsletter").mkdir(parents=True, exist_ok=True)
    authority.group_root("research").mkdir(parents=True, exist_ok=True)

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app_mod.app), config_path, group_root


def _revision(config_path: Path) -> str:
    return ConfigStore(config_path).load().revision


def _all_route_paths() -> list[str]:
    paths: list[str] = []
    for route in app_mod.app.routes:
        path = getattr(route, "path", "")
        if isinstance(route, BaseRoute) and path:
            paths.append(path)
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if not callable(effective_route_contexts):
            continue
        for route_context in effective_route_contexts():
            starlette_route = getattr(route_context, "starlette_route", None)
            effective_path = getattr(starlette_route, "path", "") or getattr(
                getattr(route_context, "original_route", None),
                "path",
                "",
            )
            if effective_path:
                paths.append(effective_path)
    return paths


def _roster_job_spec(tmp_path: Path, group_root: Path, *, job_id: str, created_at: str) -> JobSpec:
    return JobSpec(
        schema_version=3,
        job_id=job_id,
        config_path=str((tmp_path / "config.yaml").resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_root=str(group_root.resolve()),
        agent_name="advisor",
        workspace_root=str(group_root.resolve()),
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path="C:/cache/copilot/v1/digest-1",
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=(str(group_root.resolve()),),
            tool_mode="allowlist",
            tool_names=("shell",),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": job_id},
            canonical_json='{"job":"' + job_id + '","scope":"run","version":1}',
            memory_hash="a" * 64,
            path=f"C:/memory/{job_id}",
        ),
        trigger_context={"source": "test"},
        prompt_source={"type": "routine", "routine_id": "daily-review"},
        timeout_override=None,
        created_at=created_at,
    )


def _write_roster_job(
    tmp_path: Path,
    group_root: Path,
    *,
    job_id: str,
    status: str,
    created_at: str,
) -> JobRecord:
    spec = _roster_job_spec(tmp_path, group_root, job_id=job_id, created_at=created_at)
    record = JobRecord.from_spec(spec)
    record.status = status
    write_job(JobStore(tmp_path / "memory-store").path("newsletter", job_id), record)
    return record


def test_agents_page_is_instance_roster(monkeypatch, tmp_path, raw_config):
    client, _, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents")

    assert response.status_code == 200
    assert "Instances assigned to Newsletter" in response.text
    assert "advisor" in response.text
    assert "Blueprint" in response.text
    assert "Subagents" not in response.text
    assert "headshot" not in response.text.lower()
    assert '<select name="blueprint"' in response.text
    assert '<option value="advisor">advisor</option>' in response.text
    assert (
        "return window.confirm('Remove Advisor from Newsletter?')"
        in response.text
    )


def test_create_instance_from_roster(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/create",
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
    created = next(
        agent for agent in saved["groups"]["newsletter"]["agents"] if agent["name"] == "reviewer"
    )
    assert created["blueprint"] == "advisor"
    assert created["integration"] == "copilot"
    assert created["identity"]["display_name"] == "Reviewer"


def test_remove_instance_updates_config_only(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    preexisting_dir = group_root / "advisor"
    preexisting_dir.mkdir(parents=True)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/remove",
        data={"confirm": "true", "revision": revision},
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["groups"]["newsletter"]["agents"] == []
    assert preexisting_dir.is_dir()


def test_move_preview_and_apply(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    preview = client.post(
        "/newsletter/agents/advisor/move",
        data={"target_group": "research", "memory_mode": "empty", "revision": revision},
    )

    assert preview.status_code == 200
    assert "Move advisor to Research" in preview.text
    assert "empty" in preview.text.lower()
    assert revision in preview.text

    apply = client.post(
        "/newsletter/agents/advisor/move/apply",
        data={"target_group": "research", "memory_mode": "empty", "preview_revision": revision},
        follow_redirects=False,
    )

    assert apply.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["groups"]["newsletter"]["agents"] == []
    moved = next(
        agent for agent in saved["groups"]["research"]["agents"] if agent["name"] == "advisor"
    )
    assert moved["blueprint"] == "advisor"


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("queued", "Queued"),
        ("waiting_for_memory", "Waiting for memory"),
        ("running", "Running"),
    ],
)
def test_roster_shows_exact_active_job_state(monkeypatch, tmp_path, raw_config, status, label):
    client, _, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_roster_job(
        tmp_path,
        group_root,
        job_id="job-1",
        status=status,
        created_at="2026-07-15T00:00:00+00:00",
    )

    response = client.get("/newsletter/agents")

    assert response.status_code == 200
    assert label in response.text
    assert '/newsletter/jobs/job-1' in response.text
    assert "a" * 64 not in response.text


def test_roster_without_active_job_omits_job_badge(monkeypatch, tmp_path, raw_config):
    client, _, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents")

    assert response.status_code == 200
    assert '/newsletter/jobs/' not in response.text
    assert 'title="Queued job awaiting execution"' not in response.text
    assert 'title="Job is waiting for memory publication"' not in response.text
    assert 'title="Job is currently executing"' not in response.text


def test_roster_uses_newest_active_job_deterministically(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_roster_job(
        tmp_path,
        group_root,
        job_id="job-older",
        status="queued",
        created_at="2026-07-15T00:00:00+00:00",
    )
    _write_roster_job(
        tmp_path,
        group_root,
        job_id="job-newer",
        status="waiting_for_memory",
        created_at="2026-07-16T00:00:00+00:00",
    )

    response = client.get("/newsletter/agents")

    assert response.status_code == 200
    assert '/newsletter/jobs/job-newer' in response.text
    assert '/newsletter/jobs/job-older' not in response.text
    assert 'Waiting for memory' in response.text


def test_old_admin_agent_get_redirects_to_profile(monkeypatch, tmp_path, raw_config):
    client, _, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/admin/orgs/newsletter/agents/advisor", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents/advisor/profile"


def test_profile_detail_uses_config_identity(monkeypatch, tmp_path, raw_config):
    client, _, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/profile")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Blueprint Librarian" in response.text
    assert "Blueprint: advisor" in response.text
    assert "copilot" in response.text
    assert 'aria-current="page">Profile' in response.text


def test_stale_create_revision_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    stale = store.load().revision
    store.patch(stale, lambda raw: raw["groups"]["newsletter"].__setitem__("name", "Changed"))

    response = client.post(
        "/newsletter/agents/create",
        data={
            "revision": stale,
            "name": "reviewer",
            "blueprint": "advisor",
            "integration": "copilot",
            "display_name": "Reviewer",
        },
    )

    assert response.status_code == 409
    assert "reload" in response.text.lower()


def test_roster_returns_actionable_warning_when_agent_library_is_unavailable(
    monkeypatch, tmp_path, raw_config
):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    missing_root = tmp_path / "missing-library"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["agency"]["agent_library"] = str(missing_root)
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    before = config_path.read_text(encoding="utf-8")
    response = client.get("/newsletter/agents")

    assert response.status_code == 409
    assert "Instance services unavailable" in response.text
    assert config_path.read_text(encoding="utf-8") == before


def test_stale_move_preview_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    stale = store.load().revision
    store.patch(stale, lambda raw: raw["groups"]["newsletter"].__setitem__("name", "Changed"))

    response = client.post(
        "/newsletter/agents/advisor/move",
        data={"target_group": "research", "memory_mode": "empty", "revision": stale},
    )

    assert response.status_code == 409
    assert "reload" in response.text.lower()


def test_stale_move_apply_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    revision = store.load().revision
    preview = client.post(
        "/newsletter/agents/advisor/move",
        data={"target_group": "research", "memory_mode": "empty", "revision": revision},
    )
    assert preview.status_code == 200
    store.patch(revision, lambda raw: raw["groups"]["newsletter"].__setitem__("name", "Changed"))

    response = client.post(
        "/newsletter/agents/advisor/move/apply",
        data={"target_group": "research", "memory_mode": "empty", "preview_revision": revision},
    )

    assert response.status_code == 409
    assert "reload" in response.text.lower()


def test_removed_mutation_routes_are_absent_from_route_table(monkeypatch, tmp_path, raw_config):
    _seed_app(monkeypatch, tmp_path, raw_config)
    route_paths = set(_all_route_paths())

    for path in {
        "/admin/orgs/{group}/agents/create",
        "/admin/orgs/{group}/agents/{agent}/save",
        "/admin/orgs/{group}/agents/{agent}/rename",
        "/admin/orgs/{group}/agents/{agent}/delete",
        "/admin/orgs/{org}/agents/create",
        "/admin/orgs/{org}/agents/{agent}/save",
        "/admin/orgs/{org}/agents/{agent}/rename",
        "/admin/orgs/{org}/agents/{agent}/delete",
        "/{group}/agents/{agent}/identity",
        "/{group}/agents/{agent}/definition",
        "/{group}/agents/{agent}/upload-headshot",
        "/{group}/agents/{agent}/headshot",
        "/{group}/agents/{agent}/toggle-subagent",
    }:
        assert path not in route_paths


def test_task14_removed_mutation_routes_are_unregistered_and_nonmutating(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()
    route_paths = set(_all_route_paths())

    for path in {
        "/admin/orgs/{org}/initialize",
        "/admin/orgs/{org}/autodetect",
        "/admin/orgs/{org}/dispatch",
    }:
        assert path not in route_paths

    for url in {
        "/admin/orgs/newsletter/initialize",
        "/admin/orgs/newsletter/autodetect",
        "/admin/orgs/newsletter/dispatch",
    }:
        response = client.post(url, follow_redirects=False)
        assert response.status_code == 404
        assert config_path.read_bytes() == before


def test_task14_route_ownership_is_unique_and_canonical(monkeypatch, tmp_path, raw_config):
    client, config_path, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    route_paths = _all_route_paths()

    canonical_paths = {
        "/admin/",
        "/admin/groups",
        "/admin/dispatch",
        "/admin/settings",
        "/admin/integrations",
        "/admin/integrations/register",
        "/admin/integrations/unregister",
        "/admin/integrations/restart",
        "/admin/orgs/new",
        "/admin/orgs/{org}/delete",
        "/{group}/agents/{agent}/run",
    }

    for path in canonical_paths:
        assert route_paths.count(path) == 1

    assert client.get("/admin/dispatch").status_code == 200
    assert client.post("/newsletter/agents/advisor/run", data={"routine_id": "daily-review"}).status_code in {202, 404, 400}