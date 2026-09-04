from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import yaml

from agency.configuration.models import MemorySelector
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import transition_job, write_job
from agency.memory import MemoryStore, resolve_memory_selector
from agency.prompts import PromptStore


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PARENT = Path(__file__).resolve().parent / ".runtime"
RUNTIME_ROOT = RUNTIME_PARENT / "current"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.yaml"
FIXED_NOW = "2026-07-16T12:00:00+00:00"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _set_mtime(path: Path, value: str) -> None:
    timestamp = datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))


def _replace_runtime(value: object, runtime: Path) -> object:
    if isinstance(value, str):
        return value.replace("__RUNTIME__", runtime.as_posix())
    if isinstance(value, list):
        return [_replace_runtime(item, runtime) for item in value]
    if isinstance(value, dict):
        return {key: _replace_runtime(item, runtime) for key, item in value.items()}
    return value


def _prompt_bytes(name: str, description: str, body: str, *, argument_hint: str | None = None) -> bytes:
    metadata = [f"name: {name}", f"description: {description}"]
    if argument_hint is not None:
        metadata.append(f"argument-hint: {argument_hint}")
    return ("---\n" + "\n".join(metadata) + f"\n---\n\n{body.rstrip()}\n").encode("utf-8")


def _seed_blueprint(
    library: Path,
    key: str,
    title: str,
    skill: str,
    *,
    prompts: tuple[tuple[str, str, str, str | None], ...],
) -> None:
    _write(library / key / "AGENTS.md", f"# {title}\n\nDeterministic release-gate instructions.\n")
    _write(
        library / key / ".agents" / "skills" / skill / "SKILL.md",
        f"---\nname: {skill}\ndescription: Release gate skill\n---\n\nRun the deterministic workflow.\n",
    )
    _write(library / key / ".agents" / "skills" / skill / "checklist.md", "- Verify content\n")
    prompt_root = library / key / ".agents" / "prompts"
    for name, description, body, argument_hint in prompts:
        prompt_root.mkdir(parents=True, exist_ok=True)
        (prompt_root / f"{name}.prompt.md").write_bytes(
            _prompt_bytes(name, description, body, argument_hint=argument_hint)
        )


def _seed_pipeline(team: Path) -> None:
    for directory in ("logs/2026-07-16", "observations", "proposals", "decisions", "locks"):
        (team / directory).mkdir(parents=True, exist_ok=True)
    _write(
        team / "observations" / "audience-signal.md",
        "---\nagent: advisor\nstatus: open\ndate: 2026-07-16T09:00:00+00:00\nfloat: true\n---\n\n# Audience signal\n\nReaders want shorter releases.\n",
    )
    _write(
        team / "proposals" / "weekly-brief.md",
        "---\norigin_agent: advisor\nstatus: proposed\ndate: 2026-07-16T10:00:00+00:00\nquestions:\n  - Approve the weekly brief?\n---\n\n# Weekly brief\n\nPublish a concise weekly brief.\n",
    )
    _write(
        team / "decisions" / "approve-brief.md",
        "---\ndecided_by: editor\ndate: 2026-07-16T11:00:00+00:00\nanswers:\n  approve: approved\n---\n\n# Approve brief\n",
    )


def _seed_team_scaffold(team: Path) -> None:
    for directory in ("logs/2026-07-16", "observations", "proposals", "decisions", "locks"):
        (team / directory).mkdir(parents=True, exist_ok=True)


def _job_spec(runtime: Path, config_path: Path, job_id: str) -> JobSpec:
    team = runtime / "teams" / "newsletter"
    workspace = runtime / "workspaces" / "newsletter"
    return JobSpec(
        schema_version=5,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="ui-gate-revision",
        team_key="newsletter",
        team_root=str(team.resolve()),
        agent_name="advisor",
        workspace_root=str(workspace.resolve()),
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
        skill=None,
        skill_arguments=(),
        task_input="# Daily review\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1200,
            mode="restricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "channel", "channel": "brand-strategy"},
            canonical_json='{"channel":"brand-strategy","scope":"channel"}',
            memory_hash="2" * 64,
            path=str((runtime / "memory-store" / "channel-brand-strategy").resolve()),
        ),
        trigger_context={"source": "ui-gate"},
        prompt_source={
            "type": "blueprint_prompt",
            "scope": "blueprint",
            "name": "daily-review",
            "source_path": ".agents/prompts/daily-review.prompt.md",
            "source_digest": "1" * 64,
            "title": "Daily review",
        },
        timeout_override=None,
        created_at="2026-07-16T12:00:00+00:00",
        private_prompts=(),
    )


