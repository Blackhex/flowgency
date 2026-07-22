import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import yaml

from agency.jobs.authority import JobStore
from agency.jobs.store import read_job


def _read_job_eventually(path: Path, *, deadline: float):
    last_error = None
    while time.monotonic() < deadline:
        try:
            return read_job(path)
        except (FileNotFoundError, PermissionError, OSError) as error:
            last_error = error
            time.sleep(0.05)
    if last_error is not None:
        raise last_error
    raise FileNotFoundError(path)


def _shell_command(arguments):
    if os.name == "nt":
        return subprocess.list2cmdline([str(item) for item in arguments])
    return shlex.join(str(item) for item in arguments)


def test_detached_worker_survives_submitter_exit(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    group_path = tmp_path / "group"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    gate = tmp_path / "continue"
    sentinel = tmp_path / "completed"
    helper = tmp_path / "agent_helper.py"
    helper.write_text(
        "import pathlib, sys, time\n"
        "gate = pathlib.Path(sys.argv[1])\n"
        "deadline = time.monotonic() + 10\n"
        "while not gate.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "if not gate.exists():\n"
        "    raise SystemExit(2)\n"
        "pathlib.Path(sys.argv[2]).write_text('done')\n"
    )
    config_path = tmp_path / "config.yaml"
    agent_library = tmp_path / "agent-library"
    blueprint = agent_library / "product-blueprint"
    skill = blueprint / ".agents" / "skills" / "run-product"
    skill.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_text("# Product\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: run-product\ndescription: Run product work.\n---\n\nRun it.\n",
        encoding="utf-8",
    )
    config_path.write_text(yaml.safe_dump({
        "schema_version": 3,
        "agency": {
            "title": "Agency",
            "default_group": "test",
            "ai_backend": "claude-code",
            "agent_library": str(agent_library),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(tmp_path / "memory"),
        },
        "groups": {"test": {
            "name": "Test",
            "workspace_path": str(group_path),
            "path": str(group_path),
            "default_integration": "script",
            "runtime": {
                "timeout": 1800,
                "sandbox": {"mode": "unrestricted", "roots": []},
                "tools": {"mode": "all", "names": []},
            },
            "agents": [{
                "name": "product",
                "blueprint": "product-blueprint",
                "integration": "script",
                "integration_config": {
                    "command": _shell_command([sys.executable, helper, gate, sentinel]),
                },
                "routines": [],
            }],
        }},
    }), encoding="utf-8")
    job_id_file = tmp_path / "job-id"
    parent_pid_file = tmp_path / "parent-pid"
    submitter_script = tmp_path / "submitter.py"
    submitter_script.write_text(
        "import os, pathlib, sys\n"
        "from agency.jobs import JobRequest, submit_job_request\n"
        "config, job_id_file, pid_file = map(pathlib.Path, sys.argv[1:])\n"
        "request = JobRequest(config_path=config, group_key='test', "
        "agent_name='product', trigger='decision', task_input='run')\n"
        "handle = submit_job_request(request)\n"
        "job_id_file.write_text(handle.job_id)\n"
        "pid_file.write_text(str(os.getpid()))\n"
    )
    submitter = subprocess.Popen(
        [sys.executable, str(submitter_script), str(config_path),
         str(job_id_file), str(parent_pid_file)],
        cwd=repository,
    )
    assert submitter.wait(timeout=10) == 0
    submitter_pid = int(parent_pid_file.read_text())
    assert submitter.poll() == 0

    job_id = job_id_file.read_text().strip()
    record_path = JobStore(tmp_path / "memory").path("test", job_id)
    deadline = time.monotonic() + 10
    running = None
    while time.monotonic() < deadline:
        if not record_path.exists():
            time.sleep(0.05)
            continue
        running = _read_job_eventually(record_path, deadline=deadline)
        if running.status not in {"queued", "waiting_for_memory"}:
            break
        time.sleep(0.05)
    assert running is not None
    assert running.status in {"waiting_for_memory", "running"}
    assert running.worker_pid != submitter_pid
    assert not sentinel.exists()

    gate.touch()
    while time.monotonic() < deadline and _read_job_eventually(record_path, deadline=deadline).status == "running":
        time.sleep(0.05)
    record = _read_job_eventually(record_path, deadline=deadline)
    assert sentinel.read_text() == "done"
    assert record.status == "complete"
