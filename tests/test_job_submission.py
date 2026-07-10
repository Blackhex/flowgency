from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agency.jobs import JobSpec, JobSubmissionError, JobValidationError, submit_job
from agency.jobs.launcher import (
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    DetachedProcessLauncher,
    LaunchResult,
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