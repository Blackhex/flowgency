from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.app import app, is_agent_running
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import job_path, write_job


def _setup_group(tmp_path: Path) -> Path:
    group_path = tmp_path / "grp"
    (group_path / "product").mkdir(parents=True)
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("# Routine\n")
    (prompts / "product-routine.md").write_text("# Product routine\n")
    (prompts / "other-routine.md").write_text("# Other routine\n")
    (prompts / "_observation-system-steps.md").write_text("# System\n")
    (group_path / "shared" / "logs").mkdir(parents=True)
    app_mod.CONFIG = {"groups": {"test": {"name": "Test", "path": str(group_path)}}}
    app_mod.GROUPS = {
        "test": {
            "key": "test",
            "name": "Test",
            "path": group_path,
            "shared": group_path / "shared",
            "agents": ["product"],
            "agents_full": [{"name": "product", "integration": "script"}],
            "_agents_normalized": [{"name": "product", "integration": "script"}],
            "dispatch": {"timeout": 1800},
        }
    }
    return group_path


def test_run_returns_202_and_schedules(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: calls.append(spec) or SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "routine.md"})

    assert resp.status_code == 202
    assert resp.json() == {"status": "started", "job_id": "job-1"}
    assert len(calls) == 1
    spec = calls[0]
    assert spec.trigger == "manual_prompt"
    assert spec.group_key == "test"
    assert spec.agent_name == "product"
    assert spec.prompt_content == "# Routine\n"


def test_run_unknown_prompt_404(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.submit_job", lambda spec: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "nope.md"})

    assert resp.status_code == 404


def test_run_path_traversal_400(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.submit_job", lambda spec: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "../secret.md"})

    assert resp.status_code == 400


def test_run_allows_concurrent_jobs_for_same_agent(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: calls.append(spec) or SimpleNamespace(job_id=f"job-{len(calls)}"))
    client = TestClient(app)

    assert client.post("/test/agents/product/run", data={"prompt": "routine.md"}).status_code == 202
    assert client.post("/test/agents/product/run", data={"prompt": "routine.md"}).status_code == 202
    assert len(calls) == 2


def test_agent_running_state_comes_from_active_job_records(tmp_path):
    group_path = _setup_group(tmp_path)
    for status in ("queued", "running"):
        spec = JobSpec.create(
            config_path=tmp_path / "config.yaml",
            group_key="test",
            agent_name="product",
            trigger="manual_prompt",
            prompt_source={"type": "prompt", "path": "routine.md"},
            prompt_content="# Routine\n",
        )
        record = replace(JobRecord.from_spec(spec), status=status)
        write_job(job_path(group_path, spec.job_id), record)

    assert not (group_path / "shared" / "logs" / ".running-product").exists()
    assert is_agent_running(app_mod.GROUPS["test"], "product") is True


def test_run_returns_400_when_prompt_snapshot_fails_spec_validation(tmp_path, monkeypatch):
    group_path = _setup_group(tmp_path)
    (group_path / "shared" / "prompts" / "routine.md").write_text("\n")
    monkeypatch.setattr("agency.app.submit_job", lambda spec: SimpleNamespace(job_id="job-1"))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "routine.md"})

    assert resp.status_code == 400
    assert "Prompt content must not be blank" in resp.json()["detail"]


def test_agents_page_lists_prompts_with_run(tmp_path):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    # Prompt inferred to this agent by filename prefix is shown, with a Run button.
    assert 'data-prompt="product-routine.md"' in resp.text
    assert "/test/prompts/" in resp.text


def test_agents_page_excludes_unrelated_and_system_prompts(tmp_path):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    # System prompts and prompts belonging to other agents are not listed.
    assert 'data-prompt="_observation-system-steps.md"' not in resp.text
    assert 'data-prompt="other-routine.md"' not in resp.text
    assert 'data-prompt="routine.md"' not in resp.text
