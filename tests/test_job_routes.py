from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import read_job, transition_job, write_job
from tests._group_helpers import apply_group_paths, create_group_environment


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
    paths = create_group_environment(tmp_path, "newsletter")
    group_root = paths.state_root
    (tmp_path / "Research" / "shared").mkdir(parents=True, exist_ok=True)
    for rel in [
        ("shared", "jobs"),
        ("shared", "logs", "2026-07-16"),
        ("shared", "observations"),
        ("shared", "proposals"),
        ("shared", "decisions"),
        ("shared", "prompts"),
    ]:
        group_root.joinpath(*rel).mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "memory.md").write_text("# Shared\n", encoding="utf-8")
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["groups"] = {
        "newsletter": apply_group_paths({
            "name": "Newsletter",
            "default_integration": "copilot",
            "agents": [
                {
                    "name": "advisor",
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "identity": {
                        "display_name": "Advisor",
                        "title": "Brand Strategist",
                    },
                    "routines": [
                        {
                            "id": "daily-review",
                            "skill": "daily-review",
                            "schedule": {"at": "09:00"},
                            "memory": {"scope": "channel", "channel": "support"},
                        }
                    ],
                }
            ],
        }, paths)
    }

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path, group_root


def _write_job_record(group_root: Path, config_path: Path, *, job_id: str = "job-1", status: str = "queued") -> Path:
    group_id = "research" if group_root.name == "research" else "newsletter"
    job_store = JobStore(group_root.parent.parent / "memory-store")
    spec = JobSpec(
        schema_version=3,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key=group_id,
        group_root=str(group_root.resolve()),
        agent_name="advisor",
        workspace_root=str(group_root.resolve()),
        trigger="scheduled_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path=str((group_root.parent.parent / "compiled-agents" / "copilot" / "v1" / "digest-1").resolve()),
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
            selector={"scope": "channel", "channel": "support"},
            canonical_json='{"channel":"support","scope":"channel"}',
            memory_hash="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            path=str((group_root.parent.parent / "memory-store" / "channel-support").resolve()),
        ),
        trigger_context={"source": "test"},
        prompt_source={"type": "routine", "routine_id": "daily-review", "title": "Daily review"},
        timeout_override=None,
        created_at="2026-07-16T00:00:00+00:00",
    )
    path = job_store.path(group_id, job_id)
    record = JobRecord.from_spec(spec)
    write_job(path, record)
    if status != "queued":
        transition_job(path, "queued", status)
    return path


