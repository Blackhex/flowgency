from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

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
    paths = create_group_environment(tmp_path, "newsletter")
    group_root = paths.state_root
    for rel in [
        ("logs", "2026-07-16"),
        ("observations",),
        ("proposals",),
        ("decisions",),
        ("locks",),
    ]:
        group_root.joinpath(*rel).mkdir(parents=True, exist_ok=True)
    _write_blueprint(library_root, "advisor", "Advisor")

    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["compilation_cache"] = str(cache_root)
    raw["agency"]["memory_store"] = str(memory_root)
    raw["agency"]["prompt_store"] = str(tmp_path / "prompts")
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
    return TestClient(app_mod.app), config_path, group_root


def _write_job_record(
    group_root: Path,
    config_path: Path,
    *,
    group_id: str = "newsletter",
    job_id: str = "job-1",
    status: str = "queued",
    due_at: str | None = None,
) -> Path:
    job_store = JobStore(group_root.parent.parent / "memory-store")
    workspace_root = group_root.parent.parent / "workspaces" / group_id
    spec = JobSpec(
        schema_version=3,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key=group_id,
        group_root=str(group_root.resolve()),
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
            cache_path=str((group_root.parent.parent / "compiled-agents" / "copilot" / "v1" / "digest-1").resolve()),
        ),
        routine_id="daily-review",
        skill="daily-review",
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
            path=str((group_root.parent.parent / "memory-store" / "channel-support").resolve()),
        ),
        trigger_context={"source": "test"},
        prompt_source={"type": "routine", "routine_id": "daily-review", "title": "Daily review"},
        timeout_override=None,
        created_at="2026-07-16T00:00:00+00:00",
    )
    path = job_store.path(group_id, job_id)
    record = JobRecord.from_spec(spec, due_at=due_at)
    write_job(path, record)
    if status != "queued":
        transition_job(path, "queued", status)
    return path


