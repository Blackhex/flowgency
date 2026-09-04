from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

import agency.app as app_mod
from agency.app import app, is_agent_running
from agency.configuration import ConfigStore, PromptSelector
from agency.configuration.models import MemorySelector
from agency.jobs import JobRequest
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.prompts import PromptStore
from agency.jobs.store import write_job
from tests._team_helpers import create_team_environment


def _setup_team(tmp_path: Path) -> Path:
    paths = create_team_environment(
        tmp_path,
        "test",
        team_dirs=("prompts", "logs"),
    )
    team_path = paths.state_root
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory"
    prompt_store = tmp_path / "prompt-store"
    prompts = paths.state_root / "prompts"
    (prompts / "routine.md").write_text("# Routine\n")
    (prompts / "product-routine.md").write_text("# Product routine\n")
    (prompts / "other-routine.md").write_text("# Other routine\n")
    (prompts / "_observation-system-steps.md").write_text("# System\n")
    prompt_dir = library_root / "builder-blueprint" / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (library_root / "builder-blueprint" / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "product-routine.prompt.md").write_text(
        "---\nname: product-routine\ndescription: Product routine\n---\n\nRun product routine.\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "schema_version: 6\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_team: test\n"
        "  ai_backend: claude-code\n"
        f"  agent_library: {library_root.as_posix()}\n"
        f"  compilation_cache: {cache_root.as_posix()}\n"
        f"  memory_store: {memory_root.as_posix()}\n"
        f"  prompt_store: {prompt_store.as_posix()}\n"
        "teams:\n"
        "  test:\n"
        "    name: Test\n"
        f"    workspace_path: {paths.workspace_root.as_posix()}\n"
        f"    path: {team_path.as_posix()}\n"
        "    default_integration: script\n"
        "    agents:\n"
        "      - name: product\n"
        "        blueprint: builder-blueprint\n"
        "        integration: script\n"
        "        routines:\n"
        "          - id: daily-review\n"
        "            prompt:\n"
        "              scope: blueprint\n"
        "              name: daily-review\n"
        "            arguments:\n"
        "              - --mode=review\n"
        "              - literal value\n"
        "            schedule:\n"
        "              every: 6h\n"
        "            memory:\n"
        "              scope: routine\n"
        "          - id: product-routine\n"
        "            prompt:\n"
        "              scope: blueprint\n"
        "              name: product-routine\n"
        "            schedule:\n"
        "              every: 6h\n",
        encoding="utf-8",
    )
    app_mod.CONFIG_PATH = config_path
    app_mod.refresh_services()
    return team_path


def _configure_schedule(routine_id: str) -> None:
    config = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    for agent in config["teams"]["test"]["agents"]:
        if agent["name"] != "product":
            continue
        for routine in agent.get("routines", []):
            if routine["id"] == routine_id:
                routine["schedule"] = {"every": "6h"}
        break
    app_mod.CONFIG_PATH.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    app_mod.refresh_services()


def test_run_returns_202_and_schedules(tmp_path, monkeypatch):
    team_path = _setup_team(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={
            "routine_id": "daily-review",
            "mode": "saved",
            "prompt_scope": "blueprint",
            "prompt_name": "daily-review",
        },
    )

    assert resp.status_code == 202
    assert resp.json() == {"status": "started", "job_id": "job-1"}
    assert len(calls) == 1
    request = calls[0]
    assert isinstance(request, JobRequest)
    assert request.trigger == "manual_prompt"
    assert request.team_key == "test"
    assert request.agent_name == "product"
    assert request.routine_id == "daily-review"
    assert request.task_input == ""
    assert request.prompt == PromptSelector(scope="blueprint", name="daily-review")
    assert request.timeout_override is None
    assert not (team_path / "product").exists()


def test_run_renders_routine_arguments_in_task_input(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={
            "routine_id": "daily-review",
            "mode": "saved",
            "prompt_scope": "blueprint",
            "prompt_name": "daily-review",
        },
    )

    assert resp.status_code == 202
    assert calls[0].invocation_input == ""


def test_run_unknown_routine_404(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={"routine_id": "nope", "mode": "saved", "prompt_scope": "blueprint", "prompt_name": "daily-review"},
    )

    assert resp.status_code == 404


def test_run_invalid_routine_id_400(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={"routine_id": "../secret", "mode": "saved", "prompt_scope": "blueprint", "prompt_name": "daily-review"},
    )

    assert resp.status_code == 400


