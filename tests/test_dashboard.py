"""Tests for mission control dashboard helpers."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from agency import app as app_mod
from agency.app import (
    app,
    build_activity_feed,
    build_dashboard_fleet,
    build_pipeline_stats,
    list_markdown_items,
)
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import transition_job, write_job
from tests._team_helpers import apply_team_paths, create_team_environment


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


def test_list_markdown_items_reads_the_explicit_team_directory(tmp_path):
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "signal.md").write_text(
        "---\nagent: scout\nstatus: open\n---\n\n**Signal**\n",
        encoding="utf-8",
    )

    items = list_markdown_items(observations, apply_ttl=True)

    assert [item["_slug"] for item in items] == ["signal"]


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

    # Set up team with decision directory
    paths = create_team_environment(
        tmp_path,
        "test",
        create_state=True,
    )
    team_path = paths.state_root
    decisions_path = team_path / "decisions"
    logs_path = team_path / "logs" / "2026-07-10"
    decisions_path.mkdir(parents=True, exist_ok=True)
    logs_path.mkdir(parents=True, exist_ok=True)

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
    prompt_root = tmp_path / "prompts"
    library_root.mkdir()
    (library_root / "worker").mkdir(parents=True, exist_ok=True)
    (library_root / "worker" / "AGENTS.md").write_text("# Worker\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "agency": {
                    "title": "Agency",
                    "default_team": "test",
                    "ai_backend": "script",
                    "agent_library": str(library_root),
                    "compilation_cache": str(cache_root),
                    "memory_store": str(memory_root),
                    "prompt_store": str(prompt_root),
                },
                "memory": {"channels": {}},
                "teams": {
                    "test": apply_team_paths({
                        "name": "Test Group",
                        "default_integration": "script",
                        "agents": [
                            {
                                "name": "worker",
                                "blueprint": "worker",
                                "integration": "script",
                            }
                        ],
                    }, paths)
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
    prompt_dir = blueprint / ".agents" / "prompts"
    skill.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _seed_dashboard_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    prompt_root = tmp_path / "prompts"
    paths = create_team_environment(tmp_path, "newsletter")
    team_root = paths.state_root
    for rel in [
        ("logs", "2026-07-16"),
        ("observations",),
        ("proposals",),
        ("decisions",),
        ("locks",),
    ]:
        (team_root.joinpath(*rel)).mkdir(parents=True, exist_ok=True)
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
                    "identity": {
                        "display_name": "Advisor",
                        "title": "Strategy Lead",
                        "emoji": ":)",
                    },
                    "routines": [
                        {
                            "id": "daily-review",
                            "prompt": {"scope": "blueprint", "name": "daily-review"},
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
    return TestClient(app_mod.app), config_path, team_root


def _job_spec(
    team_root: Path,
    config_path: Path,
    *,
    status: str,
    job_id: str = "job-waiting",
    agent_name: str = "advisor",
    routine_id: str = "daily-review",
) -> JobSpec:
    return JobSpec(
        schema_version=5,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key="newsletter",
        team_root=str(team_root.resolve()),
        agent_name=agent_name,
        workspace_root=str(team_root.resolve()),
        trigger="scheduled_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path=str((team_root.parent.parent / "compiled-agents" / "copilot" / "v1" / "digest-1").resolve()),
        ),
        routine_id=routine_id,
        skill=None,
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="restricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "channel", "channel": "support"},
            canonical_json='{"channel":"support","scope":"channel"}',
            memory_hash="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            path=str((team_root.parent.parent / "memory-store" / "channel-support").resolve()),
        ),
        trigger_context={"source": "test"},
        prompt_source={"type": "routine", "routine_id": routine_id, "title": routine_id.replace("-", " ").title()},
        timeout_override=None,
        created_at="2026-07-16T00:00:00+00:00",
    )


def test_dashboard_shows_waiting_memory_with_canonical_links(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    spec = _job_spec(team_root, config_path, status="waiting_for_memory")
    path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    transition_job(path, "queued", "waiting_for_memory")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "Waiting for memory" in response.text
    assert f'/newsletter/jobs/{spec.job_id}' in response.text
    assert "/newsletter/agents/advisor/profile" in response.text
    assert ">advisor</span>" in response.text
    assert "copilot" in response.text
    assert spec.memory.memory_hash not in response.text


def test_dashboard_active_job_does_not_override_agent_health(monkeypatch, tmp_path, raw_config):
    _, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    spec = _job_spec(team_root, config_path, status="running", job_id="job-running")
    path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    transition_job(path, "queued", "running")

    fleet = build_dashboard_fleet(app_mod.get_team("newsletter"))

    assert fleet[0]["health"] == "gray"
    assert fleet[0]["running"] is True


def test_dashboard_running_count_excludes_queued_but_not_waiting_jobs(monkeypatch, tmp_path, raw_config):
    """A queued job has no worker; a waiting one has a live worker holding a slot.

    `waiting_for_memory` is a phase of a running job, not a phase of waiting to
    run, so the card stays lit and the count includes it. Only `queued` — which
    nothing has started yet — is excluded.
    """
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    advisor = raw["teams"]["newsletter"]["agents"][0]
    for agent_name in ("researcher", "writer"):
        agent = deepcopy(advisor)
        agent["name"] = agent_name
        agent["identity"]["display_name"] = agent_name.title()
        raw["teams"]["newsletter"]["agents"].append(agent)
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)

    queued = _job_spec(team_root, config_path, status="queued", job_id="job-queued")
    waiting = _job_spec(
        team_root,
        config_path,
        status="waiting_for_memory",
        job_id="job-waiting",
        agent_name="researcher",
    )
    running = _job_spec(
        team_root,
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
    assert "2 running" in response.text
    assert response.text.count("data-agent-running") == 2

    fleet = {agent["name"]: agent for agent in build_dashboard_fleet(app_mod.get_team("newsletter"))}
    assert fleet["advisor"]["job_status_key"] == "queued"
    assert fleet["advisor"]["running"] is False
    assert fleet["advisor"].get("queued") is True
    assert fleet["researcher"]["job_status_key"] == "waiting_for_memory"
    assert fleet["researcher"]["running"] is True
    assert fleet["writer"]["job_status_key"] == "running"
    assert fleet["writer"]["running"] is True


@pytest.mark.parametrize("fallback_mode", ["absent", "startup_error"])
def test_dashboard_fallback_preserves_exact_active_job_states(
    monkeypatch,
    tmp_path,
    raw_config,
    fallback_mode,
):
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    advisor = raw["teams"]["newsletter"]["agents"][0]
    for agent_name in ("researcher", "writer"):
        agent = deepcopy(advisor)
        agent["name"] = agent_name
        agent["identity"]["display_name"] = agent_name.title()
        raw["teams"]["newsletter"]["agents"].append(agent)
        (team_root / agent_name).mkdir()
        (team_root / agent_name / "AGENTS.md").write_text(
            f"# {agent_name.title()}\n",
            encoding="utf-8",
        )
    (team_root / "advisor").mkdir()
    (team_root / "advisor" / "AGENTS.md").write_text("# Advisor\n", encoding="utf-8")
    _write_yaml(config_path, raw)
    app_mod.refresh_services()

    jobs = [
        _job_spec(team_root, config_path, status="queued", job_id="job-queued"),
        _job_spec(
            team_root,
            config_path,
            status="waiting_for_memory",
            job_id="job-waiting",
            agent_name="researcher",
        ),
        _job_spec(
            team_root,
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

    writer_log = team_root / "logs" / "2026-07-16" / "writer-run.out"
    writer_log.write_text("recent activity\n", encoding="utf-8")
    if fallback_mode == "absent":
        monkeypatch.delattr(app_mod.app.state, "services", raising=False)
    else:
        app_mod.app.state.services = SimpleNamespace(startup_error=RuntimeError("unavailable"))

    response = client.get("/newsletter/")
    fleet = {agent["name"]: agent for agent in build_dashboard_fleet(app_mod.get_team("newsletter"))}

    assert response.status_code == 200
    assert "Queued" in response.text
    assert "Waiting for memory" in response.text
    assert "/newsletter/jobs/job-queued" in response.text
    assert "/newsletter/jobs/job-waiting" in response.text
    assert "2 running" in response.text
    assert response.text.count("data-agent-running") == 2
    assert fleet["advisor"]["job_status_key"] == "queued"
    assert fleet["advisor"]["job_status"] == "Queued"
    assert fleet["advisor"]["job_href"] == "/newsletter/jobs/job-queued"
    assert fleet["advisor"]["running"] is False
    assert fleet["advisor"].get("queued") is True
    assert fleet["advisor"]["health"] == "gray"
    assert fleet["researcher"]["job_status_key"] == "waiting_for_memory"
    assert fleet["researcher"]["job_status"] == "Waiting for memory"
    assert fleet["researcher"]["job_href"] == "/newsletter/jobs/job-waiting"
    assert fleet["researcher"]["running"] is True
    assert fleet["researcher"]["health"] == "gray"
    assert fleet["writer"]["job_status_key"] == "running"
    assert fleet["writer"]["job_status"] == "Running"
    assert fleet["writer"]["job_href"] == "/newsletter/jobs/job-running"
    assert fleet["writer"]["running"] is True
    assert fleet["writer"]["health"] == "green"
    for agent_name in ("advisor", "researcher", "writer"):
        assert fleet[agent_name]["activity_href"] == f"/newsletter/agents/{agent_name}/activity"
        assert fleet[agent_name]["profile_href"] == f"/newsletter/agents/{agent_name}/profile"


def test_dashboard_uses_selected_team_instances_only(monkeypatch, tmp_path, raw_config):
    client, _, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    other_paths = create_team_environment(tmp_path, "research")
    other_team = other_paths.state_root
    for rel in [("logs",), ("observations",), ("proposals",), ("decisions",), ("locks",)]:
        other_team.joinpath(*rel).mkdir(parents=True, exist_ok=True)

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    raw["teams"]["research"] = apply_team_paths({
        "name": "Research",
        "default_integration": "copilot",
        "agents": [
            {
                "name": "analyst",
                "blueprint": "advisor",
                "integration": "copilot",
                "identity": {"display_name": "Analyst"},
            }
        ],
    }, other_paths)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(tmp_path / "config.yaml")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Analyst" not in response.text


def test_dashboard_reports_never_run_agents_separately(monkeypatch, tmp_path, raw_config):
    client, _, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "1 never run" in response.text
    assert "1 needs attention" not in response.text
    assert "text-gray-400" in response.text
    assert 'title="No run on record"' in response.text


def test_initials_filter_builds_a_two_letter_avatar():
    assert app_mod.initials("Duncan Idaho") == "DI"
    assert app_mod.initials("Lady Jessica Atreides") == "LJ"
    assert app_mod.initials("advisor") == "AD"
    assert app_mod.initials("") == "?"


def test_fleet_cards_render_both_timing_values(monkeypatch, tmp_path, raw_config):
    client, _, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "never run" in response.text
    assert "no schedule" in response.text
    assert "/newsletter/agents/advisor/routines" in response.text


def test_running_dot_uses_health_sentence_as_title(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    spec = _job_spec(team_root, config_path, status="running", job_id="job-running")
    path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    transition_job(path, "queued", "running")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert 'title="Running"' not in response.text
    assert "animate-pulse" in response.text
    assert 'title="No run on record"' in response.text


def test_overdue_agent_renders_a_fault_line(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    (team_root / "logs" / "2026-07-16" / "advisor-run.out").write_text("x", encoding="utf-8")

    with patch("agency.app.clock_now", return_value=datetime(2026, 7, 16, 12, 0)):
        response = client.get("/newsletter/")

    assert "daily-review due 09:00" in response.text
    assert 'title="Routine daily-review was due at 09:00' in response.text


def test_health_items_cover_faults_and_skip_healthy_agents():
    g = {"key": "newsletter"}
    agents = [
        {"name": "a", "display_name": "A", "health_kind": "healthy", "health_sentence": "Healthy", "health_job": None},
        {"name": "b", "display_name": "B", "health_kind": "never_run", "health_sentence": "No run on record", "health_job": None},
        {"name": "c", "display_name": "C", "health_kind": "overdue", "health_sentence": "Routine r was due.", "health_job": None},
        {"name": "d", "display_name": "D", "health_kind": "due", "health_sentence": "Routine r came due.", "health_job": None},
    ]
    items = app_mod.build_health_items(g, agents)

    assert [item["name"] for item in items] == ["c", "d"]
    assert items[0]["label"] == "overdue"
    assert items[0]["sentence"] == "Routine r was due."
    assert items[0]["routines_href"] == "/newsletter/agents/c/routines"
    assert items[0]["run_href"] == "/newsletter/agents/c"
    assert items[0]["last_line"] == ""


def test_a_running_agent_produces_no_health_item():
    g = {"key": "newsletter"}
    agents = [{"name": "c", "display_name": "C", "health_kind": "overdue", "health_sentence": "s", "health_job": None, "running": True}]

    assert app_mod.build_health_items(g, agents) == []


def test_overdue_agent_appears_in_the_attention_queue(monkeypatch, tmp_path, raw_config):
    # dispatch must be enabled so schedule_lateness detects the overdue routine
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    (team_root / "logs" / "2026-07-16" / "advisor-run.out").write_text("x", encoding="utf-8")

    with patch("agency.app.clock_now", return_value=datetime(2026, 7, 16, 12, 0)):
        response = client.get("/newsletter/")

    assert "Routine daily-review was due at 09:00" in response.text
    assert "No items need attention right now." not in response.text
    assert "Open routine" in response.text


def test_never_run_agent_produces_no_queue_item(monkeypatch, tmp_path, raw_config):
    client, _, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/")

    assert "No items need attention right now." in response.text


def test_build_health_items_job_href(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="succeeded", job_id="job-abc-123")
    record = JobRecord.from_spec(spec)
    record.status = "succeeded"
    record.completed_at = "2026-07-29T10:00:00+00:00"
    record.duration_seconds = 235.0

    g = {"key": "newsletter"}
    agents = [{"name": "advisor", "display_name": "Advisor", "health_kind": "job_failed", "health_sentence": "Last run failed.", "health_job": record}]
    items = app_mod.build_health_items(g, agents)

    assert len(items) == 1
    assert items[0]["job_href"] == f"/newsletter/jobs/{spec.job_id}"


def test_last_run_line_succeeded(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="succeeded", job_id="job-succeeded")
    record = JobRecord.from_spec(spec)
    record.status = "succeeded"
    record.duration_seconds = 235.0

    # same instant: +00:00 and +02:00 must produce the same line
    record.completed_at = "2026-07-29T10:00:00+00:00"
    line_utc = app_mod._last_run_line(record)
    record.completed_at = "2026-07-29T12:00:00+02:00"
    line_offset = app_mod._last_run_line(record)
    record.completed_at = "2026-07-29T10:00:00"  # naive = UTC
    line_naive = app_mod._last_run_line(record)

    assert line_utc == line_offset
    assert line_utc == line_naive
    assert "· succeeded in 3m 55s" in line_utc
    assert line_utc.startswith("last run ")


def test_last_run_line_failed(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="failed", job_id="job-failed")
    record = JobRecord.from_spec(spec)
    record.status = "failed"
    record.duration_seconds = 10.0

    record.completed_at = "2026-07-29T08:30:00+00:00"
    line_utc = app_mod._last_run_line(record)
    record.completed_at = "2026-07-29T10:30:00+02:00"
    line_offset = app_mod._last_run_line(record)
    record.completed_at = "2026-07-29T08:30:00"  # naive = UTC
    line_naive = app_mod._last_run_line(record)

    assert line_utc == line_offset
    assert line_utc == line_naive
    assert "· failed in 10s" in line_utc
    assert line_utc.startswith("last run ")


def test_last_run_line_none():
    assert app_mod._last_run_line(None) == ""


def test_last_run_line_no_duration(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="failed", job_id="job-nodur")
    record = JobRecord.from_spec(spec)
    record.status = "failed"
    record.duration_seconds = None

    record.completed_at = "2026-07-29T08:30:00+00:00"
    line_utc = app_mod._last_run_line(record)
    record.completed_at = "2026-07-29T10:30:00+02:00"
    line_offset = app_mod._last_run_line(record)
    record.completed_at = "2026-07-29T08:30:00"  # naive = UTC
    line_naive = app_mod._last_run_line(record)

    assert line_utc == line_offset
    assert line_utc == line_naive
    assert line_utc.endswith("· failed")
    assert line_utc.startswith("last run ")


def test_health_sentence_fully_populated(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="failed", job_id="job-full1234")
    record = JobRecord.from_spec(spec)
    record.status = "failed"
    record.exit_code = 1
    record.completed_at = "2026-07-29T10:00:00+00:00"
    record.duration_seconds = 12.0

    from datetime import timezone
    finished = datetime.fromisoformat("2026-07-29T10:00:00+00:00").astimezone().replace(tzinfo=None)
    mock_now = finished + timedelta(hours=4)

    status = SimpleNamespace(kind="job_failed")
    with patch("agency.app.clock_now", return_value=mock_now):
        sentence = app_mod._health_sentence(status, record, mock_now)
    assert sentence == "Job job-full exited 1 after 12s, 4h ago."


def test_health_sentence_exit_code_none(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="failed", job_id="job-nocode1")
    record = JobRecord.from_spec(spec)
    record.status = "failed"
    record.exit_code = None
    record.completed_at = "2026-07-29T10:00:00+00:00"
    record.duration_seconds = 12.0

    finished = datetime.fromisoformat("2026-07-29T10:00:00+00:00").astimezone().replace(tzinfo=None)
    mock_now = finished + timedelta(hours=4)

    status = SimpleNamespace(kind="job_failed")
    with patch("agency.app.clock_now", return_value=mock_now):
        sentence = app_mod._health_sentence(status, record, mock_now)
    assert sentence == "Job job-noco failed after 12s, 4h ago."


def test_health_sentence_duration_none(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="failed", job_id="job-nodur123")
    record = JobRecord.from_spec(spec)
    record.status = "failed"
    record.exit_code = 1
    record.completed_at = "2026-07-29T10:00:00+00:00"
    record.duration_seconds = None

    finished = datetime.fromisoformat("2026-07-29T10:00:00+00:00").astimezone().replace(tzinfo=None)
    mock_now = finished + timedelta(hours=4)

    status = SimpleNamespace(kind="job_failed")
    with patch("agency.app.clock_now", return_value=mock_now):
        sentence = app_mod._health_sentence(status, record, mock_now)
    assert sentence == "Job job-nodu exited 1, 4h ago."


def test_health_sentence_completed_at_none(tmp_path):
    spec = _job_spec(tmp_path / "team", tmp_path / "config.yaml", status="failed", job_id="job-notime12")
    record = JobRecord.from_spec(spec)
    record.status = "failed"
    record.exit_code = 1
    record.completed_at = None
    record.started_at = None
    record.duration_seconds = 12.0

    status = SimpleNamespace(kind="job_failed")
    sentence = app_mod._health_sentence(status, record, datetime(2026, 7, 29, 14, 0))
    assert sentence == "Job job-noti exited 1 after 12s."


def test_attention_queue_header_singular(monkeypatch, tmp_path, raw_config):
    # dispatch must be enabled so schedule_lateness produces the overdue fault
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    (team_root / "logs" / "2026-07-16" / "advisor-run.out").write_text("x", encoding="utf-8")

    with patch("agency.app.clock_now", return_value=datetime(2026, 7, 16, 12, 0)):
        response = client.get("/newsletter/")

    assert "1 item" in response.text
    assert "1 items" not in response.text


def test_fleet_attention_counts_health_items_not_color():
    """fleet_attention must equal len(health_items) so the footer and queue cannot drift."""
    g = {"key": "newsletter"}
    # one overdue agent that is running — must produce 0 health items
    agents_running_unhealthy = [
        {"name": "x", "display_name": "X", "health": "red", "health_kind": "overdue",
         "health_sentence": "s", "health_job": None, "running": True},
    ]
    items = app_mod.build_health_items(g, agents_running_unhealthy)
    assert len(items) == 0, "running agent must be excluded from health items"

    # fleet_attention was previously computed independently; verify the new formula agrees
    # one overdue (not running) + one healthy = 1 item, so fleet_attention must be 1
    agents_mixed = [
        {"name": "a", "display_name": "A", "health": "red", "health_kind": "overdue",
         "health_sentence": "s", "health_job": None},
        {"name": "b", "display_name": "B", "health": "green", "health_kind": "healthy",
         "health_sentence": "ok", "health_job": None},
    ]
    items2 = app_mod.build_health_items(g, agents_mixed)
    assert len(items2) == 1


def test_fleet_attention_matches_queue_for_running_unhealthy_agent(monkeypatch, tmp_path, raw_config):
    """An unhealthy agent that is running must not appear in fleet_attention or the queue."""
    from copy import deepcopy
    client, config_path, team_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    # add a second agent so advisor can be overdue while writer is running
    advisor = raw["teams"]["newsletter"]["agents"][0]
    writer = deepcopy(advisor)
    writer["name"] = "writer"
    writer["identity"] = {"display_name": "Writer"}
    raw["teams"]["newsletter"]["agents"].append(writer)
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    (team_root / "logs" / "2026-07-16" / "advisor-run.out").write_text("x", encoding="utf-8")
    (team_root / "logs" / "2026-07-16" / "writer-run.out").write_text("x", encoding="utf-8")

    # put writer in a running job so it is unhealthy but running
    running_spec = _job_spec(team_root, config_path, status="running", job_id="job-writer-run", agent_name="writer")
    running_path = JobStore(tmp_path / "memory-store").path("newsletter", running_spec.job_id)
    write_job(running_path, JobRecord.from_spec(running_spec))
    transition_job(running_path, "queued", "running")

    with patch("agency.app.clock_now", return_value=datetime(2026, 7, 16, 12, 0)):
        response = client.get("/newsletter/")
        # Must be computed under the same frozen clock as the rendered page,
        # otherwise this compares a page built at 2026-07-16 12:00 against
        # health recomputed at real wall-clock time.
        expected_attention = len(app_mod.build_health_items(
            app_mod.get_team("newsletter"),
            [a for a in app_mod.build_dashboard_fleet(app_mod.get_team("newsletter"))],
        ))

    # advisor is overdue and NOT running → counts as 1 attention
    # writer has a running job (even if its last job later fails), NOT counted in queue
    assert response.status_code == 200
    text = response.text
    # the fleet_attention count must equal the queue item count
    import re
    footer_match = re.search(r"(\d+) needs attention", text)
    queue_items = text.count('class="text-xs font-mono text-amber-700') + text.count('class="text-xs font-mono text-rose-700')
    if footer_match:
        assert int(footer_match.group(1)) == expected_attention


def test_startup_drain_failure_does_not_prevent_startup(monkeypatch):
    import asyncio
    from agency.app import lifespan

    mock_config = SimpleNamespace(agency=SimpleNamespace(memory_store=None))
    mock_snapshot = SimpleNamespace(config=mock_config)
    mock_services = SimpleNamespace(
        startup_error=None,
        config_store=SimpleNamespace(load=lambda: mock_snapshot),
    )
    monkeypatch.setattr("agency.app.refresh_services", lambda: mock_services)

    def _boom(*a, **k):
        raise RuntimeError("simulated drain failure")

    monkeypatch.setattr("agency.app.drain", _boom)

    reached_yield = []

    async def run():
        async with lifespan(None):
            reached_yield.append(True)

    asyncio.run(run())
    assert reached_yield == [True]


class TestWorkQueueStrip:
    @pytest.fixture
    def _env(self, monkeypatch, tmp_path, raw_config):
        return _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    @pytest.fixture
    def client(self, _env):
        return _env[0]

    @pytest.fixture
    def waiting_jobs(self, _env, tmp_path):
        _, config_path, team_root = _env
        authority = JobStore(tmp_path / "memory-store")

        for job_id in ("job-running-1", "job-running-2"):
            spec = _job_spec(team_root, config_path, status="running", job_id=job_id)
            path = authority.path("newsletter", job_id)
            write_job(path, JobRecord.from_spec(spec))
            transition_job(path, "queued", "running")

        for job_id, routine, due in (
            ("job-queue-sh", "suite-health", "2026-07-16T08:00:00+00:00"),
            ("job-queue-aa", "authority-audit", "2026-07-16T09:00:00+00:00"),
            ("job-queue-da", "docs-audit", "2026-07-16T11:42:00+00:00"),
        ):
            spec = _job_spec(team_root, config_path, status="queued", job_id=job_id, routine_id=routine)
            path = authority.path("newsletter", job_id)
            write_job(path, JobRecord.from_spec(spec, due_at=due))

    def test_the_strip_sits_between_pipeline_and_the_attention_queue(self, client, waiting_jobs):
        body = client.get("/newsletter/").text
        assert body.index("Pipeline") < body.index("Work queue") < body.index("Attention Queue")

    def test_the_strip_lists_waiting_jobs_in_due_order(self, client, waiting_jobs):
        body = client.get("/newsletter/").text
        strip = body[body.index("Work queue"):body.index("Attention Queue")]
        assert [
            line for line in ("suite-health", "authority-audit", "docs-audit")
            if line in strip
        ] == ["suite-health", "authority-audit", "docs-audit"]
        assert (
            strip.index("suite-health")
            < strip.index("authority-audit")
            < strip.index("docs-audit")
        )

    def test_the_strip_header_counts_running_and_waiting(self, client, waiting_jobs):
        body = client.get("/newsletter/").text
        assert "2 running" in body and "3 queued" in body

    def test_an_empty_queue_keeps_one_idle_line(self, client):
        body = client.get("/newsletter/").text
        strip = body[body.index("Work queue"):body.index("Attention Queue")]
        assert strip.count("idle") == 1
        assert "pool 4" in strip
        assert "queued" not in strip

    def test_waiting_jobs_due_time_is_not_a_raw_iso_string(self, client, waiting_jobs):
        """Raw ISO timestamps must not appear; a formatted clock time must."""
        import re
        body = client.get("/newsletter/").text
        assert "2026-07-16T08:00:00" not in body
        assert re.search(r"\b\d{2}:\d{2}\b", body)

    def test_waiting_jobs_due_today_renders_as_time_only(self, client, waiting_jobs, monkeypatch):
        """A job due today shows HH:MM with no weekday prefix."""
        import re
        # Fix "today" to 2026-07-16 so fixture jobs (due 2026-07-16 UTC) are today
        monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-16T12:00:00")
        body = client.get("/newsletter/").text
        # No weekday prefix before the time (Mon/Tue/…/Sun HH:MM pattern absent)
        assert not re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{2}:\d{2}", body)

    def test_waiting_jobs_due_other_day_renders_with_weekday_prefix(self, client, waiting_jobs, monkeypatch):
        """A job due on a different day shows Mon HH:MM (abbreviated weekday prefix)."""
        import re
        # today is 2026-07-30; fixture jobs are due on 2026-07-16 — a past day
        monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-30T12:00:00")
        body = client.get("/newsletter/").text
        assert re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{2}:\d{2}", body)

    def test_load_snapshot_exception_falls_back_to_idle(self, client, monkeypatch):
        # Patch queue_snapshot to raise while _load_snapshot (used by get_team) still works;
        # the work queue's try/except catches this and falls back to the idle line.
        import agency.app as app_mod

        def _raise(config, *, memory_store):
            raise RuntimeError("config unavailable")

        monkeypatch.setattr(app_mod, "queue_snapshot", _raise)
        body = client.get("/newsletter/").text
        assert "idle" in body and "pool 4" in body

    def test_memory_store_none_falls_back_to_idle(self, client, monkeypatch):
        # Patch the snapshot so the work queue section sees memory_store=None with pool=8.
        # Patching runtime_team prevents the earlier get_team call from failing on None.
        # The guard sends pool=8 (configured); without the guard queue_snapshot(…, None)
        # raises TypeError → except fires → pool=4, causing the assertion to fail.
        import agency.app as app_mod
        from agency.web.state import runtime_team as real_runtime_team
        from dataclasses import replace as dc_replace

        real_snapshot = app_mod._load_snapshot()
        real_group = real_runtime_team(real_snapshot, "newsletter")

        modified_agency = real_snapshot.config.agency.model_copy(
            update={
                "memory_store": None,
                "jobs": real_snapshot.config.agency.jobs.model_copy(update={"pool": 8}),
            }
        )
        mock_snapshot = dc_replace(
            real_snapshot,
            config=real_snapshot.config.model_copy(update={"agency": modified_agency}),
        )

        monkeypatch.setattr(app_mod, "_load_snapshot", lambda: mock_snapshot)
        monkeypatch.setattr(app_mod, "runtime_team", lambda snap, gid: real_group)

        body = client.get("/newsletter/").text
        assert "idle" in body and "pool 8" in body

    def test_missing_job_directory_exception_falls_back_to_idle(self, client, monkeypatch):
        import agency.app as app_mod

        def _raise(config, *, memory_store):
            raise FileNotFoundError("job store directory missing")

        monkeypatch.setattr(app_mod, "queue_snapshot", _raise)
        body = client.get("/newsletter/").text
        assert "idle" in body and "pool 4" in body


class TestQueueDueTimeFilter:
    """Unit tests for the queue_due_time Jinja filter."""

    def test_none_returns_empty_string(self):
        from agency.app import queue_due_time
        assert queue_due_time(None) == ""

    def test_today_formats_as_hhmm(self, monkeypatch):
        from agency.app import queue_due_time
        import re
        monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-16T12:00:00")
        # Noon local time: local date is always 2026-07-16 on every machine
        due = datetime(2026, 7, 16, 12, 0, 0).astimezone()
        result = queue_due_time(due)
        assert re.fullmatch(r"\d{2}:\d{2}", result)

    def test_other_day_formats_with_weekday_prefix(self, monkeypatch):
        from datetime import timezone
        from agency.app import queue_due_time
        monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-30T12:00:00")
        due = datetime(2026, 7, 16, 8, 0, 0, tzinfo=timezone.utc)
        result = queue_due_time(due)
        import re
        assert re.fullmatch(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}:\d{2}", result)

    def test_accepts_iso_string(self, monkeypatch):
        from agency.app import queue_due_time
        monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-30T12:00:00")
        result = queue_due_time("2026-07-16T08:00:00+00:00")
        import re
        assert re.fullmatch(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}:\d{2}", result)

    def test_uses_astimezone_not_naive_strip(self, monkeypatch):
        """Naive replace(tzinfo=None) discards the UTC offset; astimezone must be used."""
        from datetime import timezone, timedelta
        from agency.app import queue_due_time
        from agency.clock import today as clock_today
        # 08:00 UTC+3 = 05:00 UTC; naive strip gives "08:00", astimezone gives local hours
        utc_plus_3 = timezone(timedelta(hours=3))
        due = datetime(2026, 7, 16, 8, 0, 0, tzinfo=utc_plus_3)
        local = due.astimezone()
        # Set today to match the local date so the format branch is predictable
        monkeypatch.setenv("AGENCY_FIXED_NOW", local.strftime("%Y-%m-%dT%H:%M:%S"))
        result = queue_due_time(due)
        assert result == local.strftime("%H:%M"), (
            f"got {result!r}; filter may be using replace(tzinfo=None) "
            f"instead of astimezone() (naive hours={due.hour}, local hours={local.hour})"
        )


class TestFleetCardQueuedState:
    """Fleet card correctly distinguishes queued agents from running ones."""

    @pytest.fixture
    def _env(self, monkeypatch, tmp_path, raw_config):
        return _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    @pytest.fixture
    def client(self, _env):
        return _env[0]

    @pytest.fixture
    def waiting_jobs(self, _env, tmp_path):
        _, config_path, team_root = _env
        spec = _job_spec(team_root, config_path, status="queued", job_id="job-queued")
        path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
        write_job(path, JobRecord.from_spec(spec))

    @pytest.fixture
    def running_job(self, _env, tmp_path):
        _, config_path, team_root = _env
        spec = _job_spec(team_root, config_path, status="running", job_id="job-running")
        path = JobStore(tmp_path / "memory-store").path("newsletter", spec.job_id)
        write_job(path, JobRecord.from_spec(spec))
        transition_job(path, "queued", "running")

    def test_an_agent_with_a_waiting_job_is_not_shown_as_running(self, client, waiting_jobs):
        fleet = build_dashboard_fleet(app_mod.get_team("newsletter"))
        assert fleet[0].get("queued") is True
        assert fleet[0].get("running") is False
        body = client.get("/newsletter/").text
        assert "data-agent-running" not in body

    def test_an_agent_with_a_started_job_is_still_shown_as_running(self, client, running_job):
        body = client.get("/newsletter/").text
        assert "data-agent-running" in body
        fleet = build_dashboard_fleet(app_mod.get_team("newsletter"))
        assert fleet[0].get("running") is True
        assert not fleet[0].get("queued")
