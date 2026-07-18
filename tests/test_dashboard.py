"""Tests for mission control dashboard helpers."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.app import app, build_activity_feed, build_dashboard_fleet, build_pipeline_stats
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import transition_job, write_job


class TestBuildPipelineStats:
    def test_counts_items_per_stage(self):
        observations = [{"status": "open"}, {"status": "open"}, {"status": "archived"}]
        proposals = [{"status": "proposed"}]
        decisions = [{"answers": {"approve": "approved"}}, {"answers": {"approach": "Option A"}}]
        result = build_pipeline_stats(observations, proposals, decisions)
        assert result["observations"]["total"] == 3
        assert result["proposals"]["total"] == 1
        assert result["decisions"]["total"] == 2

    def test_sparkline_buckets_last_7_days(self):
        today = datetime.now()
        observations = [{"date": (today - timedelta(days=i)).isoformat()} for i in range(3)]
        result = build_pipeline_stats(observations, [], [])
        assert len(result["observations"]["sparkline"]) == 7

    def test_empty_pipeline(self):
        result = build_pipeline_stats([], [], [])
        assert result["observations"]["total"] == 0
        assert result["proposals"]["total"] == 0
        assert result["decisions"]["total"] == 0
        assert len(result["observations"]["sparkline"]) == 7

    def test_detects_bottleneck(self):
        observations = [{"status": "open"}] * 10
        proposals = [{"status": "proposed"}]
        decisions = []
        result = build_pipeline_stats(observations, proposals, decisions)
        assert result["flow_status"] == "bottleneck"

    def test_healthy_flow(self):
        observations = [{"status": "open"}] * 3
        proposals = [{"status": "proposed"}] * 2
        decisions = [{"answers": {"approve": "approved"}}] * 2
        result = build_pipeline_stats(observations, proposals, decisions)
        assert result["flow_status"] == "healthy"


class TestBuildActivityFeed:
    def test_interleaves_observations_and_proposals(self):
        obs = [
            {"agent": "scout", "_slug": "obs-1", "date": "2026-03-22T10:00:00", "status": "open"},
            {"agent": "scout", "_slug": "obs-2", "date": "2026-03-22T08:00:00", "status": "open"},
        ]
        props = [
            {"origin_agent": "arch", "_slug": "prop-1", "date": "2026-03-22T09:00:00", "status": "proposed"},
        ]
        feed = build_activity_feed(obs, props, limit=10)
        assert len(feed) == 3
        assert feed[0]["slug"] == "obs-1"
        assert feed[1]["slug"] == "prop-1"
        assert feed[2]["slug"] == "obs-2"

    def test_limits_results(self):
        obs = [{"agent": f"a{i}", "_slug": f"obs-{i}", "date": f"2026-03-{20+i}T10:00:00", "status": "open"} for i in range(10)]
        feed = build_activity_feed(obs, [], limit=5)
        assert len(feed) == 5

    def test_handles_empty_input(self):
        feed = build_activity_feed([], [])
        assert feed == []


def test_decision_detail_shows_agent_log_and_changes(tmp_path, monkeypatch):
    """Verify decision_detail route passes executed_by, execution_log, and changed_files to template."""
    from pathlib import Path
    from fastapi.testclient import TestClient
    import agency.app as app_mod
    from agency.app import app

    # Set up group with decision directory
    group_path = tmp_path / "grp"
    decisions_path = group_path / "shared" / "decisions"
    logs_path = group_path / "shared" / "logs" / "2026-07-10"
    decisions_path.mkdir(parents=True)
    logs_path.mkdir(parents=True)

    # Create decision with execution metadata
    log_file = logs_path / "worker-exec-12345.out"
    log_file.write_text("execution output")
    
    decision = decisions_path / "test-decision.md"
    decision.write_text(f"""---
decided_by: admin
date: 2026-07-10
execution_status: complete
execution_summary: "Task completed successfully."
executed_by: worker
execution_log: {str(log_file)}
changed_files:
  - path: a.txt
    status: modified
    lines_added: 2
    lines_removed: 1
