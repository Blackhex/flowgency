from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

import agency.app as app_mod
from agency.app import app, is_agent_running
from agency.configuration import ConfigStore
from agency.jobs import JobRequest
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import write_job


def _setup_group(tmp_path: Path) -> Path:
    group_path = tmp_path / "grp"
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory"
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("# Routine\n")
    (prompts / "product-routine.md").write_text("# Product routine\n")
    (prompts / "other-routine.md").write_text("# Other routine\n")
    (prompts / "_observation-system-steps.md").write_text("# System\n")
    (group_path / "shared" / "logs").mkdir(parents=True)
    skill = library_root / "builder-blueprint" / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True, exist_ok=True)
    (library_root / "builder-blueprint" / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "schema_version: 2\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_group: test\n"
        "  ai_backend: claude-code\n"
        f"  agent_library: {library_root.as_posix()}\n"
        f"  compilation_cache: {cache_root.as_posix()}\n"
        f"  memory_store: {memory_root.as_posix()}\n"
        "groups:\n"
        "  test:\n"
        "    name: Test\n"
        f"    path: {group_path.as_posix()}\n"
        "    default_integration: script\n"
        "    agents:\n"
        "      - name: product\n"
        "        blueprint: builder-blueprint\n"
        "        integration: script\n"
        "        routines:\n"
        "          - id: daily-review\n"
        "            skill: daily-review\n"
        "            arguments:\n"
        "              - --mode=review\n"
        "              - literal value\n"
        "            schedule:\n"
        "              every: 6h\n"
        "            memory:\n"
        "              scope: routine\n"
        "          - id: product-routine\n"
        "            skill: product-routine\n"
        "            schedule:\n"
        "              every: 6h\n",
        encoding="utf-8",
    )
    app_mod.CONFIG_PATH = config_path
    app_mod.refresh_services()
    return group_path


def _configure_schedule(routine_id: str) -> None:
    config = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    for agent in config["groups"]["test"]["agents"]:
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
    group_path = _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"routine_id": "daily-review"})

    assert resp.status_code == 202
    assert resp.json() == {"status": "started", "job_id": "job-1"}
    assert len(calls) == 1
    request = calls[0]
    assert isinstance(request, JobRequest)
    assert request.trigger == "manual_prompt"
    assert request.group_key == "test"
    assert request.agent_name == "product"
    assert request.routine_id == "daily-review"
    assert request.task_input == "Run routine 'daily-review' with arguments: --mode=review, literal value."
    assert request.timeout_override is None
    assert not (group_path / "product").exists()


def test_run_renders_routine_arguments_in_task_input(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"routine_id": "daily-review"})

    assert resp.status_code == 202
    assert calls[0].task_input == "Run routine 'daily-review' with arguments: --mode=review, literal value."


def test_run_unknown_routine_404(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"routine_id": "nope"})

    assert resp.status_code == 404


def test_run_invalid_routine_id_400(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"routine_id": "../secret"})

    assert resp.status_code == 400


def test_run_allows_concurrent_jobs_for_same_agent(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id=f"job-{len(calls)}"))
    client = TestClient(app)

    assert client.post("/test/agents/product/run", data={"routine_id": "daily-review"}).status_code == 202
    assert client.post("/test/agents/product/run", data={"routine_id": "daily-review"}).status_code == 202
    assert len(calls) == 2