def test_job_list_is_group_scoped(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-1", status="queued")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    research_paths = create_group_environment(tmp_path, "research")
    other_group = research_paths.state_root
    (other_group / "logs" / "2026-07-16").mkdir(parents=True, exist_ok=True)
    raw["groups"]["research"] = {
        **apply_group_paths({}, research_paths),
        "name": "Research",
        "default_integration": "copilot",
        "agents": deepcopy(raw["groups"]["newsletter"]["agents"]),
    }
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    _write_job_record(other_group, config_path, group_id="research", job_id="job-2", status="queued")

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
        stdout_path=str((group_root / "logs" / "2026-07-16" / "advisor-scheduled_prompt-job-failed.out").resolve()),
        stderr_path=str((group_root / "logs" / "2026-07-16" / "advisor-scheduled_prompt-job-failed.err").resolve()),
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
    moved_root.joinpath("logs", "2026-07-16").mkdir(parents=True)
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


def test_job_detail_links_logs_to_viewer(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-logs", status="queued")
    log_dir = group_root / "logs" / "2026-07-16"
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


def test_job_detail_omits_log_link_without_path(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-nolog", status="queued")
    log_dir = group_root / "logs" / "2026-07-16"
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


def _write_resumable_job(group_root, config_path, *, job_id, session_id):
    path = _write_job_record(group_root, config_path, job_id=job_id, status="queued")
    record = read_job(path)
    write_job(path, replace(record, status="complete", session_id=session_id))
    return path


def test_job_detail_offers_resume_when_session_known(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-resume", session_id="sess-1")

    response = client.get("/newsletter/jobs/job-resume")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-resume/resume" in response.text
    assert "--resume" in response.text
    assert "sess-1" in response.text


def test_job_detail_hides_resume_without_session(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-nosess", status="queued")
    write_job(path, replace(read_job(path), status="complete"))

    response = client.get("/newsletter/jobs/job-nosess")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-nosess/resume" not in response.text


def test_job_detail_hides_resume_without_integration_support(monkeypatch, tmp_path, raw_config):
    from agency.integrations.agency.copilot import CopilotIntegration

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-unsup", session_id="sess-9")
    monkeypatch.setattr(CopilotIntegration, "resume_command", lambda self, session_id: None)

    response = client.get("/newsletter/jobs/job-unsup")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-unsup/resume" not in response.text


def test_resume_spawns_terminal_and_redirects(monkeypatch, tmp_path, raw_config):
    import agency.web.routes.jobs as jobs_mod
    from agency.integrations.agency.copilot import CopilotIntegration

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-spawn", session_id="sess-2")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")
    captured = {}

    def fake_spawn(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        return "copilot --resume sess-2"

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-spawn/resume", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/jobs/job-spawn?resume=launched"
    assert captured["command"] == ["/opt/copilot", "--resume", "sess-2"]
    assert captured["cwd"] == Path(group_root.parent.parent / "workspaces" / "newsletter")


def test_resume_reports_failure(monkeypatch, tmp_path, raw_config):
    import agency.web.routes.jobs as jobs_mod
    from agency.integrations import IntegrationError
    from agency.integrations.agency.copilot import CopilotIntegration

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-nospawn", session_id="sess-3")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")

    def fake_spawn(command, cwd):
        raise IntegrationError("no terminal")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-nospawn/resume", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/jobs/job-nospawn?resume=failed"


def test_resume_rejects_unsafe_session_id(monkeypatch, tmp_path, raw_config):
    import agency.web.routes.jobs as jobs_mod

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(
        group_root,
        config_path,
        job_id="job-evil",
        session_id="a; rm -rf ~",
    )

    def fail_spawn(command, cwd):
        raise AssertionError("spawn must not be reached")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fail_spawn)

    response = client.post("/newsletter/jobs/job-evil/resume", follow_redirects=False)

    assert response.status_code == 400


def test_resume_unknown_job_is_not_found(monkeypatch, tmp_path, raw_config):
    client, _config_path, _group_root = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.post("/newsletter/jobs/job-missing/resume", follow_redirects=False)

    assert response.status_code == 404


def test_job_detail_shows_failure_notice_when_resume_failed(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-notice", session_id="sess-n")

    response = client.get("/newsletter/jobs/job-notice?resume=failed")

    assert response.status_code == 200
    assert "Could not open a terminal" in response.text


def test_waiting_jobs_show_their_position(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-pos-a", status="queued")
    _write_job_record(group_root, config_path, job_id="job-pos-b", status="queued")
    _write_job_record(group_root, config_path, job_id="job-pos-c", status="queued")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "1 of 3" in response.text


def test_a_position_counts_the_whole_queue_not_just_this_group(
    monkeypatch, tmp_path, raw_config
):
    """The pool is machine-wide, so a position must be too."""
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    research_paths = create_group_environment(tmp_path, "research")
    other_group = research_paths.state_root
    (other_group / "logs" / "2026-07-16").mkdir(parents=True, exist_ok=True)
    raw["groups"]["research"] = {
        **apply_group_paths({}, research_paths),
        "name": "Research",
        "default_integration": "copilot",
        "agents": deepcopy(raw["groups"]["newsletter"]["agents"]),
    }
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    _write_job_record(
        other_group,
        config_path,
        group_id="research",
        job_id="job-earlier",
        due_at="2026-07-16T08:00:00",
    )
    _write_job_record(
        group_root,
        config_path,
        job_id="job-later",
        due_at="2026-07-16T09:00:00",
    )

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "job-earlier" not in response.text
    assert "2 of 2" in response.text


def test_a_running_job_has_no_position(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-pos-running", status="running")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "Running" in response.text  # the job row is present
    assert " of " not in response.text  # no position badge anywhere on the page


def test_a_queued_job_offers_cancel(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_job_record(group_root, config_path, job_id="job-pos-q", status="queued")

    response = client.get("/newsletter/jobs")

    assert response.status_code == 200
    assert "/jobs/" in response.text
    assert "cancel" in response.text.lower()