---
Decision body
""")

    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    library_root.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agency": {
                    "title": "Agency",
                    "default_group": "test",
                    "ai_backend": "script",
                    "agent_library": str(library_root),
                    "compilation_cache": str(cache_root),
                    "memory_store": str(memory_root),
                },
                "memory": {"channels": {}},
                "groups": {
                    "test": {
                        "name": "Test Group",
                        "path": str(group_path),
                        "default_integration": "script",
                        "agents": [
                            {
                                "name": "worker",
                                "blueprint": "worker",
                                "integration": "script",
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()

    client = TestClient(app)
    resp = client.get("/test/decisions/test-decision")

    assert resp.status_code == 200
    html = resp.text
    # Assert agent badge is rendered (via agent_badge filter)
    assert "worker" in html
    # Assert log link is rendered
    assert "/test/logs/view" in html
    assert "worker-exec-12345.out" in html
    # Assert changed file is rendered
    assert "a.txt" in html
    # Assert change stats are rendered
    assert "+2" in html
    assert "−1" in html or "&minus;1" in html


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


def _seed_dashboard_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    group_root = tmp_path / "groups" / "newsletter"
    for rel in [
        ("shared", "jobs"),
        ("shared", "logs", "2026-07-16"),
        ("shared", "observations"),
        ("shared", "proposals"),
        ("shared", "decisions"),
        ("shared", "prompts"),
    ]:
        (group_root.joinpath(*rel)).mkdir(parents=True, exist_ok=True)
    (group_root / "shared" / "memory.md").write_text("# Shared\n", encoding="utf-8")
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["groups"] = {
        "newsletter": {
            "name": "Newsletter",
            "path": str(group_root),
            "default_integration": "copilot",
            "agents": [
                {
                    "name": "advisor",
                    "blueprint": "advisor",
                    "integration": "copilot",
                    "identity": {
                        "display_name": "Advisor",
                        "title": "Strategy Lead",
                        "emoji": ":)",
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
        }
    }

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path, group_root


def _job_spec(
    group_root: Path,
    config_path: Path,
    *,
    status: str,
    job_id: str = "job-waiting",
    agent_name: str = "advisor",
) -> JobSpec:
    return JobSpec(
        schema_version=2,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_path=str(group_root.resolve()),
        agent_name=agent_name,
        workspace_dir=str(group_root.resolve()),
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


def test_dashboard_shows_waiting_memory_with_canonical_links(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    spec = _job_spec(group_root, config_path, status="waiting_for_memory")
    path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    transition_job(path, "queued", "waiting_for_memory")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "Waiting for memory" in response.text
    assert f'/newsletter/jobs/{spec.job_id}' in response.text
    assert "/newsletter/agents/advisor/activity" in response.text
    assert "Blueprint: advisor" in response.text
    assert "copilot" in response.text
    assert spec.memory.memory_hash not in response.text


def test_dashboard_active_job_does_not_override_agent_health(monkeypatch, tmp_path, raw_config):
    _, config_path, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    spec = _job_spec(group_root, config_path, status="running", job_id="job-running")
    path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    transition_job(path, "queued", "running")

    fleet = build_dashboard_fleet(app_mod.get_group("newsletter"))

    assert fleet[0]["health"] == "red"
    assert fleet[0]["running"] is True


def test_dashboard_running_count_excludes_queued_and_waiting_jobs(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    advisor = raw["groups"]["newsletter"]["agents"][0]
    for agent_name in ("researcher", "writer"):
        agent = deepcopy(advisor)
        agent["name"] = agent_name
        agent["identity"]["display_name"] = agent_name.title()
        raw["groups"]["newsletter"]["agents"].append(agent)
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)

    queued = _job_spec(group_root, config_path, status="queued", job_id="job-queued")
    waiting = _job_spec(
        group_root,
        config_path,
        status="waiting_for_memory",
        job_id="job-waiting",
        agent_name="researcher",
    )
    running = _job_spec(
        group_root,
        config_path,
        status="running",
        job_id="job-running",
        agent_name="writer",
    )
    authority = JobStore(tmp_path / "memory-store")
    queued_path = authority.path("newsletter", queued.job_id)
    waiting_path = authority.path("newsletter", waiting.job_id)
    running_path = authority.path("newsletter", running.job_id)
    write_job(queued_path, JobRecord.from_spec(queued))
    write_job(waiting_path, JobRecord.from_spec(waiting))
    transition_job(waiting_path, "queued", "waiting_for_memory")
    write_job(running_path, JobRecord.from_spec(running))
    transition_job(running_path, "queued", "running")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "Queued" in response.text
    assert "Waiting for memory" in response.text
    assert "Running" in response.text
    assert "1 running" in response.text
    assert response.text.count('title="Running"') == 1

    fleet = {agent["name"]: agent for agent in build_dashboard_fleet(app_mod.get_group("newsletter"))}
    assert fleet["advisor"]["job_status_key"] == "queued"
    assert fleet["advisor"]["running"] is False
    assert fleet["researcher"]["job_status_key"] == "waiting_for_memory"
    assert fleet["researcher"]["running"] is False
    assert fleet["writer"]["job_status_key"] == "running"
    assert fleet["writer"]["running"] is True


@pytest.mark.parametrize("fallback_mode", ["absent", "startup_error"])
def test_dashboard_fallback_preserves_exact_active_job_states(
    monkeypatch,
    tmp_path,
    raw_config,
    fallback_mode,
):
    client, config_path, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    advisor = raw["groups"]["newsletter"]["agents"][0]
    for agent_name in ("researcher", "writer"):
        agent = deepcopy(advisor)
        agent["name"] = agent_name
        agent["identity"]["display_name"] = agent_name.title()
        raw["groups"]["newsletter"]["agents"].append(agent)
        (group_root / agent_name).mkdir()
        (group_root / agent_name / "AGENTS.md").write_text(
            f"# {agent_name.title()}\n",
            encoding="utf-8",
        )
    (group_root / "advisor").mkdir()
    (group_root / "advisor" / "AGENTS.md").write_text("# Advisor\n", encoding="utf-8")
    _write_yaml(config_path, raw)
    app_mod.refresh_services()

    jobs = [
        _job_spec(group_root, config_path, status="queued", job_id="job-queued"),
        _job_spec(
            group_root,
            config_path,
            status="waiting_for_memory",
            job_id="job-waiting",
            agent_name="researcher",
        ),
        _job_spec(
            group_root,
            config_path,
            status="running",
            job_id="job-running",
            agent_name="writer",
        ),
    ]
    authority = JobStore(tmp_path / "memory-store")
    for spec in jobs:
        path = authority.path("newsletter", spec.job_id)
        write_job(path, JobRecord.from_spec(spec))
        if spec.job_id == "job-waiting":
            transition_job(path, "queued", "waiting_for_memory")
        elif spec.job_id == "job-running":
            transition_job(path, "queued", "running")

    writer_log = group_root / "shared" / "logs" / "2026-07-16" / "writer-run.out"
    writer_log.write_text("recent activity\n", encoding="utf-8")
    if fallback_mode == "absent":
        monkeypatch.delattr(app_mod.app.state, "services", raising=False)
    else:
        app_mod.app.state.services = SimpleNamespace(startup_error=RuntimeError("unavailable"))

    response = client.get("/newsletter/")
    fleet = {agent["name"]: agent for agent in build_dashboard_fleet(app_mod.get_group("newsletter"))}

    assert response.status_code == 200
    assert "Queued" in response.text
    assert "Waiting for memory" in response.text
    assert "/newsletter/jobs/job-queued" in response.text
    assert "/newsletter/jobs/job-waiting" in response.text
    assert "1 running" in response.text
    assert response.text.count('title="Running"') == 1
    assert fleet["advisor"]["job_status_key"] == "queued"
    assert fleet["advisor"]["job_status"] == "Queued"
    assert fleet["advisor"]["job_href"] == "/newsletter/jobs/job-queued"
    assert fleet["advisor"]["running"] is False
    assert fleet["advisor"]["health"] == "red"
    assert fleet["researcher"]["job_status_key"] == "waiting_for_memory"
    assert fleet["researcher"]["job_status"] == "Waiting for memory"
    assert fleet["researcher"]["job_href"] == "/newsletter/jobs/job-waiting"
    assert fleet["researcher"]["running"] is False
    assert fleet["researcher"]["health"] == "red"
    assert fleet["writer"]["job_status_key"] == "running"
    assert fleet["writer"]["job_status"] == "Running"
    assert fleet["writer"]["job_href"] == "/newsletter/jobs/job-running"
    assert fleet["writer"]["running"] is True
    assert fleet["writer"]["health"] == "green"
    for agent_name in ("advisor", "researcher", "writer"):
        assert fleet[agent_name]["activity_href"] == f"/newsletter/agents/{agent_name}/activity"
        assert fleet[agent_name]["profile_href"] == f"/newsletter/agents/{agent_name}/profile"


def test_dashboard_uses_selected_group_instances_only(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    other_group = group_root.parent / "research"
    for rel in [("shared", "jobs"), ("shared", "logs"), ("shared", "observations"), ("shared", "proposals"), ("shared", "decisions")]:
        other_group.joinpath(*rel).mkdir(parents=True, exist_ok=True)
    other_group.joinpath("shared", "memory.md").write_text("# Shared\n", encoding="utf-8")

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["groups"]["research"] = {
        "name": "Research",
        "path": str(other_group),
        "default_integration": "copilot",
        "agents": [
            {
                "name": "analyst",
                "blueprint": "advisor",
                "integration": "copilot",
                "identity": {"display_name": "Analyst"},
            }
        ],
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(tmp_path / "config.yaml")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Analyst" not in response.text