def _seed_jobs(runtime: Path, config_path: Path) -> None:
    authority = JobStore(runtime / "memory-store")
    authority.team_root("newsletter").mkdir(parents=True, exist_ok=True)
    waiting_path = authority.path("newsletter", "job-waiting")
    write_job(waiting_path, JobRecord.from_spec(_job_spec(runtime, config_path, "job-waiting")))
    transition_job(waiting_path, "queued", "waiting_for_memory")

    failed_path = authority.path("newsletter", "job-failed")
    failed = JobRecord.from_spec(_job_spec(runtime, config_path, "job-failed"))
    failed.status = "failed"
    failed.changed_files = [{"path": "docs/newsletter.md", "status": "modified", "lines_added": 4, "lines_removed": 1}]
    failed.execution_summary = "Memory publication failed after the draft was retained."
    artifact = authority.artifact_root("newsletter", "job-failed") / "memory.md"
    _write(artifact, "# Retained draft memory\n")
    failed.memory_publication = {
        "failed_artifacts": [{"name": "memory.md", "path": str(artifact.resolve()), "size": artifact.stat().st_size}]
    }
    failed.stdout_path = str((runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / "advisor-job-failed.out").resolve())
    failed.stderr_path = str((runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / "advisor-job-failed.err").resolve())
    _write(Path(failed.stdout_path), "deterministic stdout\n")
    _write(Path(failed.stderr_path), "deterministic stderr\n")
    _set_mtime(Path(failed.stdout_path), "2026-07-16T11:30:00+00:00")
    _set_mtime(Path(failed.stderr_path), "2026-07-16T11:30:00+00:00")
    write_job(failed_path, failed)


def _seed_private_prompts(runtime: Path) -> None:
    PromptStore(runtime / "prompts").create(
        "newsletter",
        "advisor",
        "local-triage",
        _prompt_bytes(
            "local-triage",
            "Private local triage.",
            "Audit the current release blockers and call out anything that needs a human decision.\n",
            argument_hint="Escalate blockers if the draft is stale.",
        ),
    )


def _seed_memory(runtime: Path, config: dict) -> None:
    store = MemoryStore(runtime / "memory-store")
    store.root.mkdir(parents=True, exist_ok=True)
    channel = resolve_memory_selector(
        MemorySelector(scope="channel", channel="brand-strategy"),
        job_id="ui-preview",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=config["memory"]["channels"],
        store_root=store.root,
    )
    store.ensure(channel)
    _write(channel.directory / "memory.md", "# Brand Strategy\n\nPrefer concise, evidence-led releases.\n")


def _safe_remove_runtime(runtime: Path) -> None:
    parent = RUNTIME_PARENT.resolve(strict=False)
    candidate = runtime.resolve(strict=False)
    if candidate.parent != parent or candidate.name != "current":
        raise RuntimeError(f"Refusing to remove unsafe UI runtime path: {candidate}")
    shutil.rmtree(candidate, ignore_errors=True)


def _prepare_runtime() -> tuple[Path, Path]:
    runtime = RUNTIME_ROOT
    RUNTIME_PARENT.mkdir(parents=True, exist_ok=True)
    _safe_remove_runtime(runtime)
    runtime.mkdir()
    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    config_path = runtime / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    team = runtime / "teams" / "newsletter"
    (runtime / "teams" / "newsletter" / "editorial").mkdir(parents=True, exist_ok=True)
    (runtime / "workspaces" / "newsletter").mkdir(parents=True, exist_ok=True)
    (runtime / "workspaces" / "research").mkdir(parents=True, exist_ok=True)
    _seed_pipeline(team)
    _seed_team_scaffold(runtime / "teams" / "research")
    _seed_blueprint(
        runtime / "agent-library",
        "advisor",
        "Advisor",
        "daily-review",
        prompts=(
            (
                "daily-review",
                "Shared daily review.",
                "Review the current release plan and summarize the next decision.\n",
                "Mention the release window if relevant.",
            ),
            (
                "release-window",
                "Shared release window check.",
                "Verify the release window, rollout risk, and communication timing.\n",
                "Include the launch date and any blocked approvals.",
            ),
        ),
    )
    _seed_blueprint(
        runtime / "agent-library",
        "builder",
        "Builder",
        "publish-draft",
        prompts=(
            (
                "publish-draft",
                "Shared publish draft.",
                "Prepare the current draft for publication and flag any unresolved edits.\n",
                "Note whether publishing is blocked by review.",
            ),
        ),
    )
    (runtime / "compiled-agents").mkdir()
    _seed_private_prompts(runtime)
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

    runtime = RUNTIME_ROOT
    process: subprocess.Popen[bytes] | None = None

    def stop(_signum: int | None = None, _frame: object | None = None) -> None:
        if process is not None and process.poll() is None:
            process.terminate()

    try:
        runtime, config_path = _prepare_runtime()
        env = os.environ.copy()
        env["AGENCY_CONFIG"] = str(config_path)
        env["AGENCY_FIXED_NOW"] = FIXED_NOW
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
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        _wait_ready(args.port, process)
        return process.wait()
    finally:
        stop()
        if process is not None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        _safe_remove_runtime(runtime)


if __name__ == "__main__":
    raise SystemExit(main())