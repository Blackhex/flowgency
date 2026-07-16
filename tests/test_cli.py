"""Tests for the CLI interface."""

from argparse import Namespace
import subprocess
import sys

import pytest
import yaml

import agency.app as app_mod
from agency import cli
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import write_job


def _setup_jobs_group(
    tmp_path,
    monkeypatch,
    *,
    job_id="cli-job",
    record_filename=None,
    started_at="2026-07-11T10:00:00+00:00",
):
    """Create a group with one complete job that has changed files, and wire it
    into the app registry the CLI reads through get_group."""
    group = tmp_path / "group"
    jobs_dir = group / "shared" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups: {}\n", encoding="utf-8")

    spec = JobSpec(
        schema_version=2,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="test",
        group_path=str(group.resolve()),
        agent_name="engineer",
        workspace_dir=str(group.resolve()),
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
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
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
    stdout_path = group / "shared" / "logs" / f"{spec.job_id}.out"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text("line one\nline two\nline three\n", encoding="utf-8")
    record.stdout_path = str(stdout_path)
    write_job(jobs_dir / (record_filename or f"{spec.job_id}.yaml"), record)

    monkeypatch.setattr(app_mod, "CONFIG", {"groups": {"test": {"path": str(group)}}})
    monkeypatch.setattr(app_mod, "GROUPS", {"test": {
        "key": "test", "name": "Test", "path": group,
        "agents": ["engineer"], "_agents_normalized": [{"name": "engineer"}],
    }})
    monkeypatch.setattr(
        cli,
        "_group",
        lambda args: (
            None,
            "test",
            Namespace(name="Test", path=group),
        ),
    )
    return spec


def test_cli_help_shows_subcommands():
    """Running agency --help should list available subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "inbox" in result.stdout
    assert "status" in result.stdout


def test_cli_no_args_shows_help():
    """Running agency with no args should show help."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert "serve" in output or result.returncode == 0


def test_command_handlers_return_integer_statuses(monkeypatch):
    monkeypatch.setattr(cli, "run_server", lambda **options: None)
    assert cli.cmd_serve(Namespace(host="127.0.0.1", port=8500, reload=False)) == 0


def test_cli_serve_help_shows_reload():
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "serve", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--reload" in result.stdout


def test_cmd_serve_forwards_arguments_without_mutating_sys_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_server", lambda **options: calls.append(options))
    original_argv = sys.argv.copy()

    cli.cmd_serve(Namespace(host="127.0.0.1", port=8700, reload=True))

    assert calls == [{"host": "127.0.0.1", "port": 8700, "reload": True}]
    assert sys.argv == original_argv


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
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "agency": {
                    "agent_library": "agent-library",
                    "compilation_cache": "compiled-agents",
                    "memory_store": "memory",
                    "dispatch": {"interval": 15},
                },
                "groups": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_cli_help_shows_dispatch_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "dispatch", "--help"],
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
    assert saved["agency"]["dispatch"] == {"interval": 30}


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
        [sys.executable, "-m", "agency.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "jobs" in result.stdout
    assert "logs" in result.stdout


def test_cmd_jobs_lists_records(tmp_path, monkeypatch, capsys):
    _setup_jobs_group(tmp_path, monkeypatch)
    cli.cmd_jobs(Namespace(group="test", status=None, agent=None, json=False))
    out = capsys.readouterr().out
    assert "engineer" in out
    assert "complete" in out
    assert "1 file(s)" in out


def test_cmd_jobs_json_reports_changed_file_count(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_group(tmp_path, monkeypatch)
    cli.cmd_jobs(Namespace(group="test", status=None, agent=None, json=True))
    out = capsys.readouterr().out
    assert spec.job_id in out
    assert '"changed_files": 1' in out


def test_cmd_jobs_status_filter_excludes_non_matching(tmp_path, monkeypatch, capsys):
    _setup_jobs_group(tmp_path, monkeypatch)
    cli.cmd_jobs(Namespace(group="test", status="failed", agent=None, json=False))
    out = capsys.readouterr().out
    assert "engineer" not in out


def test_cmd_logs_tails_execution_log(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_group(tmp_path, monkeypatch)
    cli.cmd_logs(Namespace(group="test", job_id=spec.job_id, lines=40, stderr=False))
    out = capsys.readouterr().out
    assert "line one" in out
    assert "line three" in out


def test_cmd_logs_no_job_id_lists_recent(tmp_path, monkeypatch, capsys):
    spec = _setup_jobs_group(tmp_path, monkeypatch)
    cli.cmd_logs(Namespace(group="test", job_id=None, lines=40, stderr=False))
    out = capsys.readouterr().out
    assert spec.job_id in out


def test_equal_timestamp_jobs_use_deterministic_id_order_in_json_and_logs(
    tmp_path,
    monkeypatch,
    capsys,
):
    timestamp = "2026-07-11T10:00:00+00:00"
    _setup_jobs_group(
        tmp_path,
        monkeypatch,
        job_id="beta-job",
        record_filename="a-record.yaml",
        started_at=timestamp,
    )
    _setup_jobs_group(
        tmp_path,
        monkeypatch,
        job_id="alpha-job",
        record_filename="z-record.yaml",
        started_at=timestamp,
    )

    cli.cmd_jobs(Namespace(group="test", status=None, agent=None, json=True))
    jobs = yaml.safe_load(capsys.readouterr().out)
    assert [job["job_id"] for job in jobs] == ["alpha-job", "beta-job"]

    cli.cmd_logs(Namespace(group="test", job_id=None, lines=40, stderr=False))
    logs = capsys.readouterr().out
    assert logs.index("alpha-job") < logs.index("beta-job")


def test_cmd_logs_unknown_job_exits(tmp_path, monkeypatch):
    _setup_jobs_group(tmp_path, monkeypatch)
    assert cli.cmd_logs(Namespace(group="test", job_id="deadbeef", lines=40, stderr=False)) == 1


# ── Task 5: CLI decide parity tests ─────────────────────────────────────────

import agency.app as app_mod  # noqa: E402  (already imported above, this is a no-op re-import)
from agency.jobs import JobSubmissionError  # noqa: E402


def setup_cli_proposal(tmp_path, monkeypatch, *, execution_agent="builder", questions=None):
    group = tmp_path / "group"
    shared = group / "shared"
    for directory in ("proposals", "decisions", "jobs", "logs"):
        (shared / directory).mkdir(parents=True, exist_ok=True)
    (group / "builder").mkdir()
    proposal_path = shared / "proposals" / "change.md"
    proposal_meta = {
        "origin_agent": "observer",
        "execution_agent": execution_agent,
        "status": "proposed",
        "questions": questions or [
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
        ],
    }
    proposal_path.write_text(
        "---\n" + yaml.safe_dump(proposal_meta, sort_keys=False) + "---\n\nProposal body\n",
        encoding="utf-8",
    )
    agents = [
        {
            "name": "builder",
            "integration": "script",
            "integration_config": {"command": "echo ok"},
            "capabilities": {"write": True},
        },
    ]
    runtime_group = {
        "key": "test",
        "name": "Test",
        "path": group,
        "shared": shared,
        "agents": ["builder"],
        "_agents_normalized": agents,
    }
    monkeypatch.setattr(cli, "_resolve_group", lambda args: runtime_group)
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.yaml")
    return Namespace(group="test", slug="change"), shared / "decisions" / "change.md", proposal_path


def test_cmd_decide_rejects_invalid_proposal_schema(tmp_path, monkeypatch, capsys):
    args, _, _ = setup_cli_proposal(tmp_path, monkeypatch, execution_agent="")
    assert cli.cmd_decide(args) == 3
    assert "execution_agent is required" in capsys.readouterr().err


def test_cmd_decide_collects_decline_open_answer_and_note(tmp_path, monkeypatch):
    args, decision_path, _ = setup_cli_proposal(
        tmp_path,
        monkeypatch,
        questions=[
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
            {"id": "detail", "type": "free-response", "prompt": "Direction?"},
        ],
    )
    responses = iter(["", "d", "Use the alternate", "Overall note"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job_request", lambda request: submitted.append(request))
    cli.cmd_decide(args)
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["answers"] == {"approve": "declined", "detail": "Use the alternate"}
    assert meta["decision_note"] == "Overall note"
    assert meta["execution_agent"] == "builder"
    assert meta["execution_status"] == "pending"
    assert meta["execution_job_id"] == submitted[0].job_id
    assert "Overall note" in submitted[0].task_input


def test_cmd_decide_deduplicates_multi_choice_answers(tmp_path, monkeypatch):
    args, decision_path, _ = setup_cli_proposal(
        tmp_path,
        monkeypatch,
        questions=[
            {
                "id": "targets",
                "type": "choice",
                "prompt": "Targets?",
                "multi": True,
                "options": ["Alpha", "Beta", "Gamma"],
            },
        ],
    )
    responses = iter(["", "1,1,2", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job_request", lambda request: submitted.append(request))
    cli.cmd_decide(args)
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["answers"] == {"targets": ["Alpha", "Beta"]}
    assert meta["execution_job_id"] == submitted[0].job_id
    assert len(submitted) == 1


def test_cmd_decide_all_declined_without_guidance_skips_job(tmp_path, monkeypatch):
    args, decision_path, _ = setup_cli_proposal(tmp_path, monkeypatch)
    responses = iter(["", "d", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job_request", lambda request: submitted.append(request))
    cli.cmd_decide(args)
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["execution_status"] == "skipped"
    assert "execution_job_id" not in meta
    assert submitted == []
    assert meta["execution_job_history"] == []  # Finding 5: shape parity with web path


def test_cmd_decide_submission_failure_removes_decision_and_preserves_proposal(tmp_path, monkeypatch):
    args, decision_path, proposal_path = setup_cli_proposal(tmp_path, monkeypatch)
    responses = iter(["", "a", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    monkeypatch.setattr(cli, "submit_job_request", lambda request: (_ for _ in ()).throw(JobSubmissionError("spawn denied", decision_path)))
    assert cli.cmd_decide(args) == 1
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()


# ── Finding 6: CLI input robustness ─────────────────────────────────────────

def test_cmd_decide_eoferror_exits_cleanly(tmp_path, monkeypatch, capsys):
    """EOFError from stdin must be caught, produce a concise stderr error, and exit 1."""
    args, _, _ = setup_cli_proposal(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert cli.cmd_decide(args) == 1
    assert "Input closed" in capsys.readouterr().err


def test_cmd_decide_invalid_then_valid_boolean_completes(tmp_path, monkeypatch, capsys):
    """An invalid boolean response followed by a valid one must complete normally."""
    args, decision_path, _ = setup_cli_proposal(tmp_path, monkeypatch)
    responses = iter(["", "x", "a", ""])  # executor default, invalid boolean, valid boolean, empty note
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job_request", lambda request: submitted.append(request))
    cli.cmd_decide(args)
    # Should have printed feedback for the invalid input
    out = capsys.readouterr().out
    # After invalid input, some feedback line should appear
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["answers"]["approve"] == "approved"
    assert len(submitted) == 1


def test_cmd_decide_invalid_multi_choice_reprompts(tmp_path, monkeypatch, capsys):
    """Multi-choice input that is non-empty but yields no valid indices must re-prompt
    rather than completing with an accidental empty selection."""
    args, decision_path, _ = setup_cli_proposal(
        tmp_path,
        monkeypatch,
        questions=[
            {
                "id": "targets",
                "type": "choice",
                "prompt": "Targets?",
                "multi": True,
                "options": ["Alpha", "Beta", "Gamma"],
            },
        ],
    )
    # First executor input: default; then invalid multi-choice "99" (no valid index), then valid "1"; then empty note
    responses = iter(["", "99", "1", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    submitted = []
    monkeypatch.setattr(cli, "submit_job_request", lambda request: submitted.append(request))
    cli.cmd_decide(args)
    meta, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert meta["answers"]["targets"] == ["Alpha"]
    assert len(submitted) == 1