def test_run_returns_400_when_blueprint_prompt_disappears_after_validation(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    prompt_path = (
        tmp_path / "agent-library" / "builder-blueprint" / ".agents" / "prompts" / "daily-review.prompt.md"
    )
    prompt_path.unlink()
    monkeypatch.setattr(
        app_mod,
        "resolve_catalog_prompt",
        lambda *args, **kwargs: SimpleNamespace(
            scope="blueprint",
            document=SimpleNamespace(name="daily-review"),
            source_path=".agents/prompts/daily-review.prompt.md",
        ),
    )
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={
            "routine_id": "daily-review",
            "mode": "saved",
            "prompt_scope": "blueprint",
            "prompt_name": "daily-review",
        },
    )

    assert resp.status_code == 400
    assert "daily-review" in resp.json()["detail"]


def test_run_returns_400_when_private_prompt_disappears_after_validation(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    config = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    config["teams"]["test"]["agents"][0]["prompts"] = ["local-triage"]
    app_mod.CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    prompt_store = PromptStore(tmp_path / "prompt-store")
    created = prompt_store.create(
        "test",
        "product",
        "local-triage",
        (
            "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
            "Review local work.\n"
        ).encode("utf-8"),
    )
    app_mod.refresh_services()
    prompt_store.delete(
        "test",
        "product",
        "local-triage",
        expected_digest=created.document.digest,
    )
    monkeypatch.setattr(
        app_mod,
        "resolve_catalog_prompt",
        lambda *args, **kwargs: SimpleNamespace(
            scope="instance",
            document=SimpleNamespace(name="local-triage"),
            source_path=str(prompt_store.path("test", "product", "local-triage")),
        ),
    )
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={
            "mode": "saved",
            "prompt_scope": "instance",
            "prompt_name": "local-triage",
            "invocation_input": "triage the update",
        },
    )

    assert resp.status_code == 400
    assert "local-triage" in resp.json()["detail"]


def test_run_allows_concurrent_jobs_for_same_agent(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id=f"job-{len(calls)}"))
    client = TestClient(app)

    payload = {"routine_id": "daily-review", "mode": "saved", "prompt_scope": "blueprint", "prompt_name": "daily-review"}
    assert client.post("/test/agents/product/run", data=payload).status_code == 202
    assert client.post("/test/agents/product/run", data=payload).status_code == 202
    assert len(calls) == 2


def test_agent_running_state_comes_from_active_job_records(tmp_path):
    team_path = _setup_team(tmp_path)
    job_store = JobStore(tmp_path / "memory")
    team_store = job_store.team_root("test")
    team_store.mkdir(parents=True, exist_ok=True)
    for status in ("queued", "running"):
        spec = JobSpec(
            schema_version=5,
            job_id=f"job-{status}",
            config_path=str((tmp_path / "config.yaml").resolve()),
            config_revision="cfg-1",
            team_key="test",
            team_root=str(team_path.resolve()),
            agent_name="product",
            workspace_root=str(team_path.resolve()),
            trigger="manual_prompt",
            integration_name="script",
            integration_config={},
            blueprint=BlueprintRef(
                key="builder-blueprint",
                source_digest="digest-1",
                integration="script",
                projector_version="v1",
                cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
            ),
            routine_id="daily-review",
            skill=None,
            skill_arguments=(),
            task_input="# Routine\n",
            runtime_policy=RuntimePolicySnapshot(
                timeout=1800,
                mode="unrestricted",
            ),
            memory=MemoryBinding(
                selector={"scope": "agent", "version": 1, "team": "test", "agent": "product"},
                canonical_json='{"agent":"product","team":"test","scope":"agent","version":1}',
                memory_hash="memory-hash-1",
                path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
            ),
            trigger_context=None,
            prompt_source={"type": "blueprint_prompt", "scope": "blueprint", "name": "daily-review", "source_path": ".agents/prompts/daily-review.prompt.md", "source_digest": "digest-1"},
            timeout_override=None,
            created_at="2026-07-15T00:00:00+00:00",
            private_prompts=(),
        )
        record = replace(JobRecord.from_spec(spec), status=status)
        write_job(job_store.path("test", spec.job_id), record)

    assert not (team_path / "logs" / ".running-product").exists()
    assert is_agent_running(app_mod.get_team("test"), "product") is True


def test_run_accepts_valid_selector_override_for_routine(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={"routine_id": "daily-review", "mode": "saved", "prompt_scope": "blueprint", "prompt_name": "daily-review", "memory_scope": "routine"},
    )

    assert resp.status_code == 202
    assert calls[0].memory_override == MemorySelector(scope="routine")


