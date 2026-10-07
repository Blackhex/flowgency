from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlencode

import yaml
from fastapi.testclient import TestClient

from flowgency import app as app_mod
from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from flowgency.jobs.store import read_job, transition_job, write_job
from flowgency.web.routes.jobs import (
    JOB_DETAIL_REGION_MACROS,
    JOB_DETAIL_STRUCTURE,
    JOBS_LIST_REGION_MACROS,
    JOBS_LIST_STRUCTURE,
)
from tests._git_evidence_helpers import requires_git
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
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _seed_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    paths = create_team_environment(tmp_path, "newsletter")
    team_root = paths.state_root
    for rel in [
        ("logs", "2026-07-16"),
        ("observations",),
        ("proposals",),
        ("decisions",),
        ("locks",),
    ]:
        team_root.joinpath(*rel).mkdir(parents=True, exist_ok=True)
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["flowgency"]["agent_library"] = str(library_root)
    raw["flowgency"]["compilation_cache"] = str(cache_root)
    raw["flowgency"]["memory_store"] = str(memory_root)
    raw["flowgency"]["prompt_store"] = str(tmp_path / "prompts")
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
                        "title": "Brand Strategist",
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


def _write_job_record(
    team_root: Path,
    config_path: Path,
    *,
    team_id: str = "newsletter",
    job_id: str = "job-1",
    status: str = "queued",
    due_at: str | None = None,
) -> Path:
    job_store = JobStore(team_root.parent.parent / "memory-store")
    workspace_root = team_root.parent.parent / "workspaces" / team_id
    spec = JobSpec(
        schema_version=5,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key=team_id,
        team_root=str(team_root.resolve()),
        agent_name="advisor",
        workspace_root=str(workspace_root.resolve()),
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
        routine_id="daily-review",
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
        prompt_source={"type": "routine", "routine_id": "daily-review", "title": "Daily review"},
        timeout_override=None,
        created_at="2026-07-16T00:00:00+00:00",
    )
    path = job_store.path(team_id, job_id)
    record = JobRecord.from_spec(spec, due_at=due_at)
    write_job(path, record)
    if status != "queued":
        transition_job(path, "queued", status)
    return path