def test_job_list_is_group_scoped(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-1", status="queued")

    other_group = group_root.parent / "research"
    other_group.joinpath("shared", "logs", "2026-07-16").mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    research_paths = create_group_environment(tmp_path, "research")
    raw["groups"]["research"] = {
        **apply_group_paths({}, research_paths),
        "name": "Research",
        "default_integration": "copilot",
        "agents": deepcopy(raw["groups"]["newsletter"]["agents"]),
    }
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    _write_job_record(other_group, config_path, job_id="job-2", status="queued")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "job-1" in response.text
    assert "job-2" not in response.text


def test_job_detail_uses_friendly_memory_and_artifacts(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    job_store = JobStore(tmp_path / "memory-store")
    path = _write_job_record(group_root, config_path, job_id="job-failed", status="queued")
    record = read_job(path)
    failed = JobRecord(
        spec=record.spec,
        authority_digest=record.authority_digest,
        status="failed",
        stdout_path=str((group_root / "shared" / "logs" / "2026-07-16" / "advisor-scheduled_prompt-job-failed.out").resolve()),
        stderr_path=str((group_root / "shared" / "logs" / "2026-07-16" / "advisor-scheduled_prompt-job-failed.err").resolve()),
        changed_files=[{"path": "docs/brief.md", "status": "modified", "lines_added": 3, "lines_removed": 1}],
        execution_summary="Memory publication failed.",
        memory_publication={
            "failed_artifacts": [
                {
                    "name": "memory.md",
                    "path": str((job_store.artifact_root("newsletter", "job-failed") / "memory.md").resolve()),
                    "size": 12,
                }
            ]
        },
    )
    write_job(path, failed)
    Path(failed.stdout_path).write_text("stdout", encoding="utf-8")
    Path(failed.stderr_path).write_text("stderr", encoding="utf-8")
    artifact_dir = job_store.artifact_root("newsletter", "job-failed")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "memory.md").write_text("snapshot", encoding="utf-8")

    response = client.get("/newsletter/jobs/job-failed")

    assert response.status_code == 200
    assert "Routine: Daily review" in response.text
    assert "Memory: Channel: Support" in response.text
    assert "Failed memory snapshot" in response.text
    assert "Brand Strategist" in response.text
    assert "advisor" in response.text
    assert "copilot" in response.text
    assert "docs/brief.md" in response.text
    assert "advisor/activity" in response.text
    assert "advisor/routines" in response.text
    assert "job-failed" not in response.text.split("<summary", 1)[0]
    before_diagnostics, diagnostics = response.text.split('<summary class="text-sm text-gray-500 cursor-pointer">Diagnostics</summary>', 1)
    assert failed.spec.memory.memory_hash not in before_diagnostics
    assert f"Memory hash: {failed.spec.memory.memory_hash}" in diagnostics

    list_response = client.get("/newsletter/jobs")
    dashboard_response = client.get("/newsletter/")
    assert failed.spec.memory.memory_hash not in list_response.text
    assert failed.spec.memory.memory_hash not in dashboard_response.text


def test_historical_job_survives_instance_removal(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-historical", status="failed")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"] = []
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)

    list_response = client.get("/newsletter/jobs")
    detail_response = client.get("/newsletter/jobs/job-historical")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    for response in (list_response, detail_response):
        assert "advisor" in response.text
        assert "Blueprint:" in response.text
        assert "copilot" in response.text.lower()
        assert "Routine: Daily review" in response.text
        assert "Instance no longer belongs to this group" in response.text
        assert "/newsletter/agents/advisor/" not in response.text


def test_historical_job_survives_instance_move_to_another_group(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-moved", status="failed")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    advisor = raw["groups"]["newsletter"]["agents"].pop()
    moved_paths = create_group_environment(tmp_path, "research")
    moved_root = moved_paths.state_root
    moved_root.joinpath("shared", "logs", "2026-07-16").mkdir(parents=True)
    raw["groups"]["research"] = apply_group_paths({
        "name": "Research",
        "default_integration": "copilot",
        "agents": [advisor],
    }, moved_paths)
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)

    response = client.get("/newsletter/jobs/job-moved")

    assert response.status_code == 200
    assert "advisor" in response.text
    assert "Blueprint:" in response.text
    assert "copilot" in response.text.lower()
    assert "Routine: Daily review" in response.text
    assert "Instance no longer belongs to this group" in response.text
    assert "/newsletter/agents/advisor/" not in response.text


def test_job_metadata_uses_spec_snapshot_when_instance_still_exists(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-snapshot", status="failed")
    record = read_job(path)
    snapshot_spec = replace(
        record.spec,
        blueprint=replace(record.spec.blueprint, key="historical-advisor"),
        integration_name="claude-code",
        routine_id="snapshot-review",
        prompt_source={"type": "routine", "routine_id": "snapshot-review", "title": "Snapshot review"},
    )
    write_job(
        path,
        replace(
            JobRecord.from_spec(snapshot_spec),
            status=record.status,
            started_at=record.started_at,
            completed_at=record.completed_at,
        ),
    )

    response = client.get("/newsletter/jobs/job-snapshot")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Brand Strategist" in response.text
    assert "historical-advisor" in response.text
    assert "claude-code" in response.text
    assert "Routine: Snapshot review" in response.text


def test_cancel_waiting_job(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-waiting", status="waiting_for_memory")

    response = client.post("/newsletter/jobs/job-waiting/cancel", follow_redirects=False)

    assert response.status_code == 303
    assert read_job(path).status == "cancelled"


def test_cancel_running_job_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-running", status="running")

    response = client.post("/newsletter/jobs/job-running/cancel")

    assert response.status_code == 409


def test_job_artifact_path_must_be_canonical(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-safe", status="failed")

    response = client.get("/newsletter/jobs/job-safe?artifact=..%2F..%2Fsecret.txt")

    assert response.status_code in {400, 403}