def test_run_rejects_invalid_selector_override_for_routine(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post(
        "/test/agents/product/run",
        data={"routine_id": "daily-review", "mode": "saved", "prompt_scope": "blueprint", "prompt_name": "daily-review", "memory_scope": "channel"},
    )

    assert resp.status_code == 400


def test_run_accepts_valid_channel_memory_override(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    config = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    config["memory"] = {"channels": {"support": {"display_name": "Support"}}}
    app_mod.CONFIG_PATH.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    calls = []
    monkeypatch.setattr(
        "agency.app.submit_job_request",
        lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"),
    )
    client = TestClient(app)

    response = client.post(
        "/test/agents/product/run",
        data={
            "mode": "saved",
            "prompt_scope": "blueprint",
            "prompt_name": "daily-review",
            "memory_scope": "channel",
            "memory_channel": "support",
        },
    )

    assert response.status_code == 202
    assert calls[0].memory_override == MemorySelector(
        scope="channel",
        channel="support",
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "mode": "saved",
                "prompt_scope": "blueprint",
                "prompt_name": "daily-review",
                "memory_scope": "agent",
            },
            id="saved",
        ),
        pytest.param(
            {
                "mode": "one-off",
                "task_input": "Inspect the current suite.",
                "memory_scope": "agent",
            },
            id="one-off",
        ),
    ],
)
def test_run_submits_typed_memory_override_for_manual_modes(
    tmp_path,
    monkeypatch,
    payload,
):
    _setup_team(tmp_path)
    calls = []
    monkeypatch.setattr(
        "agency.app.submit_job_request",
        lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"),
    )
    client = TestClient(app)

    response = client.post("/test/agents/product/run", data=payload)

    assert response.status_code == 202
    assert isinstance(calls[0].memory_override, MemorySelector)
    assert calls[0].memory_override == MemorySelector(scope="agent")


def test_agents_page_lists_prompts_with_run(tmp_path):
    _setup_team(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "Instances assigned to Test" in resp.text
    assert "Blueprint: builder-blueprint" in resp.text
    assert "product-routine" in resp.text
    assert "Run prompt" in resp.text
    assert "/test/prompts/" not in resp.text


def test_agents_page_excludes_unrelated_and_system_prompts(tmp_path):
    _setup_team(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert 'data-prompt="_observation-system-steps.md"' not in resp.text
    assert 'data-prompt="other-routine.md"' not in resp.text
    assert 'data-prompt="routine.md"' not in resp.text
    assert "Move" in resp.text
    assert "Remove" in resp.text


def test_agents_page_shows_config_only_roster_without_activity_links(tmp_path):
    _setup_team(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-" not in resp.text
    assert "last run stdout log" not in resp.text


def test_exact_dispatch_slug_does_not_resolve_to_generic_prompt_routes(tmp_path):
    _setup_team(tmp_path)
    client = TestClient(app)

    detail_response = client.get("/test/prompts/dispatch")
    save_response = client.post(
        "/test/prompts/dispatch/save",
        data={"content": "# should not save\n"},
        follow_redirects=False,
    )

    assert detail_response.status_code == 404
    assert save_response.status_code == 404


@pytest.mark.parametrize(
    "prompt",
    ["missing", "_observation-system-steps"],
)
def test_agents_page_does_not_render_retired_schedule_links(
    tmp_path,
    prompt,
):
    _setup_team(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert 'href="/admin/teams/test/edit#rules-product"' not in resp.text
    assert 'href="/test/prompts#schedule-product-0"' not in resp.text


def test_agents_page_keeps_roster_layout_when_logs_exist(tmp_path):
    team_path = _setup_team(tmp_path)
    day = team_path / "logs" / "2026-07-11"
    day.mkdir()
    (day / "product-error.err").write_text("run failure")
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "product" in resp.text
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-" not in resp.text
    assert "last run stdout log" not in resp.text


def test_agents_page_running_status_has_no_time_links(tmp_path, monkeypatch):
    _setup_team(tmp_path)
    monkeypatch.setattr(app_mod, "is_agent_running", lambda *args, **kwargs: True)
    job_store = JobStore(tmp_path / "memory")
    team_store = job_store.team_root("test")
    team_store.mkdir(parents=True, exist_ok=True)
    spec = JobSpec(
        schema_version=5,
        job_id="job-running",
        config_path=str((tmp_path / "config.yaml").resolve()),
        config_revision=ConfigStore(tmp_path / "config.yaml").load().revision,
        team_key="test",
        team_root=str((tmp_path / "grp").resolve()),
        agent_name="product",
        workspace_root=str((tmp_path / "grp").resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="builder-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
        ),
        routine_id="daily-review",
        skill=None,
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "team": "test", "agent": "product"},
            canonical_json='{"agent":"product","team":"test","scope":"agent","version":1}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "blueprint_prompt", "scope": "blueprint", "name": "daily-review", "source_path": ".agents/prompts/daily-review.prompt.md", "source_digest": "digest-1"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
        private_prompts=(),
    )
    write_job(job_store.path("test", spec.job_id), JobRecord.from_spec(spec))
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "Queued" in resp.text
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-product-0" not in resp.text
