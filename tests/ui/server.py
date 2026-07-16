from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

import yaml

from agency.configuration.models import MemorySelector
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import transition_job, write_job
from agency.memory import MemoryStore, resolve_memory_selector


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PARENT = Path(__file__).resolve().parent / ".runtime"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.yaml"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _replace_runtime(value: object, runtime: Path) -> object:
    if isinstance(value, str):
        return value.replace("__RUNTIME__", runtime.as_posix())
    if isinstance(value, list):
        return [_replace_runtime(item, runtime) for item in value]
    if isinstance(value, dict):
        return {key: _replace_runtime(item, runtime) for key, item in value.items()}
    return value


def _seed_blueprint(library: Path, key: str, title: str, skill: str) -> None:
    _write(library / key / "AGENTS.md", f"# {title}\n\nDeterministic release-gate instructions.\n")
    _write(
        library / key / ".agents" / "skills" / skill / "SKILL.md",
        f"---\nname: {skill}\ndescription: Release gate skill\n---\n\nRun the deterministic workflow.\n",
    )
    _write(library / key / ".agents" / "skills" / skill / "checklist.md", "- Verify content\n")


def _seed_pipeline(group: Path) -> None:
    for directory in ("jobs", "logs/2026-07-16", "observations", "proposals", "decisions", "pipeline"):
        (group / "shared" / directory).mkdir(parents=True, exist_ok=True)
    _write(group / "shared" / "memory.md", "# Newsletter shared memory\n")
    _write(
        group / "shared" / "observations" / "audience-signal.md",
        "---\nagent: advisor\nstatus: open\ndate: 2026-07-16T09:00:00+00:00\nfloat: true\n---\n\n# Audience signal\n\nReaders want shorter releases.\n",
    )
    _write(
        group / "shared" / "proposals" / "weekly-brief.md",
        "---\norigin_agent: advisor\nstatus: proposed\ndate: 2026-07-16T10:00:00+00:00\nquestions:\n  - Approve the weekly brief?\n---\n\n# Weekly brief\n\nPublish a concise weekly brief.\n",
    )
    _write(
        group / "shared" / "decisions" / "approve-brief.md",
        "---\ndecided_by: editor\ndate: 2026-07-16T11:00:00+00:00\nanswers:\n  approve: approved\n---\n\n# Approve brief\n",
    )


def _seed_group_scaffold(group: Path) -> None:
    for directory in ("jobs", "logs/2026-07-16", "observations", "proposals", "decisions", "pipeline"):
        (group / "shared" / directory).mkdir(parents=True, exist_ok=True)
    _write(group / "shared" / "memory.md", f"# {group.name.title()} shared memory\n")


def _job_spec(runtime: Path, config_path: Path, job_id: str) -> JobSpec:
    group = runtime / "groups" / "newsletter"
    return JobSpec(
        schema_version=2,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="ui-gate-revision",
        group_key="newsletter",
        group_path=str(group.resolve()),
        agent_name="advisor",
        workspace_dir=str(group.resolve()),
        trigger="scheduled_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="1" * 64,
            integration="copilot",
            projector_version="v1",
            cache_path=str((runtime / "compiled-agents" / "copilot" / "v1" / ("1" * 64)).resolve()),
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=("--brief",),
        task_input="# Daily review\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1200,
            sandbox_mode="restricted",
            sandbox_roots=(str((group / "shared").resolve()), str((group / "editorial").resolve())),
            tool_mode="allowlist",
            tool_names=("shell",),
        ),
        memory=MemoryBinding(
            selector={"scope": "channel", "channel": "brand-strategy"},
            canonical_json='{"channel":"brand-strategy","scope":"channel"}',
            memory_hash="2" * 64,
            path=str((runtime / "memory-store" / "channel-brand-strategy").resolve()),
        ),
        trigger_context={"source": "ui-gate"},
        prompt_source={"type": "routine", "routine_id": "daily-review", "title": "Daily review"},
        timeout_override=None,
        created_at="2026-07-16T12:00:00+00:00",
    )


