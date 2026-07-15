"""
Linux-only integration test proving transient systemd services survive submitter exit.

Requires:
- Linux with a user systemd manager (systemctl --user is-system-running → running/degraded)
- The AGENCY_TEST_SYSTEMD=1 environment variable set

Run command:
    AGENCY_TEST_SYSTEMD=1 .venv/bin/python -m pytest tests/test_job_systemd_integration.py -v
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from agency.jobs.launcher import _systemd_available
from agency.jobs.store import read_job

_SKIP_REASON = (
    "Requires Linux with user systemd manager and AGENCY_TEST_SYSTEMD=1"
)

pytestmark = pytest.mark.skipif(
    not (os.environ.get("AGENCY_TEST_SYSTEMD") == "1" and _systemd_available()),
    reason=_SKIP_REASON,
)


def _shell_command(arguments):
    import shlex
    return shlex.join(str(item) for item in arguments)


def test_systemd_worker_survives_submitter_exit(tmp_path):
    """Transient systemd service continues after submitting process exits."""
    group_path = tmp_path / "group"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    gate = tmp_path / "continue"
    sentinel = tmp_path / "completed"
    helper = tmp_path / "agent_helper.py"
    helper.write_text(
        "import pathlib, sys, time\n"
        "gate = pathlib.Path(sys.argv[1])\n"
        "deadline = time.monotonic() + 15\n"
        "while not gate.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "if not gate.exists():\n"
        "    raise SystemExit(2)\n"
        "pathlib.Path(sys.argv[2]).write_text('done')\n"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"groups": {"test": {
        "name": "Test", "path": str(group_path),
        "agents": [{
            "name": "product", "integration": "script",
            "integration_config": {
                "command": _shell_command([sys.executable, helper, gate, sentinel]),
            },
        }],
    }}}))
    job_id_file = tmp_path / "job-id"
    submitter_script = tmp_path / "submitter.py"
    submitter_script.write_text(
        "import pathlib, sys\n"
        "from agency.jobs import JobRequest, submit_job_request\n"
        "config, job_id_file = map(pathlib.Path, sys.argv[1:])\n"
        "request = JobRequest(config_path=config, group_key='test', "
        "agent_name='product', trigger='manual_prompt', task_input='run', routine_id='run-product')\n"
        "handle = submit_job_request(request)\n"  # uses default_launcher → SystemdRunLauncher
        "job_id_file.write_text(handle.job_id)\n"
    )

    # Run submitter as a child process (it will exit after submitting)
    result = subprocess.run(
        [sys.executable, str(submitter_script), str(config_path), str(job_id_file)],
        timeout=10,
    )
    assert result.returncode == 0, "Submitter failed"
    assert job_id_file.exists()
    job_id = job_id_file.read_text().strip()

    # Submitter has exited. The systemd transient service should still be running.
    job_path = group_path / "shared" / "jobs" / f"{job_id}.yaml"
    deadline = time.monotonic() + 5
    while not job_path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert job_path.exists(), "Job record not found"

    record = read_job(job_path)
    assert record.status in ("queued", "running")
    assert not sentinel.exists(), "Agent completed before gate opened"

    # Open the gate so the agent can finish
    gate.write_text("go")

    # Wait for completion
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = read_job(job_path)
        if record.status in ("complete", "failed"):
            break
        time.sleep(0.2)

    assert sentinel.exists(), "Agent never completed"
    assert record.status == "complete"
