from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

import pytest

from agency.jobs import JobSpec, JobSubmissionError, JobValidationError, submit_job
from agency.jobs.context import resolve_job_context
from agency.jobs.launcher import (
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    DetachedProcessLauncher,
    LaunchResult,
    SystemdRunLauncher,
    _sanitize_unit_name,
    _systemd_available,
    default_launcher,
)
from agency.jobs.store import read_job


def configured_spec(tmp_path: Path, *, agent="product") -> JobSpec:
    group = tmp_path / "group"
    (group / agent).mkdir(parents=True)
    config = tmp_path / "config.yaml"
    config.write_text(
        "groups:\n  test:\n    name: Test\n    path: "
        + str(group).replace("\\", "/")
        + "\n    agents:\n      - name: "
        + agent
        + "\n        integration: script\n"
        "        integration_config:\n          command: echo ok\n"
    )
    return JobSpec.create(
        config_path=config,
        group_key="test",
        agent_name=agent,
        trigger="manual_prompt",
        prompt_source={"type": "saved_prompt", "path": "routine.md"},
        prompt_content="Run it",
    )


def test_submit_persists_then_launches(tmp_path):
    spec = configured_spec(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job(spec, launcher)

    record = read_job(handle.path)
    assert record.status == "queued"
    assert launcher.launch.call_args.args == (handle.path,)
    assert handle.worker_pid == 4321


def test_submit_marks_record_failed_when_launch_fails(tmp_path):
    spec = configured_spec(tmp_path)
    launcher = Mock()
    launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied") as error:
        submit_job(spec, launcher)

    record = read_job(error.value.job_path)
    assert record.status == "failed"
    assert "spawn denied" in record.execution_summary


def test_resolve_job_context_prefers_per_agent_timeout_over_group_default(tmp_path):
    """Worker context resolution must be the sole timeout authority: a
    configured per-agent timeout wins over the group default when the spec
    itself carries no override (trigger routes no longer pass one)."""
    group = tmp_path / "group"
    (group / "product").mkdir(parents=True)
    config = tmp_path / "config.yaml"
    config.write_text(
        "groups:\n  test:\n    name: Test\n    path: "
        + str(group).replace("\\", "/")
        + "\n    agents:\n      - name: product\n        integration: script\n"
        "        integration_config:\n          command: echo ok\n"
        "    dispatch:\n      timeout: 1800\n      agents:\n        product:\n          timeout: 45\n"
    )
    spec = JobSpec.create(
        config_path=config,
        group_key="test",
        agent_name="product",
        trigger="manual_prompt",
        prompt_source={"type": "saved_prompt", "path": "routine.md"},
        prompt_content="Run it",
    )
    assert spec.timeout_override is None

    context = resolve_job_context(spec)

    assert context.timeout == 45


def test_submit_rejects_missing_or_non_executable_agent(tmp_path):
    spec = configured_spec(tmp_path, agent="missing")
    Path(spec.config_path).write_text(
        "groups:\n  test:\n    name: Test\n    path: "
        + str(tmp_path / "group").replace("\\", "/")
        + "\n    agents:\n      - name: missing\n        integration: sdk\n"
    )
    with pytest.raises(JobValidationError):
        submit_job(spec, Mock())


def test_windows_launcher_uses_detached_flags(tmp_path):
    with patch("agency.jobs.launcher.os.name", "nt"), patch(
        "agency.jobs.launcher.subprocess.Popen"
    ) as popen:
        popen.return_value.pid = 77
        result = DetachedProcessLauncher().launch(tmp_path / "job.yaml")
    flags = popen.call_args.kwargs["creationflags"]
    assert flags & DETACHED_PROCESS
    assert flags & CREATE_NEW_PROCESS_GROUP
    assert result.worker_pid == 77


def test_posix_launcher_starts_new_session(tmp_path):
    with patch("agency.jobs.launcher.os.name", "posix"), patch(
        "agency.jobs.launcher.subprocess.Popen"
    ) as popen:
        popen.return_value.pid = 78
        DetachedProcessLauncher().launch(tmp_path / "job.yaml")
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["shell"] is False


# --- SystemdRunLauncher tests ---


def test_systemd_launcher_argv_and_shell_false(tmp_path):
    """SystemdRunLauncher uses correct systemd-run argv with shell=False."""
    job = tmp_path / "abc-123.yaml"
    with patch("agency.jobs.launcher.subprocess.run") as run_mock:
        result = SystemdRunLauncher().launch(job)
    call_args = run_mock.call_args
    argv = call_args.args[0]
    assert argv[0] == "systemd-run"
    assert "--user" in argv
    assert "--collect" in argv
    assert "--unit=agency-job-abc-123" in argv
    assert "--" in argv
    # worker command after --
    sep_idx = argv.index("--")
    worker_part = argv[sep_idx + 1 :]
    assert "-m" in worker_part
    assert "agency.jobs.worker" in worker_part
    assert str(job.resolve()) in worker_part
    # shell=False, no stream inheritance
    assert call_args.kwargs["shell"] is False
    assert call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert call_args.kwargs["stdout"] == subprocess.DEVNULL
    assert call_args.kwargs["stderr"] == subprocess.DEVNULL
    # returns None pid (systemd owns the process)
    assert result.worker_pid is None


def test_systemd_launcher_no_stream_inheritance(tmp_path):
    """Streams are explicitly DEVNULL — no stdin/stdout/stderr leak."""
    job = tmp_path / "x.yaml"
    with patch("agency.jobs.launcher.subprocess.run") as run_mock:
        SystemdRunLauncher().launch(job)
    kw = run_mock.call_args.kwargs
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["stdout"] == subprocess.DEVNULL
    assert kw["stderr"] == subprocess.DEVNULL


def test_sanitize_unit_name_replaces_unsafe_chars():
    """Unit names only contain [a-zA-Z0-9_.-]."""
    assert _sanitize_unit_name("job-abc-123") == "job-abc-123"
    assert _sanitize_unit_name("job:with/slashes!") == "job_with_slashes_"
    assert _sanitize_unit_name("a b c") == "a_b_c"
    # UUID-style IDs pass through (dashes and hex are safe)
    assert _sanitize_unit_name("550e8400-e29b-41d4-a716-446655440000") == \
        "550e8400-e29b-41d4-a716-446655440000"


def test_systemd_launcher_launch_result_has_none_pid(tmp_path):
    """LaunchResult from systemd launcher has worker_pid=None."""
    job = tmp_path / "job.yaml"
    with patch("agency.jobs.launcher.subprocess.run"):
        result = SystemdRunLauncher().launch(job)
    assert result == LaunchResult(worker_pid=None)


# --- Detection and fallback ---


def test_systemd_available_false_on_non_linux():
    """Detection returns False when not on Linux."""
    with patch("agency.jobs.launcher.sys.platform", "win32"):
        assert _systemd_available() is False
    with patch("agency.jobs.launcher.sys.platform", "darwin"):
        assert _systemd_available() is False


def test_systemd_available_false_when_no_binary():
    """Detection returns False when systemd-run not on PATH."""
    with patch("agency.jobs.launcher.sys.platform", "linux"), \
         patch("agency.jobs.launcher.shutil.which", return_value=None):
        assert _systemd_available() is False


def test_systemd_available_true_when_running():
    """Detection returns True when systemctl reports running."""
    with patch("agency.jobs.launcher.sys.platform", "linux"), \
         patch("agency.jobs.launcher.shutil.which", return_value="/usr/bin/systemd-run"), \
         patch("agency.jobs.launcher.subprocess.run") as run_mock:
        run_mock.return_value.stdout = b"running\n"
        assert _systemd_available() is True


def test_systemd_available_true_when_degraded():
    """Detection returns True when systemctl reports degraded."""
    with patch("agency.jobs.launcher.sys.platform", "linux"), \
         patch("agency.jobs.launcher.shutil.which", return_value="/usr/bin/systemd-run"), \
         patch("agency.jobs.launcher.subprocess.run") as run_mock:
        run_mock.return_value.stdout = b"degraded\n"
        assert _systemd_available() is True


def test_systemd_available_false_when_manager_offline():
    """Detection returns False when systemctl reports something else."""
    with patch("agency.jobs.launcher.sys.platform", "linux"), \
         patch("agency.jobs.launcher.shutil.which", return_value="/usr/bin/systemd-run"), \
         patch("agency.jobs.launcher.subprocess.run") as run_mock:
        run_mock.return_value.stdout = b"offline\n"
        assert _systemd_available() is False


# --- default_launcher factory ---


def test_default_launcher_selects_systemd_when_available():
    """Factory returns SystemdRunLauncher when detection is True."""
    launcher = default_launcher(_detect=lambda: True)
    assert isinstance(launcher, SystemdRunLauncher)


def test_default_launcher_selects_detached_when_unavailable():
    """Factory returns DetachedProcessLauncher when detection is False."""
    launcher = default_launcher(_detect=lambda: False)
    assert isinstance(launcher, DetachedProcessLauncher)


# --- submit_job uses default_launcher ---


def test_submit_job_uses_default_launcher_when_none_provided(tmp_path):
    """submit_job with no explicit launcher uses default_launcher factory."""
    spec = configured_spec(tmp_path)
    fake_launcher = Mock()
    fake_launcher.launch.return_value = LaunchResult(worker_pid=999)
    with patch("agency.jobs.submission.default_launcher", return_value=fake_launcher):
        handle = submit_job(spec)
    assert fake_launcher.launch.called
    assert handle.worker_pid == 999