def test_agent_running_state_comes_from_active_job_records(tmp_path):
    group_path = _setup_group(tmp_path)
    job_store = JobStore(tmp_path / "memory")
    group_store = job_store.group_root("test")
    group_store.mkdir(parents=True, exist_ok=True)
    for status in ("queued", "running"):
        spec = JobSpec(
            schema_version=2,
            job_id=f"job-{status}",
            config_path=str((tmp_path / "config.yaml").resolve()),
            config_revision="cfg-1",
            group_key="test",
            group_path=str(group_path.resolve()),
            agent_name="product",
            workspace_dir=str(group_path.resolve()),
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
            skill="daily-review",
            skill_arguments=(),
            task_input="# Routine\n",
            runtime_policy=RuntimePolicySnapshot(
                timeout=1800,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tool_mode="all",
                tool_names=(),
            ),
            memory=MemoryBinding(
                selector={"scope": "agent", "version": 1, "group": "test", "agent": "product"},
                canonical_json='{"agent":"product","group":"test","scope":"agent","version":1}',
                memory_hash="memory-hash-1",
                path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
            ),
            trigger_context=None,
            prompt_source={"type": "prompt", "path": "routine.md"},
            timeout_override=None,
            created_at="2026-07-15T00:00:00+00:00",
        )
        record = replace(JobRecord.from_spec(spec), status=status)
        write_job(job_store.path("test", spec.job_id), record)

    assert not (group_path / "shared" / "logs" / ".running-product").exists()
    assert is_agent_running(app_mod.get_group("test"), "product") is True


def test_run_accepts_valid_selector_override_for_routine(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"routine_id": "daily-review", "memory_scope": "routine"})

    assert resp.status_code == 202
    assert calls[0].memory_override == {"scope": "routine"}


def test_run_rejects_invalid_selector_override_for_routine(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.submit_job_request", lambda request: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"routine_id": "daily-review", "memory_scope": "channel"})

    assert resp.status_code == 400


def test_agents_page_lists_prompts_with_run(tmp_path):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "Instances assigned to Test" in resp.text
    assert "Blueprint: builder-blueprint" in resp.text
    assert "product-routine" not in resp.text
    assert "/test/prompts/" not in resp.text


def test_agents_page_excludes_unrelated_and_system_prompts(tmp_path):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert 'data-prompt="_observation-system-steps.md"' not in resp.text
    assert 'data-prompt="other-routine.md"' not in resp.text
    assert 'data-prompt="routine.md"' not in resp.text
    assert "Move" in resp.text
    assert "Remove" in resp.text


def test_agents_page_shows_config_only_roster_without_activity_links(tmp_path):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-" not in resp.text
    assert "last run stdout log" not in resp.text


def test_exact_dispatch_slug_does_not_resolve_to_generic_prompt_routes(tmp_path):
    _setup_group(tmp_path)
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
def test_agents_page_does_not_render_superseded_schedule_links(
    tmp_path,
    prompt,
):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert 'href="/admin/orgs/test/edit#rules-product"' not in resp.text
    assert 'href="/test/prompts#schedule-product-0"' not in resp.text


def test_agents_page_keeps_roster_layout_when_logs_exist(tmp_path):
    group_path = _setup_group(tmp_path)
    day = group_path / "shared" / "logs" / "2026-07-11"
    day.mkdir()
    (day / "product-superseded.err").write_text("superseded failure")
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "product" in resp.text
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-" not in resp.text
    assert "last run stdout log" not in resp.text


def test_agents_page_running_status_has_no_time_links(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr(app_mod, "is_agent_running", lambda *args, **kwargs: True)
    job_store = JobStore(tmp_path / "memory")
    group_store = job_store.group_root("test")
    group_store.mkdir(parents=True, exist_ok=True)
    spec = JobSpec(
        schema_version=2,
        job_id="job-running",
        config_path=str((tmp_path / "config.yaml").resolve()),
        config_revision=ConfigStore(tmp_path / "config.yaml").load().revision,
        group_key="test",
        group_path=str((tmp_path / "grp").resolve()),
        agent_name="product",
        workspace_dir=str((tmp_path / "grp").resolve()),
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
        skill="daily-review",
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "group": "test", "agent": "product"},
            canonical_json='{"agent":"product","group":"test","scope":"agent","version":1}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "prompt", "path": "routine.md"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    write_job(job_store.path("test", spec.job_id), JobRecord.from_spec(spec))
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "Queued" in resp.text
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-product-0" not in resp.text