def _seed_jobs(runtime: Path, config_path: Path) -> None:
    jobs = runtime / "groups" / "newsletter" / "shared" / "jobs"
    waiting_path = jobs / "job-waiting.yaml"
    write_job(waiting_path, JobRecord.from_spec(_job_spec(runtime, config_path, "job-waiting")))
    transition_job(waiting_path, "queued", "waiting_for_memory")

    failed_path = jobs / "job-failed.yaml"
    failed = JobRecord.from_spec(_job_spec(runtime, config_path, "job-failed"))
    failed.status = "failed"
    failed.changed_files = [{"path": "docs/newsletter.md", "status": "modified", "lines_added": 4, "lines_removed": 1}]
    failed.execution_summary = "Memory publication failed after the draft was retained."
    artifact = jobs / "artifacts" / "job-failed" / "memory.md"
    _write(artifact, "# Retained draft memory\n")
    failed.memory_publication = {
        "failed_artifacts": [{"name": "memory.md", "path": str(artifact.resolve()), "size": artifact.stat().st_size}]
    }
    failed.stdout_path = str((runtime / "groups" / "newsletter" / "shared" / "logs" / "2026-07-16" / "advisor-job-failed.out").resolve())
    failed.stderr_path = str((runtime / "groups" / "newsletter" / "shared" / "logs" / "2026-07-16" / "advisor-job-failed.err").resolve())
    _write(Path(failed.stdout_path), "deterministic stdout\n")
    _write(Path(failed.stderr_path), "deterministic stderr\n")
    write_job(failed_path, failed)


def _seed_memory(runtime: Path, config: dict) -> None:
    store = MemoryStore(runtime / "memory-store")
    channel = resolve_memory_selector(
        MemorySelector(scope="channel", channel="brand-strategy"),
        job_id="ui-preview",
        group_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=config["memory"]["channels"],
        store_root=store.root,
    )
    snapshot = store.ensure(channel)
    store.try_save(channel, snapshot.revision, {"memory.md": b"# Brand Strategy\n\nPrefer concise, evidence-led releases.\n"})


def _prepare_runtime() -> tuple[Path, Path]:
    RUNTIME_PARENT.mkdir(parents=True, exist_ok=True)
    for stale in RUNTIME_PARENT.iterdir():
        if stale.is_dir() and time.time() - stale.stat().st_mtime > 24 * 60 * 60:
            shutil.rmtree(stale, ignore_errors=True)
    runtime = RUNTIME_PARENT / f"run-{os.getpid()}-{uuid4().hex[:8]}"
    runtime.mkdir()
    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    config_path = runtime / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    group = runtime / "groups" / "newsletter"
    _seed_pipeline(group)
    _seed_group_scaffold(runtime / "groups" / "research")
    _seed_blueprint(runtime / "agent-library", "advisor", "Advisor", "daily-review")
    _seed_blueprint(runtime / "agent-library", "builder", "Builder", "publish-draft")
    (runtime / "compiled-agents").mkdir()
    _seed_memory(runtime, config)
    _seed_jobs(runtime, config_path)
    (runtime / "server.pid").write_text(str(os.getpid()), encoding="ascii")
    return runtime, config_path


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_ready(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    url = f"http://127.0.0.1:{port}/newsletter/"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Agency server exited with status {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Agency server was not ready at {url}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not _port_is_free(args.port):
        raise RuntimeError(f"Test port {args.port} is already in use; refusing to reuse an unknown server")

    runtime, config_path = _prepare_runtime()
    env = os.environ.copy()
    env["AGENCY_CONFIG"] = str(config_path)
    env["PYTHONPATH"] = str(ROOT)
    command = [
        sys.executable,
        "-m",
        "agency.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
    ]
    process = subprocess.Popen(command, cwd=ROOT, env=env)

    def stop(_signum: int | None = None, _frame: object | None = None) -> None:
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        _wait_ready(args.port, process)
        return process.wait()
    finally:
        stop()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())