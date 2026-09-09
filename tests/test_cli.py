"""Tests for the CLI interface."""

from argparse import Namespace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

import flowgency.app as app_mod
from flowgency import cli
from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from flowgency.jobs.store import write_job
from tests._team_helpers import apply_team_paths, create_team_environment


def _setup_jobs_team(
    tmp_path,
    monkeypatch,
    *,
    job_id="cli-job",
    started_at="2026-07-11T10:00:00+00:00",
):
    """Create a team with one complete job that has changed files, and wire it
    into the app registry the CLI reads through get_team."""
    paths = create_team_environment(
        tmp_path,
        "test",
        create_state=True,
    )
    team_dir = paths.state_root
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flowgency": {
                    "title": "Flowgency",
                    "default_team": "test",
                    "ai_backend": "claude-code",
                    "agent_library": str((tmp_path / "agent-library").resolve()),
                    "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
                    "memory_store": str((tmp_path / "memory").resolve()),
                    "prompt_store": str((tmp_path / "prompts").resolve()),
                },
                "teams": {
                    "test": apply_team_paths({
                        "name": "Test",
                        "default_integration": "script",
                        "agents": [
                            {
                                "name": "engineer",
                                "blueprint": "engineer-blueprint",
                                "integration": "script",
                                "integration_config": {"command": "echo ok"},
                            }
                        ],
                    }, paths)
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    spec = JobSpec(
        schema_version=5,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key="test",
        team_root=str(team_dir.resolve()),
        agent_name="engineer",
        workspace_root=str(team_dir.resolve()),
        trigger="decision",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="engineer-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
        ),
        routine_id=None,
        skill=None,
        skill_arguments=(),
        task_input="Do the work",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": job_id},
            canonical_json=f'{{"job":"{job_id}","scope":"run","version":1}}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "decision"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    record = JobRecord.from_spec(spec)
    record.status = "complete"
    record.exit_code = 0
    record.started_at = started_at
    record.completed_at = "2026-07-11T10:00:05+00:00"
    record.changed_files = [{"path": "a.txt", "status": "modified", "lines_added": 2, "lines_removed": 1}]
    stdout_path = team_dir / "logs" / f"{spec.job_id}.out"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text("line one\nline two\nline three\n", encoding="utf-8")
    record.stdout_path = str(stdout_path)
    job_store = JobStore(tmp_path / "memory")
    write_job(job_store.path("test", spec.job_id), record)

    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    snapshot = cli._snapshot_read_only(config_path.resolve())
    monkeypatch.setattr(
        cli,
        "_team",
        lambda args: (
            snapshot,
            "test",
            Namespace(name="Test", path=team_dir),
        ),
    )
    return spec


def test_cli_help_shows_subcommands():
    """Running flowgency --help should list available subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "flowgency.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "inbox" in result.stdout
    assert "status" in result.stdout


def test_cli_no_args_shows_help():
    """Running flowgency with no args should show help."""
    result = subprocess.run(
        [sys.executable, "-m", "flowgency.cli"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert "serve" in output or result.returncode == 0


def test_command_handlers_return_integer_statuses(monkeypatch):
    monkeypatch.setattr(cli, "run_server", lambda **options: None)
    assert cli.cmd_serve(Namespace(host="127.0.0.1", port=8500, reload=False, config="config.yaml")) == 0


def test_cli_serve_help_shows_reload():
    result = subprocess.run(
        [sys.executable, "-m", "flowgency.cli", "serve", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--reload" in result.stdout


def test_cmd_serve_forwards_arguments_without_mutating_sys_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_server", lambda **options: calls.append(options))
    original_argv = sys.argv.copy()

    cli.cmd_serve(Namespace(host="127.0.0.1", port=8700, reload=True, config="config.yaml"))

    assert calls == [{"host": "127.0.0.1", "port": 8700, "reload": True}]
    assert sys.argv == original_argv


@pytest.mark.parametrize("selection", ["explicit", "environment", "default"])
def test_cmd_serve_config_precedence_is_visible_at_lazy_import(tmp_path, monkeypatch, selection):
    explicit_path = tmp_path / "explicit.yaml"
    environment_path = tmp_path / "environment.yaml"
    default_path = tmp_path / "config.yaml"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLOWGENCY_CONFIG", str(environment_path))
    args = Namespace(host="127.0.0.1", port=8700, reload=False)
    if selection == "explicit":
        args.config = str(explicit_path)
        expected = explicit_path
    elif selection == "environment":
        expected = environment_path
    else:
        monkeypatch.delenv("FLOWGENCY_CONFIG")
        expected = default_path

    observed = []

    def fake_import(name):
        assert name == "flowgency.app"
        observed.append(Path(os.environ["FLOWGENCY_CONFIG"]))
        return SimpleNamespace(run_server=lambda **options: None)

    monkeypatch.setattr(cli.importlib, "import_module", fake_import)

    assert cli.cmd_serve(args) == 0
    assert observed == [expected.resolve()]


def test_serve_app_config_path_honors_FLOWGENCY_CONFIG_at_import(tmp_path):
    selected_path = tmp_path / "missing.yaml"
    environment = os.environ.copy()
    environment["FLOWGENCY_CONFIG"] = str(selected_path)

    result = subprocess.run(
        [sys.executable, "-c", "import flowgency.app; print(flowgency.app.CONFIG_PATH)"],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0
    assert Path(result.stdout.strip()) == selected_path.resolve()


def test_serve_missing_config_bootstraps_selected_path_not_cwd(tmp_path):
    selected_path = tmp_path / "selected.yaml"
    environment = os.environ.copy()
    environment["FLOWGENCY_CONFIG"] = str(selected_path)
    script = (
        "import flowgency.app as app; "
        "app.uvicorn.run = lambda *args, **kwargs: None; "
        "app.run_server('127.0.0.1', 8500)"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    output = result.stdout + result.stderr
    assert "First run: open http://localhost:8500/setup to launch guided Flowgency setup." in output
    assert "/admin/" not in output
    assert "/setup" in output
    assert not selected_path.exists()
    assert not (tmp_path / "config.yaml").exists()


# Task 2 (Official Dispatch CLI): Tests for cmd_dispatch


def _dispatch_status(state="active", installed=True, error=None):
    return {
        "state": state,
        "installed": installed,
        "enabled": state == "active",
        "timer_active": state == "active",
        "definition_matches": installed and state != "misconfigured",
        "config_conflict": False,
        "config_path": None,
        "interval": 15 if installed else None,
        "expected_config_path": "C:/config.yaml",
        "expected_interval": 15,
        "mismatches": [] if state != "misconfigured" else ["interval"],
        "error": error,
    }


def _write_dispatch_config(path):
    (path.parent / "agent-library").mkdir(parents=True, exist_ok=True)
    (path.parent / "memory").mkdir(parents=True, exist_ok=True)
    (path.parent / "prompts").mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flowgency": {
                    "agent_library": "agent-library",
                    "compilation_cache": "compiled-agents",
                    "memory_store": "memory",
                    "prompt_store": "prompts",
                    "dispatch": {"interval": 15},
                },
                "teams": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_cli_help_shows_dispatch_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "flowgency.cli", "dispatch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert all(command in result.stdout for command in ("install", "status", "uninstall"))


def test_cmd_dispatch_install_persists_interval_and_forwards_replace(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_dispatch_config(config_path)
    calls = []
    monkeypatch.setattr(cli, "install_timer", lambda path, interval, replace=False: calls.append((path, interval, replace)))
    monkeypatch.setattr(cli, "get_timer_status", lambda path, interval: _dispatch_status())
    exit_code = cli.cmd_dispatch(
        Namespace(dispatch_command="install", config=str(config_path), interval=30, replace=True, force=False)
    )
    assert exit_code == 0
    assert calls == [(str(config_path.resolve()), 30, True)]
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["flowgency"]["dispatch"] == {"interval": 30}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_dispatch_status(), 0),
        (_dispatch_status(state="inactive", installed=False), 1),
        (_dispatch_status(state="inactive", installed=True), 1),
        (_dispatch_status(state="misconfigured", installed=True), 3),
        (_dispatch_status(state="inactive", installed=False, error="unavailable"), 1),
    ],
)
def test_dispatch_status_exit_codes(tmp_path, monkeypatch, status, expected):
    config_path = tmp_path / "config.yaml"
    _write_dispatch_config(config_path)
    monkeypatch.setattr(cli, "get_timer_status", lambda path, interval: status)
    args = Namespace(dispatch_command="status", config=str(config_path), interval=None, replace=False, force=False)
    assert cli.cmd_dispatch(args) == expected


def test_cmd_dispatch_uninstall_forwards_force(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_dispatch_config(config_path)
    calls = []
    monkeypatch.setattr(cli, "uninstall_timer", lambda path, force=False: calls.append((path, force)))
    args = Namespace(dispatch_command="uninstall", config=str(config_path), interval=None, replace=False, force=True)
    assert cli.cmd_dispatch(args) == 0
    assert calls == [(str(config_path.resolve()), True)]


def test_cli_help_shows_jobs_and_logs():
    result = subprocess.run(
        [sys.executable, "-m", "flowgency.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "jobs" in result.stdout
    assert "logs" in result.stdout


def test_cmd_jobs_lists_records(tmp_path, monkeypatch, capsys):
    _setup_jobs_team(tmp_path, monkeypatch)
    cli.cmd_jobs(Namespace(team="test", status=None, agent=None, json=False))
    out = capsys.readouterr().out
    assert "engineer" in out
    assert "complete" in out
    assert "1 file(s)" in out


def test_cmd_jobs_json_reports_changed_file_count(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_team(tmp_path, monkeypatch)
    cli.cmd_jobs(Namespace(team="test", status=None, agent=None, json=True))
    out = capsys.readouterr().out
    assert spec.job_id in out
    assert '"changed_files": 1' in out


def test_cmd_jobs_status_filter_excludes_non_matching(tmp_path, monkeypatch, capsys):
    _setup_jobs_team(tmp_path, monkeypatch)
    cli.cmd_jobs(Namespace(team="test", status="failed", agent=None, json=False))
    out = capsys.readouterr().out
    assert "engineer" not in out


def test_cmd_logs_tails_execution_log(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_team(tmp_path, monkeypatch)
    cli.cmd_logs(Namespace(team="test", job_id=spec.job_id, lines=40, stderr=False))
    out = capsys.readouterr().out
    assert "line one" in out
    assert "line three" in out


def test_cmd_logs_no_job_id_lists_recent(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_team(tmp_path, monkeypatch)
    cli.cmd_logs(Namespace(team="test", job_id=None, lines=40, stderr=False))
    out = capsys.readouterr().out
    assert spec.job_id in out


def test_equal_timestamp_jobs_use_deterministic_id_order_in_json_and_logs(
    tmp_path,
    monkeypatch,
    capsys,
):
    timestamp = "2026-07-11T10:00:00+00:00"
    _setup_jobs_team(
        tmp_path,
        monkeypatch,
        job_id="beta-job",
        started_at=timestamp,
    )
    _setup_jobs_team(
        tmp_path,
        monkeypatch,
        job_id="alpha-job",
        started_at=timestamp,
    )

    cli.cmd_jobs(Namespace(team="test", status=None, agent=None, json=True))
    jobs = yaml.safe_load(capsys.readouterr().out)
    assert [job["job_id"] for job in jobs] == ["alpha-job", "beta-job"]

    cli.cmd_logs(Namespace(team="test", job_id=None, lines=40, stderr=False))
    logs = capsys.readouterr().out
    assert logs.index("alpha-job") < logs.index("beta-job")


def test_cmd_logs_unknown_job_exits(tmp_path, monkeypatch):
    _setup_jobs_team(tmp_path, monkeypatch)
    assert cli.cmd_logs(Namespace(team="test", job_id="deadbeef", lines=40, stderr=False)) == 1


def test_cmd_jobs_and_logs_ignore_forged_team_jobs_records(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_team(tmp_path, monkeypatch, job_id="canonical-job")
    forged_path = tmp_path / "team" / "jobs" / "forged-job.yaml"
    forged_path.parent.mkdir(parents=True, exist_ok=True)
    forged_record = JobRecord.from_spec(
        JobSpec(
            schema_version=5,
            job_id="forged-job",
            config_path=spec.config_path,
            config_revision=spec.config_revision,
            team_key="test",
            team_root=spec.team_root,
            agent_name=spec.agent_name,
            workspace_root=spec.workspace_root,
            trigger=spec.trigger,
            integration_name=spec.integration_name,
            integration_config=spec.integration_config,
            blueprint=spec.blueprint,
            routine_id=spec.routine_id,
            skill=spec.skill,
            skill_arguments=spec.skill_arguments,
            task_input=spec.task_input,
            runtime_policy=spec.runtime_policy,
            memory=spec.memory,
            trigger_context=spec.trigger_context,
            prompt_source=spec.prompt_source,
            timeout_override=spec.timeout_override,
            created_at=spec.created_at,
        )
    )
    forged_record.status = "complete"
    forged_record.started_at = spec.created_at
    forged_record.stdout_path = str(tmp_path / "team" / "logs" / "forged-job.out")
    write_job(forged_path, forged_record)

    cli.cmd_jobs(Namespace(team="test", status=None, agent=None, json=True))
    jobs = yaml.safe_load(capsys.readouterr().out)
    assert [job["job_id"] for job in jobs] == ["canonical-job"]

    cli.cmd_logs(Namespace(team="test", job_id=None, lines=40, stderr=False))
    logs = capsys.readouterr().out
    assert "canonical-job" in logs
    assert "forged-job" not in logs