def test_job_list_is_team_scoped(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-1", status="queued")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    research_paths = create_team_environment(tmp_path, "research")
    other_team = research_paths.state_root
    (other_team / "logs" / "2026-07-16").mkdir(parents=True, exist_ok=True)
    raw["teams"]["research"] = {
        **apply_team_paths({}, research_paths),
        "name": "Research",
        "default_integration": "copilot",
        "agents": deepcopy(raw["teams"]["newsletter"]["agents"]),
    }
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    _write_job_record(other_team, config_path, team_id="research", job_id="job-2", status="queued")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "job-1" in response.text
    assert "job-2" not in response.text


def test_job_detail_uses_friendly_memory_and_artifacts(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    job_store = JobStore(tmp_path / "memory-store")
    path = _write_job_record(team_root, config_path, job_id="job-failed", status="queued")
    record = read_job(path)
    failed = JobRecord(
        spec=record.spec,
        authority_digest=record.authority_digest,
        status="failed",
        stdout_path=str((team_root / "logs" / "2026-07-16" / "advisor-scheduled_prompt-job-failed.out").resolve()),
        stderr_path=str((team_root / "logs" / "2026-07-16" / "advisor-scheduled_prompt-job-failed.err").resolve()),
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
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-historical", status="failed")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"] = []
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
        assert "Instance no longer belongs to this team" in response.text
        assert "/newsletter/agents/advisor/" not in response.text


def test_historical_job_survives_instance_move_to_another_team(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-moved", status="failed")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    advisor = raw["teams"]["newsletter"]["agents"].pop()
    moved_paths = create_team_environment(tmp_path, "research")
    moved_root = moved_paths.state_root
    moved_root.joinpath("logs", "2026-07-16").mkdir(parents=True)
    raw["teams"]["research"] = apply_team_paths({
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
    assert "Instance no longer belongs to this team" in response.text
    assert "/newsletter/agents/advisor/" not in response.text


def test_job_metadata_uses_spec_snapshot_when_instance_still_exists(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-snapshot", status="failed")
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
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-waiting", status="waiting_for_memory")

    response = client.post("/newsletter/jobs/job-waiting/cancel", follow_redirects=False)

    assert response.status_code == 303
    assert read_job(path).status == "cancelled"


def test_cancel_running_job_returns_conflict(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-running", status="running")

    response = client.post("/newsletter/jobs/job-running/cancel")

    assert response.status_code == 409


def test_job_artifact_path_must_be_canonical(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-safe", status="failed")

    response = client.get("/newsletter/jobs/job-safe?artifact=..%2F..%2Fsecret.txt")

    assert response.status_code in {400, 403}


def test_job_detail_links_logs_to_viewer(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-logs", status="queued")
    log_dir = team_root / "logs" / "2026-07-16"
    stdout_log = log_dir / "advisor-scheduled_prompt-job-logs.out"
    stderr_log = log_dir / "advisor-scheduled_prompt-job-logs.err"
    stdout_log.write_text("stdout", encoding="utf-8")
    stderr_log.write_text("stderr", encoding="utf-8")
    record = read_job(path)
    write_job(
        path,
        replace(
            record,
            status="failed",
            stdout_path=str(stdout_log.resolve()),
            stderr_path=str(stderr_log.resolve()),
        ),
    )

    response = client.get("/newsletter/jobs/job-logs")

    assert response.status_code == 200
    assert f"/newsletter/logs/view?path={quote(str(stdout_log.resolve()))}" in response.text
    assert f"/newsletter/logs/view?path={quote(str(stderr_log.resolve()))}" in response.text
    assert "advisor-scheduled_prompt-job-logs.out" in response.text
    assert "advisor-scheduled_prompt-job-logs.err" in response.text


def test_job_detail_encodes_log_paths_for_viewer(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-logs-encoded", status="queued")
    log_dir = team_root / "logs" / "2026-07-16"
    stdout_log = log_dir / "advisor report & łog.out"
    stdout_log.write_text("stdout", encoding="utf-8")
    record = read_job(path)
    write_job(
        path,
        replace(
            record,
            status="complete",
            stdout_path=str(stdout_log.resolve()),
            stderr_path=None,
        ),
    )

    response = client.get("/newsletter/jobs/job-logs-encoded")

    assert response.status_code == 200
    assert f"/newsletter/logs/view?{urlencode({'path': str(stdout_log.resolve())})}" in response.text
    assert "advisor report" in response.text


def test_job_detail_omits_log_link_without_path(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-nolog", status="queued")
    log_dir = team_root / "logs" / "2026-07-16"
    stdout_log = log_dir / "advisor-scheduled_prompt-job-nolog.out"
    stdout_log.write_text("stdout", encoding="utf-8")
    record = read_job(path)
    write_job(
        path,
        replace(
            record,
            status="failed",
            stdout_path=str(stdout_log.resolve()),
            stderr_path=None,
        ),
    )

    response = client.get("/newsletter/jobs/job-nolog")

    assert response.status_code == 200
    assert "Stdout log:" in response.text
    assert "Stderr log:" not in response.text


def _write_resumable_job(team_root, config_path, *, job_id, session_id):
    path = _write_job_record(team_root, config_path, job_id=job_id, status="queued")
    record = read_job(path)
    write_job(path, replace(record, status="complete", session_id=session_id))
    return path


def test_job_detail_offers_resume_when_session_known(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-resume", session_id="sess-1")

    response = client.get("/newsletter/jobs/job-resume")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-resume/resume" in response.text
    assert "--resume" in response.text
    assert "sess-1" in response.text


def test_job_detail_hides_resume_without_session(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-nosess", status="queued")
    write_job(path, replace(read_job(path), status="complete"))

    response = client.get("/newsletter/jobs/job-nosess")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-nosess/resume" not in response.text


def test_job_detail_hides_resume_without_integration_support(monkeypatch, tmp_path, raw_config):
    from flowgency.integrations.flowgency.copilot import CopilotIntegration

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-unsup", session_id="sess-9")
    monkeypatch.setattr(CopilotIntegration, "resume_command", lambda self, session_id: None)

    response = client.get("/newsletter/jobs/job-unsup")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-unsup/resume" not in response.text


def test_resume_spawns_terminal_and_redirects(monkeypatch, tmp_path, raw_config):
    import flowgency.web.routes.jobs as jobs_mod
    from flowgency.integrations.flowgency.copilot import CopilotIntegration

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-spawn", session_id="sess-2")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")
    captured = {}

    def fake_spawn(command, cwd, env=None):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        return "copilot --resume sess-2"

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-spawn/resume", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/jobs/job-spawn?resume=launched"
    assert captured["command"] == ["/opt/copilot", "--resume", "sess-2"]
    assert captured["cwd"] == Path(team_root.parent.parent / "workspaces" / "newsletter")


def test_resume_reports_failure(monkeypatch, tmp_path, raw_config):
    import flowgency.web.routes.jobs as jobs_mod
    from flowgency.integrations import IntegrationError
    from flowgency.integrations.flowgency.copilot import CopilotIntegration

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-nospawn", session_id="sess-3")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")

    def fake_spawn(command, cwd, env=None):
        raise IntegrationError("no terminal")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-nospawn/resume", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/jobs/job-nospawn?resume=failed"


def test_resume_rejects_unsafe_session_id(monkeypatch, tmp_path, raw_config):
    import flowgency.web.routes.jobs as jobs_mod

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(
        team_root,
        config_path,
        job_id="job-evil",
        session_id="a; rm -rf ~",
    )

    def fail_spawn(command, cwd, env=None):
        raise AssertionError("spawn must not be reached")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fail_spawn)

    response = client.post("/newsletter/jobs/job-evil/resume", follow_redirects=False)

    assert response.status_code == 400


def test_resume_unknown_job_is_not_found(monkeypatch, tmp_path, raw_config):
    client, _config_path, _group_root = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.post("/newsletter/jobs/job-missing/resume", follow_redirects=False)

    assert response.status_code == 404


def test_ui_reset_rejects_unknown_fixture_before_mutating_runtime(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    runtime_parent = (tmp_path / "ui-runtime").as_posix()
    child = "\n".join(
        (
            "from pathlib import Path",
            "import os",
            "import sys",
            "from fastapi.testclient import TestClient",
            "import flowgency.app as app_mod",
            "from tests.ui import server as ui_server",
            "runtime_parent = Path(sys.argv[1])",
            "ui_server.RUNTIME_PARENT = runtime_parent",
            "ui_server.RUNTIME_ROOT = runtime_parent / 'current'",
            "runtime, config_path = ui_server._prepare_runtime()",
            "try:",
            "    os.environ['FLOWGENCY_CONFIG'] = str(config_path)",
            "    os.environ['FLOWGENCY_UI_RUNTIME'] = str(runtime)",
            "    app_mod.CONFIG_PATH = config_path",
            "    ui_server._install_ui_test_runtime()",
            "    app_mod.refresh_services()",
            "    preserved = runtime / 'teams' / 'newsletter' / 'logs' / 'fixture-preserved.out'",
            "    preserved.write_text('keep me', encoding='utf-8')",
            "    before_config = config_path.read_bytes()",
            "    before_preserved = preserved.read_bytes()",
            "    with TestClient(app_mod.app) as client:",
            "        response = client.post(ui_server.UI_RESET_PATH, json={'fixture': 'missing-fixture'})",
            "    assert response.status_code == 400",
            "    assert response.json() == {'detail': 'Unsupported fixture'}",
            "    assert config_path.read_bytes() == before_config",
            "    assert preserved.read_bytes() == before_preserved",
            "finally:",
            "    ui_server._safe_remove_runtime(runtime)",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", child, runtime_parent],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_resume_spawns_terminal_carries_copilot_home(monkeypatch, tmp_path, raw_config):
    import os
    import flowgency.web.routes.jobs as jobs_mod
    from flowgency.integrations.flowgency.copilot import CopilotIntegration

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    home_path = tmp_path / ".copilot-job"
    path = _write_job_record(team_root, config_path, job_id="job-home", status="queued")
    record = read_job(path)
    write_job(path, replace(record, status="complete", session_id="sess-h", copilot_home=str(home_path)))
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")
    captured = {}

    def fake_spawn(command, cwd, env=None):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        captured["env"] = env
        return "ok"

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-home/resume", follow_redirects=False)

    assert response.status_code == 303
    assert captured["env"] is not None
    assert captured["env"]["COPILOT_HOME"] == str(home_path)
    assert captured["env"].get("PATH") == os.environ.get("PATH")


def test_resume_spawns_terminal_no_env_without_copilot_home(monkeypatch, tmp_path, raw_config):
    import flowgency.web.routes.jobs as jobs_mod
    from flowgency.integrations.flowgency.copilot import CopilotIntegration

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-noenv", session_id="sess-ne")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")
    captured = {}

    def fake_spawn(command, cwd, env=None):
        captured["env"] = env
        return "ok"

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-noenv/resume", follow_redirects=False)

    assert response.status_code == 303
    assert captured["env"] is None


def test_job_detail_shows_failure_notice_when_resume_failed(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-notice", session_id="sess-n")

    response = client.get("/newsletter/jobs/job-notice?resume=failed")

    assert response.status_code == 200
    assert "Could not open a terminal" in response.text


def test_waiting_jobs_show_their_position(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-pos-a", status="queued")
    _write_job_record(team_root, config_path, job_id="job-pos-b", status="queued")
    _write_job_record(team_root, config_path, job_id="job-pos-c", status="queued")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "1 of 3" in response.text


def test_a_position_counts_the_whole_queue_not_just_this_team(
    monkeypatch, tmp_path, raw_config
):
    """The pool is machine-wide, so a position must be too."""
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    research_paths = create_team_environment(tmp_path, "research")
    other_team = research_paths.state_root
    (other_team / "logs" / "2026-07-16").mkdir(parents=True, exist_ok=True)
    raw["teams"]["research"] = {
        **apply_team_paths({}, research_paths),
        "name": "Research",
        "default_integration": "copilot",
        "agents": deepcopy(raw["teams"]["newsletter"]["agents"]),
    }
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    _write_job_record(
        other_team,
        config_path,
        team_id="research",
        job_id="job-earlier",
        due_at="2026-07-16T08:00:00",
    )
    _write_job_record(
        team_root,
        config_path,
        job_id="job-later",
        due_at="2026-07-16T09:00:00",
    )

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "job-earlier" not in response.text
    assert "2 of 2" in response.text


def test_a_running_job_has_no_position(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-pos-running", status="running")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "Running" in response.text  # the job row is present
    assert " of " not in response.text  # no position badge anywhere on the page


def test_a_queued_job_offers_cancel(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-pos-q", status="queued")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "/jobs/" in response.text
    assert "cancel" in response.text.lower()


def _accepted_git_evidence(env):
    """Capture real evidence in one job and accept it as the ticket's output."""
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.tickets.git_evidence import GitCaptureRequest
    from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

    fixture = create_git_repository(env.tmp_path / "source")
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    captured = env.service.capture_git_evidence(
        actor,
        ticket.version,
        GitCaptureRequest(
            transition_id="complete",
            field_id="evidence",
            base_commit=fixture.base_commit,
            end_commit=fixture.end_commit,
        ),
        env.operation("capture", actor_name="builder"),
    )
    env.service.transition(
        actor,
        captured.version,
        env.transition_request(
            outputs={"summary": "Committed result", "evidence": captured.artifact}
        ),
        env.operation("finish", actor_name="builder"),
    )
    return fixture, actor, ticket, captured


@requires_git
def test_job_detail_links_only_the_job_that_produced_the_evidence(workflow_web_env):
    env = workflow_web_env
    _, _, ticket, captured = _accepted_git_evidence(env)
    # A second persisted job that never produced this artifact.
    env.running_job("observer", "later-review-run")
    diff_path = (
        f"/{env.team_id}/workflows/{env.workflow_id}/tickets/{ticket.ref.ticket_id}"
        f"/artifacts/{captured.artifact.value}/diff?source=job"
    )

    producer = env.client.get(f"/{env.team_id}/jobs/git-evidence-run")
    reviewer = env.client.get(f"/{env.team_id}/jobs/later-review-run")

    assert producer.status_code == 200
    assert "Git evidence" in producer.text
    assert diff_path in producer.text
    assert f"/{env.team_id}/workflows/{env.workflow_id}/tickets/{ticket.ref.ticket_id}" in producer.text
    assert reviewer.status_code == 200
    assert captured.artifact.value not in reviewer.text


@requires_git
def test_job_detail_omits_evidence_that_was_never_accepted(workflow_web_env):
    from flowgency.git_evidence.models import GitPublicationPolicy
    from flowgency.tickets.git_evidence import GitCaptureRequest
    from tests._git_evidence_helpers import configure_git_ticket, create_git_repository

    env = workflow_web_env
    fixture = create_git_repository(env.tmp_path / "source")
    actor, ticket = configure_git_ticket(env, fixture, GitPublicationPolicy(mode="local"))
    captured = env.service.capture_git_evidence(
        actor,
        ticket.version,
        GitCaptureRequest(
            transition_id="complete",
            field_id="evidence",
            base_commit=fixture.base_commit,
            end_commit=fixture.end_commit,
        ),
        env.operation("capture", actor_name="builder"),
    )

    response = env.client.get(f"/{env.team_id}/jobs/git-evidence-run")

    assert response.status_code == 200
    assert captured.artifact.value not in response.text


def test_an_unknown_job_has_no_evidence_page(workflow_web_env):
    response = workflow_web_env.client.get("/newsletter/jobs/job-that-never-existed")

    assert response.status_code == 404


def _add_reopen_transition(env) -> None:
    """Let the fixture workflow be completed a second time by a later job."""
    source = env.library.inspect(env.blueprint_id)
    complete = next(
        transition
        for transition in source.definition.transitions
        if transition.id == "complete"
    )
    reopen = complete.model_copy(
        update={
            "id": "reopen",
            "name": "Reopen",
            "from_state": "done",
            "to_state": "review",
            "inputs": (),
            "outputs": (),
            "preconditions": (),
            "criteria": (),
        }
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(
            update={"transitions": source.definition.transitions + (reopen,)}
        ),
    )


@requires_git
def test_job_detail_git_evidence_scan_never_runs_on_the_event_loop(workflow_web_env):
    """The scan reads every ticket and artifact, so it must not block the loop."""
    import asyncio

    from flowgency.web.routes import jobs as jobs_routes

    env = workflow_web_env
    _accepted_git_evidence(env)
    original = jobs_routes.job_git_evidence_links
    observed: dict[str, bool] = {}

    def probe(service, actor, record):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed["on_event_loop"] = False
        else:
            observed["on_event_loop"] = True
        return original(service, actor, record)

    jobs_routes.job_git_evidence_links = probe
    try:
        response = env.client.get(f"/{env.team_id}/jobs/git-evidence-run")
    finally:
        jobs_routes.job_git_evidence_links = original

    assert response.status_code == 200
    assert observed == {"on_event_loop": False}


@requires_git
def test_a_later_reviewer_reusing_the_evidence_never_becomes_its_producer(
    workflow_web_env,
):
    from flowgency.tickets.access import TicketAccessRegistry

    env = workflow_web_env
    _, builder, ticket, captured = _accepted_git_evidence(env)
    _add_reopen_transition(env)
    env.service.transition(
        builder,
        env.read(ticket.ref).version,
        env.transition_request(transition_id="reopen"),
        env.operation("reopen", actor_name="builder"),
    )
    env.service.end_work(
        builder, env.read(ticket.ref).version, env.operation("end", actor_name="builder")
    )
    authority = env.running_job("builder", "later-review-run")
    reviewer = TicketAccessRegistry(env.job_store).open(authority).context
    env.service.start_work(
        reviewer,
        env.read(ticket.ref).version,
        env.operation("review-start", actor_name="builder"),
    )
    # The reviewer accepts the original agent's artifact as its own output.
    env.service.transition(
        reviewer,
        env.read(ticket.ref).version,
        env.transition_request(
            outputs={"summary": "Reviewed result", "evidence": captured.artifact}
        ),
        env.operation("review-complete", actor_name="builder"),
    )
    diff_path = (
        f"/{env.team_id}/workflows/{env.workflow_id}/tickets/{ticket.ref.ticket_id}"
        f"/artifacts/{captured.artifact.value}/diff?source=job"
    )

    producer = env.client.get(f"/{env.team_id}/jobs/git-evidence-run")
    reviewer_page = env.client.get(f"/{env.team_id}/jobs/later-review-run")

    assert producer.status_code == 200
    assert producer.text.count(diff_path) == 1
    assert reviewer_page.status_code == 200
    assert captured.artifact.value not in reviewer_page.text


_LIVE_INITIAL = re.compile(r'<script type="application/json" id="live-initial">(.*?)</script>', re.S)


def _live_regions(response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {region["key"]: region["html"] for region in response.json()["regions"]}


def _page_region_keys(page: str) -> list[str]:
    return re.findall(r'data-live-region="([^"]+)"', page)


def _assert_snapshot_matches_page(page: str, regions: dict[str, str]) -> None:
    assert sorted(regions) == sorted(_page_region_keys(page))
    for key, html in regions.items():
        assert html in page, key


def _write_failed_job(team_root, config_path, *, job_id: str, summary: str = "Memory publication failed."):
    job_store = JobStore(team_root.parent.parent / "memory-store")
    path = _write_job_record(team_root, config_path, job_id=job_id, status="queued")
    record = read_job(path)
    log_dir = team_root / "logs" / "2026-07-16"
    stdout_log = log_dir / f"advisor-scheduled_prompt-{job_id}.out"
    stdout_log.write_text("stdout", encoding="utf-8")
    artifact_dir = job_store.artifact_root("newsletter", job_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "memory.md").write_text("snapshot", encoding="utf-8")
    failed = replace(
        record,
        status="failed",
        stdout_path=str(stdout_log.resolve()),
        changed_files=[{"path": "docs/brief.md", "status": "modified", "lines_added": 3, "lines_removed": 1}],
        execution_summary=summary,
        memory_publication={
            "failed_artifacts": [
                {"name": "memory.md", "path": str((artifact_dir / "memory.md").resolve()), "size": 8}
            ]
        },
    )
    write_job(path, failed)
    return path, artifact_dir


def test_live_job_list_snapshot_renders_the_same_regions_as_the_page(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-live-a", status="queued")

    page = client.get("/newsletter/jobs")
    snapshot = client.get("/newsletter/jobs?__live=1")
    regions = _live_regions(snapshot)
    body = snapshot.json()

    assert body["binding"] == {"page": "jobs", "team": "newsletter", "entity": None, "tab": None, "query": {}}
    assert body["structure"] == JOBS_LIST_STRUCTURE
    assert set(JOBS_LIST_REGION_MACROS) <= set(regions)
    _assert_snapshot_matches_page(page.text, regions)
    assert 'data-live-key="job:job-live-a"' in regions["jobs-list"]
    assert regions["jobs-count"] == "1 job"
    assert "Queue: 1 waiting" in regions["jobs-queue"]
    registration = json.loads(_LIVE_INITIAL.search(page.text).group(1))
    assert registration["url"] == "/newsletter/jobs?__live=1"
    assert registration["binding"] == body["binding"]
    assert registration["structure"] == body["structure"]


def test_live_job_list_snapshot_follows_lifecycle_additions_and_removals(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    waiting = _write_job_record(team_root, config_path, job_id="job-live-w", status="waiting_for_memory")
    first = client.get("/newsletter/jobs?__live=1")
    cancel_action = 'action="/newsletter/jobs/job-live-w/cancel"'
    assert cancel_action in _live_regions(first)["jobs-list"]
    assert client.get(
        "/newsletter/jobs?__live=1", headers={"If-None-Match": first.headers["etag"]}
    ).status_code == 304

    transition_job(waiting, "waiting_for_memory", "complete")
    finished = client.get("/newsletter/jobs?__live=1", headers={"If-None-Match": first.headers["etag"]})
    assert finished.status_code == 200
    assert finished.headers["etag"] != first.headers["etag"]
    html = _live_regions(finished)["jobs-list"]
    assert "Complete" in html
    assert cancel_action not in html

    added = _write_job_record(team_root, config_path, job_id="job-live-n", status="queued")
    regions = _live_regions(client.get("/newsletter/jobs?__live=1"))
    assert 'data-live-key="job:job-live-n"' in regions["jobs-list"]
    assert regions["jobs-count"] == "2 jobs"

    added.unlink()
    regions = _live_regions(client.get("/newsletter/jobs?__live=1"))
    assert 'data-live-key="job:job-live-n"' not in regions["jobs-list"]
    assert regions["jobs-count"] == "1 job"


def test_live_job_list_snapshot_skips_a_job_that_vanishes_while_listing(monkeypatch, tmp_path, raw_config):
    import flowgency.web.routes.jobs as jobs_mod

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-live-keep", status="queued")
    gone = _write_job_record(team_root, config_path, job_id="job-live-gone", status="queued")
    original = jobs_mod.read_job

    def vanishing(path, **kwargs):
        if Path(path) == gone:
            gone.unlink()
        return original(path, **kwargs)

    monkeypatch.setattr(jobs_mod, "read_job", vanishing)

    regions = _live_regions(client.get("/newsletter/jobs?__live=1"))

    assert 'data-live-key="job:job-live-keep"' in regions["jobs-list"]
    assert "job-live-gone" not in regions["jobs-list"]


def test_live_job_list_cancel_form_is_a_disposable_button_only_form(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-live-c", status="queued")

    regions = _live_regions(client.get("/newsletter/jobs?__live=1"))

    assert re.search(r'<form method="POST" action="/newsletter/jobs/job-live-c/cancel" data-live-disposable[^>]*>', regions["jobs-list"])
    assert "<input" not in regions["jobs-list"]


def test_live_job_list_snapshot_for_an_unknown_team_is_not_found(monkeypatch, tmp_path, raw_config):
    client, _config_path, _team_root = _seed_app(monkeypatch, tmp_path, raw_config)

    assert client.get("/missing/jobs?__live=1").status_code == 404


def test_live_job_detail_snapshot_renders_the_same_regions_as_the_page(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_failed_job(team_root, config_path, job_id="job-live-f")

    page = client.get("/newsletter/jobs/job-live-f")
    snapshot = client.get("/newsletter/jobs/job-live-f?__live=1")
    regions = _live_regions(snapshot)
    body = snapshot.json()

    assert body["binding"] == {
        "page": "job-detail", "team": "newsletter", "entity": "job-live-f", "tab": None, "query": {},
    }
    assert body["structure"] == JOB_DETAIL_STRUCTURE
    assert set(JOB_DETAIL_REGION_MACROS) <= set(regions)
    _assert_snapshot_matches_page(page.text, regions)
    assert "Failed" in regions["job-status"]
    assert "Brand Strategist" in regions["job-header"]
    assert "Routine: Daily review" in regions["job-meta"]
    assert "docs/brief.md" in regions["job-changes"]
    assert "Failed memory snapshot" in regions["job-artifacts"]
    assert "/newsletter/jobs/job-live-f?artifact=memory.md" in regions["job-artifacts"]
    assert "advisor-scheduled_prompt-job-live-f.out" in regions["job-logs"]
    assert "1 retained artifact" in regions["job-publication"]
    assert "Memory publication failed." in regions["job-summary"]
    registration = json.loads(_LIVE_INITIAL.search(page.text).group(1))
    assert registration["url"] == "/newsletter/jobs/job-live-f?__live=1"
    assert registration["binding"] == body["binding"]


def test_live_job_detail_diagnostics_is_a_closed_keyed_disclosure(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(team_root, config_path, job_id="job-live-d", status="waiting_for_memory")

    html = _live_regions(client.get("/newsletter/jobs/job-live-d?__live=1"))["job-diagnostics"]

    assert re.search(r'<details data-live-key="job-diagnostics:details"[^>]*>', html)
    assert " open" not in re.search(r"<details[^>]*>", html).group(0)
    assert "Memory hash:" in html


def test_live_job_detail_status_and_actions_follow_the_job_lifecycle(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-live-s", status="waiting_for_memory")
    url = "/newsletter/jobs/job-live-s?__live=1"
    before = client.get(url)
    regions = _live_regions(before)
    assert "Waiting for memory" in regions["job-status"]
    assert re.search(r'<form method="POST" action="/newsletter/jobs/job-live-s/cancel" data-live-disposable[^>]*>', regions["job-actions"])
    assert client.get(url, headers={"If-None-Match": before.headers["etag"]}).status_code == 304

    transition_job(path, "waiting_for_memory", "complete")
    after = client.get(url, headers={"If-None-Match": before.headers["etag"]})
    regions = _live_regions(after)

    assert after.headers["etag"] != before.headers["etag"]
    assert "Complete" in regions["job-status"]
    assert "Waiting for memory" not in regions["job-status"]
    assert "/cancel" not in regions["job-actions"]


def test_live_job_detail_offers_resume_as_a_disposable_form_and_keeps_the_notice_static(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(team_root, config_path, job_id="job-live-r", session_id="sess-live")

    page = client.get("/newsletter/jobs/job-live-r?resume=failed")
    regions = _live_regions(client.get("/newsletter/jobs/job-live-r?__live=1"))

    assert re.search(r'<form method="POST" action="/newsletter/jobs/job-live-r/resume" data-live-disposable[^>]*>', regions["job-actions"])
    assert 'id="resume-command"' in regions["job-resume"]
    assert "sess-live" in regions["job-resume"]
    assert "Could not open a terminal" in page.text
    assert not any("Could not open a terminal" in html for html in regions.values())
    assert "navigator.clipboard" not in page.text
    assert '<script src="/static/job-actions.js"></script>' in page.text


def test_live_job_detail_artifacts_and_publication_follow_the_retained_files(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path, artifact_dir = _write_failed_job(team_root, config_path, job_id="job-live-a")
    url = "/newsletter/jobs/job-live-a?__live=1"
    (artifact_dir / "second-draft.md").write_text("second", encoding="utf-8")
    record = read_job(path)
    publication = {
        "failed_artifacts": [
            *record.memory_publication["failed_artifacts"],
            {"name": "second-draft.md", "path": str((artifact_dir / "second-draft.md").resolve()), "size": 6},
        ]
    }
    write_job(path, replace(record, memory_publication=publication))

    regions = _live_regions(client.get(url))

    assert "Second Draft" in regions["job-artifacts"]
    assert "2 retained artifacts" in regions["job-publication"]

    (artifact_dir / "memory.md").unlink()
    (artifact_dir / "second-draft.md").unlink()
    write_job(path, replace(read_job(path), memory_publication={}))
    regions = _live_regions(client.get(url))

    assert regions["job-artifacts"].strip() == ""
    assert regions["job-publication"].strip() == ""


def test_live_job_detail_summary_is_sanitized_before_it_reaches_a_region(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    hostile = '**kept**\n\n<script>window.hit=1</script><img src=x onerror="window.hit=1"><a href="javascript:alert(1)">go</a>'
    _write_failed_job(team_root, config_path, job_id="job-live-x", summary=hostile)

    snapshot = client.get("/newsletter/jobs/job-live-x?__live=1")
    page = client.get("/newsletter/jobs/job-live-x")

    summary = _live_regions(snapshot)["job-summary"]
    assert "<strong>kept</strong>" in summary
    for text in (summary, page.text):
        assert "<script>window.hit" not in text
        assert "onerror" not in text
        assert "javascript:" not in text


def test_live_job_detail_never_serves_an_artifact_as_a_snapshot(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_failed_job(team_root, config_path, job_id="job-live-dl")

    download = client.get("/newsletter/jobs/job-live-dl?__live=1&artifact=memory.md")
    traversal = client.get("/newsletter/jobs/job-live-dl?__live=1&artifact=..%2F..%2Fsecret.txt")
    missing = client.get("/newsletter/jobs/job-live-dl?__live=1&artifact=absent.md")

    assert download.status_code == 200
    assert download.text == "snapshot"
    assert "json" not in download.headers["content-type"]
    assert traversal.status_code in {400, 403}
    assert missing.status_code == 404


def test_live_job_detail_snapshot_never_runs_resume_or_cancel(monkeypatch, tmp_path, raw_config):
    import flowgency.web.routes.jobs as jobs_mod

    client, config_path, team_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(team_root, config_path, job_id="job-live-n", status="waiting_for_memory")

    def forbidden(*args, **kwargs):
        raise AssertionError("a snapshot must not run an action")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", forbidden)
    monkeypatch.setattr(jobs_mod, "cancel_job", forbidden)

    assert client.get("/newsletter/jobs/job-live-n?__live=1").status_code == 200
    assert client.get("/newsletter/jobs?__live=1").status_code == 200
    assert read_job(path).status == "waiting_for_memory"


def test_live_job_detail_snapshot_rejects_unknown_jobs_and_teams(monkeypatch, tmp_path, raw_config):
    client, _config_path, _team_root = _seed_app(monkeypatch, tmp_path, raw_config)

    assert client.get("/newsletter/jobs/job-live-none?__live=1").status_code == 404
    assert client.get("/missing/jobs/job-live-none?__live=1").status_code == 404
    assert client.get("/newsletter/jobs/..%5Cjob?__live=1").status_code == 404





