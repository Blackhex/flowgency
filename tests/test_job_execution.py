from pathlib import Path
import hashlib
import threading
import time
from dataclasses import replace as dc_replace
from types import SimpleNamespace

import pytest

import os
import subprocess

import yaml

from flowgency.integrations import FileChange, RunResult
from flowgency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest, ResolvedPermissionRule
from flowgency.blueprints.projectors import get_projector
from flowgency.jobs.authority import JobStore
from flowgency.jobs.artifacts import JobArtifact
from flowgency.jobs.execution import execute_job, resolve_job_context
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, PromptSnapshot, RuntimePolicySnapshot
from flowgency.jobs.store import cancel_job
from flowgency.jobs.reconciliation import worker_alive
from flowgency.jobs.store import read_job, write_job
from flowgency.jobs.worker import main as worker_main
from flowgency.memory import MemoryStore
from flowgency.memory.selectors import resolve_memory_selector
from flowgency.configuration.models import MemorySelector
from flowgency.integrations.models import TicketToolLaunch
from flowgency.blueprints.cache import active_pins, pin_artifact
from flowgency.fs.locks import exclusive_lock
from flowgency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX
from tests._ticket_helpers import TicketRuntimeIntegration, make_ticket_job_environment


def _authority(spec: JobSpec):
    store = JobStore(Path(spec.memory.path).parent)
    return store.reference(
        spec.team_key,
        spec.job_id,
        JobRecord.from_spec(spec).authority_digest,
    )


def queued_job(tmp_path: Path, *, decision_context=None, private_prompt_content: str | None = None):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "schema_version: 1\nflowgency:\n  title: Test\n  default_team: ''\n"
        "  ai_backend: copilot\n  agent_library: /nonexistent\n"
        "  compilation_cache: /nonexistent\n  memory_store: /nonexistent\n"
        "  prompt_store: /nonexistent\nmemory: {}\nteams: {}\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / ".compat-cache" / "script" / "v1" / "unresolved"
    runtime_path = cache_path / "runtime"
    runtime_path.mkdir(parents=True, exist_ok=True)
    if private_prompt_content is not None:
        (runtime_path / "AGENTS.md").write_text("# Shared instructions\n", encoding="utf-8")
    else:
        (runtime_path / "agent.md").write_text("run\n", encoding="utf-8")
    resolved = resolve_memory_selector(
        MemorySelector(scope="run"),
        job_id="placeholder",
        team_key="test",
        agent_name="product",
        routine_id=None,
        channels={},
        store_root=tmp_path / ".compat-memory-root",
    )
    team_root = tmp_path / "team"
    workspace_root = tmp_path / "workspace"
    private_prompts: tuple[PromptSnapshot, ...] = ()
    if private_prompt_content is not None:
        private_prompts = (
            PromptSnapshot(
                name="local-triage",
                content=private_prompt_content,
                source_digest=hashlib.sha256(
                    private_prompt_content.encode("utf-8")
                ).hexdigest(),
            ),
        )
    spec = JobSpec(
        schema_version=5,
        job_id="queued-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key="test",
        team_root=str(team_root.resolve()),
        agent_name="product",
        workspace_root=str(workspace_root.resolve()),
        trigger="decision" if decision_context else "manual_prompt",
        integration_name="copilot" if private_prompt_content is not None else "script",
        integration_config={},
        blueprint=BlueprintRef(
            key="compat-unresolved",
            source_digest="compat-unresolved",
            integration="copilot" if private_prompt_content is not None else "script",
            projector_version="2" if private_prompt_content is not None else "v1",
            cache_path=str(cache_path.resolve()),
        ),
        routine_id=None if decision_context else "daily-review",
        skill=None,
        skill_arguments=(),
        task_input="Immutable instructions",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "run"},
            canonical_json=resolved.canonical_json,
            memory_hash=resolved.memory_hash,
            path=str(resolved.directory.resolve()),
        ),
        trigger_context=decision_context,
        prompt_source={"type": "decision" if decision_context else "saved_prompt"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
        private_prompts=private_prompts,
    )
    path = JobStore(tmp_path / ".compat-memory-root").path(spec.team_key, spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    return path, spec


def memory_bound_job(tmp_path: Path):
    team_path = tmp_path / "team"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "schema_version: 1\nflowgency:\n  title: Test\n  default_team: ''\n"
        "  ai_backend: copilot\n  agent_library: /nonexistent\n"
        "  compilation_cache: /nonexistent\n  memory_store: /nonexistent\n"
        "  prompt_store: /nonexistent\nmemory: {}\nteams: {}\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "compiled-agents" / "script" / "v1" / "digest"
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="placeholder",
        team_key="test",
        agent_name="product",
        routine_id="daily-review",
        channels={},
        store_root=tmp_path / "memory-store",
    )
    spec = JobSpec(
        schema_version=5,
        job_id="memory-bound-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key="test",
        team_root=str(team_path.resolve()),
        agent_name="product",
        workspace_root=str(team_path.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={"command": "echo ok"},
        blueprint=BlueprintRef(
            key="builder-blueprint",
            source_digest="digest",
            integration="script",
            projector_version="v1",
            cache_path=str(cache_path.resolve()),
        ),
        routine_id="daily-review",
        skill=None,
        skill_arguments=(),
        task_input="Immutable instructions",
        runtime_policy=RuntimePolicySnapshot(
            timeout=30,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "agent"},
            canonical_json=resolved.canonical_json,
            memory_hash=resolved.memory_hash,
            path=str(resolved.directory.resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "routine", "routine_id": "daily-review"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    path = JobStore(tmp_path / "memory-store").path(spec.team_key, spec.job_id)
    write_job(path, JobRecord.from_spec(spec))
    return path, spec


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "tracked.txt").write_text("line1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")


class MemoryJobFixture:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.job_path, self.spec = memory_bound_job(tmp_path)
        self.authority = _authority(self.spec)
        self.team_root = Path(self.spec.team_root)
        self.memory_root = tmp_path / "memory-store"
        self.store = MemoryStore(self.memory_root)
        self.resolved = resolve_memory_selector(
            MemorySelector(scope="agent"),
            job_id=self.spec.job_id,
            team_key=self.spec.team_key,
            agent_name=self.spec.agent_name,
            routine_id=self.spec.routine_id,
            channels={},
            store_root=self.memory_root,
        )
        seeded = self.store.ensure(self.resolved)
        self.store.try_save(self.resolved, seeded.revision, {"memory.md": b"old"})

    def read(self):
        return read_job(self.job_path)


def test_execute_job_waits_for_memory_before_starting_run(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    seen = {}
    finished = threading.Event()
    held_lock = fixture.store._lock_path(fixture.resolved)

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["started_at"] = read_job(fixture.job_path).started_at
            seen["status"] = read_job(fixture.job_path).status
            finished.set()
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=fixture.team_root,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=fixture.team_root,
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    with exclusive_lock(held_lock, wait=True):
        worker = threading.Thread(target=execute_job, args=(fixture.authority,))
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if fixture.read().status == "waiting_for_memory":
                break
            time.sleep(0.02)
        record = fixture.read()
        assert record.status == "waiting_for_memory"
        assert record.started_at is None
        assert not finished.is_set()

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert seen == {
        "started_at": read_job(fixture.job_path).started_at,
        "status": "running",
    }
    assert read_job(fixture.job_path).status == "complete"


def test_execute_job_cancellation_while_waiting_terminalizes_without_run(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    held_lock = fixture.store._lock_path(fixture.resolved)
    called = {"run": 0}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            called["run"] += 1
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=fixture.team_root,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=fixture.team_root,
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    with exclusive_lock(held_lock, wait=True):
        worker = threading.Thread(target=execute_job, args=(fixture.authority,))
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if fixture.read().status == "waiting_for_memory":
                break
            time.sleep(0.02)
        assert cancel_job(fixture.job_path).status == "cancelled"

    worker.join(timeout=5)
    assert not worker.is_alive()
    record = read_job(fixture.job_path)
    assert record.status == "cancelled"
    assert record.started_at is None
    assert called["run"] == 0


def test_job_execution_has_no_selector_lock_authority():
    import inspect
    import flowgency.jobs.execution as execution

    source = inspect.getsource(execution)
    assert ".selectors" not in source
    assert "_selector_lock_path" not in source


def test_execute_job_failed_run_keeps_canonical_memory_and_retains_stage(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["memory_working_dir"] = request.memory_working_dir
            assert request.memory_working_dir is not None
            Path(request.memory_working_dir, "memory.md").write_text("new", encoding="utf-8")
            return RunResult(1, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=fixture.team_root,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=fixture.team_root,
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(fixture.authority)

    assert result.status == "failed"
    assert fixture.store.read(fixture.resolved).files == {"memory.md": b"old"}
    assert any(
        JobArtifact(**artifact).name == "memory.diff"
        for artifact in (result.memory_publication or {}).get("failed_artifacts", [])
    )
    assert seen["memory_working_dir"]


def test_execute_job_releases_cache_pin_after_terminal_state(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)
    artifact = fixture.spec.blueprint.to_artifact()
    artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(fixture.spec.blueprint.cache_root, artifact.ref, fixture.spec.job_id)

    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=fixture.team_root,
            integration=SimpleNamespace(run=lambda request: RunResult(0, "done", "", 0.1)),
            timeout=30,
            sandbox_root=None,
            team_root=fixture.team_root,
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )

    result = execute_job(fixture.authority)

    assert result.status == "complete"
    assert active_pins(fixture.spec.blueprint.cache_root, artifact.ref) == ()


def test_execute_job_persists_execution_evidence_when_publication_failure_pre_fails_job(
    tmp_path,
    monkeypatch,
):
    _init_repo(tmp_path / "team")
    fixture = MemoryJobFixture(tmp_path)
    artifact = fixture.spec.blueprint.to_artifact()
    artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(
        fixture.spec.blueprint.cache_root,
        artifact.ref,
        fixture.spec.job_id,
    )

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            Path(request.memory_working_dir, "memory.md").write_text(
                "new",
                encoding="utf-8",
            )
            return RunResult(
                0,
                "done",
                "warn",
                1.5,
                [FileChange("a.py", "modified", 2, 1)],
            )

    context = SimpleNamespace(
        workspace_root=fixture.team_root,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=fixture.team_root,
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: context,
    )

    from flowgency.memory.publication import MemoryPublicationError

    def fail_after_task9_terminalization(prepared, **kwargs):
        from flowgency.jobs.store import transition_job

        transition_job(
            fixture.job_path,
            "running",
            "failed",
            completed_at="2026-07-15T12:00:00+00:00",
            execution_summary="Memory publication failed: simulated",
        )
        raise MemoryPublicationError("simulated")

    monkeypatch.setattr(
        "flowgency.jobs.execution.apply_publication",
        fail_after_task9_terminalization,
    )

    result = execute_job(fixture.authority)

    assert result.status == "failed"
    assert result.stdout_path is not None
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "done"
    assert result.stderr_path is not None
    assert Path(result.stderr_path).read_text(encoding="utf-8") == "warn"
    assert result.exit_code == 0
    assert result.duration_seconds == 1.5
    assert result.changed_files == [
        {
            "path": "a.py",
            "status": "modified",
            "lines_added": 2,
            "lines_removed": 1,
        }
    ]
    assert result.base_sha is not None
    assert result.completed_at == "2026-07-15T12:00:00+00:00"
    assert result.memory_publication is not None
    assert result.memory_publication.get("failed_artifacts")
    assert active_pins(fixture.spec.blueprint.cache_root, artifact.ref) == ()


def read_metadata(path: Path) -> dict:
    _, frontmatter, _ = path.read_text().split("---", 2)
    return yaml.safe_load(frontmatter) or {}


def test_execute_job_transitions_writes_logs_and_changes(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    authority = _authority(spec)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["running"] = read_job(path).status
            seen["prompt"] = request.task_file.read_text()
            seen["workspace_root"] = request.workspace_root
            return RunResult(
                0,
                "done",
                "warning",
                1.25,
                [FileChange("a.py", "modified", 2, 1)],
            )

    context = SimpleNamespace(
        workspace_root=tmp_path / "workspace",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=tmp_path / "team",
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    context.workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(authority)

    assert seen == {
        "running": "running",
        "prompt": "Immutable instructions",
        "workspace_root": tmp_path / "workspace",
    }
    assert result.status == "complete"
    assert Path(result.stdout_path).read_text() == "done"
    assert Path(result.stderr_path).read_text() == "warning"
    group_logs = tmp_path / "team" / "logs"
    assert Path(result.stdout_path).is_relative_to(group_logs)
    assert Path(result.stderr_path).is_relative_to(group_logs)
    assert list(group_logs.rglob("*.prompt"))
    assert not (tmp_path / "workspace" / "shared").exists()
    assert result.changed_files == [
        {
            "path": "a.py",
            "status": "modified",
            "lines_added": 2,
            "lines_removed": 1,
        }
    ]
    assert not path.with_suffix(".prompt").exists()
    assert read_job(path) == result


def test_resolve_job_context_uses_frozen_spec_consent_after_config_changes(tmp_path):
    _, base_spec = queued_job(tmp_path, private_prompt_content="---\nname: local-triage\n---\n\nLocal prompt.\n")
    spec = dc_replace(base_spec, agent_name="advisor")
    config_path = Path(spec.config_path)
    config_path.write_text(
        "schema_version: 1\n"
        "teams:\n"
        "  newsletter:\n"
        "    agents:\n"
        "      - name: advisor\n"
        "        integration_config:\n"
        "          allow_local_network: false\n",
        encoding="utf-8",
    )
    frozen_spec = dc_replace(
        spec,
        integration_config={"model": "gpt-5.4", "allow_local_network": True},
        runtime_policy=RuntimePolicySnapshot(
            timeout=60,
            mode="restricted",
            rules=(
                {
                    "path": str(Path(spec.workspace_root)),
                    "tools": ["read", "search"],
                },
            ),
        ),
    )
    request = IntegrationRunRequest(
        workspace_root=Path(frozen_spec.workspace_root),
        launch_dir=tmp_path / "runtime",
        task_file=tmp_path / "task.prompt",
        timeout=60,
        runtime_policy=frozen_spec.runtime_policy.to_effective_policy(),
        ticket_tools=TicketToolLaunch(
            url="http://127.0.0.1:9999/mcp",
            headers={"Authorization": "Bearer fixture-only-token"},
        ),
    )

    context = resolve_job_context(frozen_spec)
    issues = context.integration.validate_run(request)

    assert not any(issue.code == "ticket-local-network-required" for issue in issues)

    updated_context = resolve_job_context(
        dc_replace(
            frozen_spec,
            integration_config={"model": "gpt-5.4", "allow_local_network": False},
        )
    )
    updated_issues = updated_context.integration.validate_run(request)

    assert any(issue.code == "ticket-local-network-required" for issue in updated_issues)


def test_execute_job_v5_spec_carries_no_skill_to_integration(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            seen["skill"] = request.skill
            seen["mode"] = request.runtime_policy.mode
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=tmp_path / "team",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=tmp_path / "team",
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    context.workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert seen == {"skill": None, "mode": "unrestricted"}


def test_v3_job_payload_with_skill_is_rejected_by_from_dict(tmp_path):
    _, spec = queued_job(tmp_path)
    payload = spec.to_dict()
    payload["skill"] = "daily-review"
    with pytest.raises(ValueError, match="durable jobs must not set skill"):
        JobSpec.from_dict(payload)


def test_execute_job_schema_v4_runs_without_selected_skill(tmp_path, monkeypatch):
    path, spec = queued_job(
        tmp_path,
        private_prompt_content=(
            "---\n"
            "name: local-triage\n"
            "description: Local triage.\n"
            "---\n\n"
            "Original private task.\n"
        ),
    )
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"
        projector = get_projector("copilot")

        def run(self, request: IntegrationRunRequest):
            seen["skill"] = request.skill
            seen["mode"] = request.runtime_policy.mode
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=tmp_path / "team",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=tmp_path / "team",
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    context.workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert seen == {"skill": None, "mode": "unrestricted"}


def test_worker_projects_private_prompt_snapshot_without_rereading_source(
    tmp_path,
    monkeypatch,
):
    path, spec = queued_job(
        tmp_path,
        private_prompt_content=(
            "---\n"
            "name: local-triage\n"
            "description: Local triage.\n"
            "---\n\n"
            "Original private task.\n"
        ),
    )
    decoy = (
        tmp_path
        / "prompt-store"
        / "test"
        / "product"
        / "local-triage.prompt.md"
    )
    decoy.parent.mkdir(parents=True)
    decoy.write_text("Changed after submission.\n", encoding="utf-8")
    other = (
        tmp_path
        / "prompt-store"
        / "test"
        / "other-agent"
        / "private-debug.prompt.md"
    )
    other.parent.mkdir(parents=True)
    other.write_text("Other instance prompt\n", encoding="utf-8")

    class Integration:
        supports_execution = True
        name = "fake"
        projector = get_projector("copilot")

        def run(self, request: IntegrationRunRequest):
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=Path(spec.workspace_root),
        team_root=Path(spec.team_root),
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: context,
    )

    record = execute_job(_authority(spec))
    projected = (
        path.with_suffix("")
        / "launch"
        / "instructions"
        / ".github"
        / "prompts"
        / "local-triage.prompt.md"
    )

    assert record.status == "complete"
    payload = projected.read_bytes()
    assert b"Original private task." in payload
    assert b"Changed after submission." not in payload
    assert not (
        path.with_suffix("") / "launch" / "instructions" / ".github" / "prompts" / "private-debug.prompt.md"
    ).exists()


def test_worker_private_prompt_overlay_does_not_mutate_shared_cache_bytes(
    tmp_path,
    monkeypatch,
):
    path, spec = queued_job(
        tmp_path,
        private_prompt_content=(
            "---\n"
            "name: local-triage\n"
            "description: Local triage.\n"
            "---\n\n"
            "Original private task.\n"
        ),
    )
    artifact = spec.blueprint.to_artifact()
    shared_instruction = artifact.runtime_path / "AGENTS.md"
    shared_before = shared_instruction.read_bytes()

    class Integration:
        supports_execution = True
        name = "fake"
        projector = get_projector("copilot")

        def run(self, request: IntegrationRunRequest):
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=Path(spec.workspace_root),
        team_root=Path(spec.team_root),
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: context,
    )

    record = execute_job(_authority(spec))

    assert record.status == "complete"
    assert shared_instruction.read_bytes() == shared_before
    assert not (artifact.runtime_path / ".github").exists()


def test_worker_rejects_private_overlay_collision_with_shared_runtime(
    tmp_path,
    monkeypatch,
):
    path, spec = queued_job(
        tmp_path,
        private_prompt_content=(
            "---\n"
            "name: local-triage\n"
            "description: Local triage.\n"
            "---\n\n"
            "Original private task.\n"
        ),
    )
    artifact = spec.blueprint.to_artifact()
    collision_target = artifact.runtime_path / ".github" / "prompts" / "local-triage.prompt.md"
    collision_target.parent.mkdir(parents=True, exist_ok=True)
    collision_target.write_text("shared prompt\n", encoding="utf-8")
    called = {"run": 0}

    class Integration:
        supports_execution = True
        name = "fake"
        projector = get_projector("copilot")

        def run(self, request: IntegrationRunRequest):
            called["run"] += 1
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=Path(spec.workspace_root),
        team_root=Path(spec.team_root),
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: context,
    )

    record = execute_job(_authority(spec))

    assert called["run"] == 0
    assert record.status == "failed"
    assert "already exists" in (record.execution_summary or "")


def test_execute_job_does_not_create_empty_error_log(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    workspace_root = tmp_path / "team"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=workspace_root,
            integration=SimpleNamespace(
                run=lambda request: RunResult(0, "done", "", 0.1)
            ),
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )

    result = execute_job(_authority(spec))

    assert result.stderr_path is None
    assert not list((tmp_path / "team" / "logs").rglob("*.err"))


def test_execute_job_records_exception_as_failed(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    context = SimpleNamespace(
        workspace_root=tmp_path / "team",
        timeout=30,
        sandbox_root=None,
        team_root=tmp_path / "team",
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
        integration=SimpleNamespace(
            run=lambda request: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(_authority(spec))

    assert result.status == "failed"
    assert "boom" in result.execution_summary
    assert result.completed_at is not None
    assert not path.with_suffix(".prompt").exists()


def test_old_decision_job_cannot_overwrite_current_retry(tmp_path, monkeypatch):
    decisions = tmp_path / "team" / "decisions"
    decisions.mkdir(parents=True)
    decision = decisions / "proposal.md"
    decision.write_text(
        "---\nexecution_job_id: newer-job\nexecution_status: running\n---\n"
    )
    path, spec = queued_job(
        tmp_path,
        decision_context={
            "decision_path": str(decision),
            "proposal_path": "proposal.md",
        },
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=tmp_path / "team",
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
            integration=SimpleNamespace(
                run=lambda request: RunResult(0, "done", "", 0.1)
            ),
        ),
    )

    execute_job(_authority(spec))

    assert read_metadata(decision) == {
        "execution_job_id": "newer-job",
        "execution_status": "running",
    }


def test_execute_job_treats_timeout_exit_code_as_failed(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=tmp_path / "team",
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
            integration=SimpleNamespace(
                run=lambda request: RunResult(124, "partial", "timeout", 30.0)
            ),
        ),
    )

    result = execute_job(_authority(spec))

    assert result.status == "failed"
    assert result.exit_code == 124
    assert result.execution_summary == "Agent timed out after 30 seconds."


def test_execute_job_accepts_result_without_changed_files(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    minimal_result = SimpleNamespace(
        exit_code=0,
        stdout="done",
        stderr="",
        duration_seconds=0.2,
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=tmp_path / "team",
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
            integration=SimpleNamespace(run=lambda request: minimal_result),
        ),
    )

    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert result.changed_files == []


def test_execute_job_retired_decision_trigger_fails_without_touching_decision(tmp_path):
    decisions = tmp_path / "team" / "decisions"
    decisions.mkdir(parents=True)
    decision = decisions / "proposal.md"
    decision.write_text(
        "---\nexecution_job_id: newer-job\nexecution_status: running\n---\n",
        encoding="utf-8",
    )
    before = decision.read_text(encoding="utf-8")
    _, spec = queued_job(
        tmp_path,
        decision_context={
            "decision_path": str(decision),
            "proposal_path": "proposal.md",
        },
    )

    result = execute_job(_authority(spec))

    assert result.status == "failed"
    assert result.execution_summary == (
        "Retired pipeline trigger 'decision' is no longer supported. "
        "Submit work through a ticket workflow instead."
    )
    assert decision.read_text(encoding="utf-8") == before


def test_execute_job_records_live_worker_pid_for_reconciliation(tmp_path, monkeypatch):
    """SystemdRunLauncher reports no PID to the submitter (LaunchResult.worker_pid
    is None). This proves execute_job's own queued->running transition records
    the worker's real, confirmable PID regardless of what the launcher reported,
    so reconciliation always has a usable PID for a running job."""
    path, spec = queued_job(tmp_path)
    assert read_job(path).worker_pid is None

    captured = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            running = read_job(path)
            captured["status"] = running.status
            captured["pid"] = running.worker_pid
            return RunResult(0, "done", "", 0.1)

    context = SimpleNamespace(
        workspace_root=tmp_path / "team" / "product",
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=tmp_path / "team",
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    context.workspace_root.mkdir(parents=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    result = execute_job(_authority(spec))

    assert captured["status"] == "running"
    assert captured["pid"] == os.getpid()
    assert worker_alive(captured["pid"]) is True
    assert result.status == "complete"
    assert result.worker_pid == os.getpid()


def test_worker_returns_status_as_exit_code(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "memory-store")
    authority = store.reference("test", "job", "a" * 64)
    seen = []

    def fake_execute(path):
        seen.append(path)
        return SimpleNamespace(status="complete")

    monkeypatch.setattr("flowgency.jobs.worker.execute_job", fake_execute)
    assert worker_main(authority.worker_args()) == 0

    monkeypatch.setattr(
        "flowgency.jobs.worker.execute_job",
        lambda path: SimpleNamespace(status="failed"),
    )
    assert worker_main(authority.worker_args()) == 1
    assert seen == [authority]


def test_execute_job_persists_session_id_from_successful_run(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    authority = _authority(spec)

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            return RunResult(0, "done", "", 0.1, session_id="sess-success-abc")

    workspace_root = tmp_path / "team"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=workspace_root,
            integration=Integration(),
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )

    execute_job(authority)

    assert read_job(path).session_id == "sess-success-abc"


def test_execute_job_persists_session_id_from_failed_run(tmp_path, monkeypatch):
    fixture = MemoryJobFixture(tmp_path)

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            return RunResult(1, "done", "", 0.1, session_id="sess-fail-xyz")

    context = SimpleNamespace(
        workspace_root=fixture.team_root,
        integration=Integration(),
        timeout=30,
        sandbox_root=None,
        team_root=fixture.team_root,
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
    )
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context", lambda ignored: context
    )

    execute_job(fixture.authority)

    assert read_job(fixture.job_path).session_id == "sess-fail-xyz"


def test_execute_job_workflow_team_run_opens_live_ticket_broker_and_persists_cleanup(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.integrations import RunResult
    from flowgency.integrations import FileChange
    from flowgency.integrations.models import EffectiveRuntimePolicy
    from flowgency.jobs.models import JobRecord, JobRequest
    from flowgency.jobs.processes import ProcessStopEvidence
    from flowgency.jobs.resolution import resolve_job_request
    from flowgency.prompts import PromptStore
    from flowgency.blueprints import CompilationCache
    from flowgency.blueprints.library import BlueprintLibrary
    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    config = env.store.path

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            assert request.ticket_tools is not None
            assert request.ticket_tools.lifecycle is not None
            assert request.ticket_tools.lifecycle.job_id == record.spec.job_id
            client = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
            )
            looked_up = client.call(
                "get_ticket",
                {"ref": ticket.ref.model_dump(mode="json")},
            )
            assert looked_up["ok"] is True
            started = client.call(
                "start_work",
                {
                    "version": ticket.version.model_dump(mode="json"),
                    "operation_id": "start-workflow-team-run",
                },
            )
            assert started["ok"] is True
            live = env.read(ticket.ref).record
            assert live.active_run is not None
            assert live.active_run.job_id == record.spec.job_id
            assert live.active_run.session_id == request.ticket_tools.lifecycle.generation
            Path(request.workspace_root / "changed.txt").write_text("changed\n", encoding="utf-8")
            return RunResult(
                0,
                "done",
                "",
                0.1,
                changed_files=[FileChange("changed.txt", "added", 1, 0)],
                session_id="native-cli-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=request.ticket_tools.lifecycle.generation,
                    confirmed=True,
                    reason="exited",
                ),
            )

    spec = resolve_job_request(
        JobRequest(
            config_path=config,
            team_key=env.team_id,
            agent_name="builder",
            trigger="manual_prompt",
            routine_id=None,
            task_input="Inspect workflow tickets.",
        ),
        config_store=env.store,
        library=BlueprintLibrary(env.store.load().config.flowgency.agent_library),
        cache=CompilationCache(env.store.load().config.flowgency.compilation_cache, {"claude-code": Integration.projector}),
        prompt_store=PromptStore(env.store.load().config.flowgency.prompt_store),
        integrations={"claude-code": Integration()},
    )
    authority = env.job_store.create(JobRecord.from_spec(spec))
    record = env.job_store.read(authority)

    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=Path(spec.workspace_root),
            integration=Integration(),
            timeout=30,
            sandbox_root=None,
            team_root=Path(spec.team_root),
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )

    result = execute_job(authority)

    stored = read_job(authority.path)
    assert result.status == "complete"
    assert stored.session_id == "native-cli-session"
    assert stored.changed_files == [
        {
            "path": "changed.txt",
            "status": "added",
            "lines_added": 1,
            "lines_removed": 0,
        }
    ]
    assert stored.result_metadata is not None
    assert stored.result_metadata["ticket_cleanup"]["status"] == "cleared"
    assert stored.result_metadata["ticket_cleanup"]["generation"]
    assert stored.result_metadata["ticket_cleanup"]["cleared"] == [
        ticket.ref.model_dump(mode="json")
    ]
    assert stored.result_metadata["ticket_cleanup"]["pending_cleanup"] == []
    assert env.read(ticket.ref).record.active_run is None


def _workflow_team_manual_authority(env, integration):
    from flowgency.blueprints import CompilationCache
    from flowgency.blueprints.library import BlueprintLibrary
    from flowgency.jobs.models import JobRecord, JobRequest
    from flowgency.jobs.resolution import resolve_job_request
    from flowgency.prompts import PromptStore

    spec = resolve_job_request(
        JobRequest(
            config_path=env.store.path,
            team_key=env.team_id,
            agent_name="builder",
            trigger="manual_prompt",
            routine_id=None,
            task_input="Inspect workflow tickets.",
        ),
        config_store=env.store,
        library=BlueprintLibrary(env.store.load().config.flowgency.agent_library),
        cache=CompilationCache(
            env.store.load().config.flowgency.compilation_cache,
            {"claude-code": type(integration).projector},
        ),
        prompt_store=PromptStore(env.store.load().config.flowgency.prompt_store),
        integrations={"claude-code": integration},
    )
    authority = env.job_store.create(JobRecord.from_spec(spec))
    return authority, env.job_store.read(authority)


def _patch_workflow_execution_context(monkeypatch, spec, integration):
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=Path(spec.workspace_root),
            integration=integration,
            timeout=30,
            sandbox_root=None,
            team_root=Path(spec.team_root),
            runtime_policy=EffectiveRuntimePolicy(timeout=30),
        ),
    )


def test_execute_ticket_target_job_refreshes_prompt_from_current_ticket_and_workflow_rules(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.integrations import RunResult
    from flowgency.jobs.processes import ProcessStopEvidence

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder", "Original title")
    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    authority = env.jobs.authority_for_handle(handle)

    updated_view = env.read(ticket.ref)
    env.service.update(
        env.user,
        updated_view.version,
        updated_view.patch(
            title="Updated title",
            description="Updated body",
            field_values={"summary": "fresh summary"},
        ),
        env.operation("retitle-before-run"),
    )
    env.publish_criteria_workflow()

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            assert request.ticket_tools is not None
            prompt_text = request.task_file.read_text(encoding="utf-8")
            assert "Updated title" in prompt_text
            assert "Updated body" in prompt_text
            assert "fresh summary" in prompt_text
            assert "Evidence was reviewed" in prompt_text
            assert "Original title" not in prompt_text
            assert "Body text." not in prompt_text
            return RunResult(
                0,
                "done",
                "",
                0.1,
                session_id="native-cli-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=handle.job_id,
                    generation=request.ticket_tools.lifecycle.generation,
                    confirmed=True,
                    reason="exited",
                ),
            )

    _patch_workflow_execution_context(
        monkeypatch,
        env.jobs.read(handle).spec,
        Integration(),
    )

    result = execute_job(authority)

    assert result.status == "complete"


def test_execute_job_workflow_team_retains_active_on_unknown_evidence_and_closes_broker(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.integrations import RunResult
    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    captured = {}

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
            )
            captured["client"] = client
            captured["ref"] = ticket.ref.model_dump(mode="json")
            started = client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-unknown-evidence",
                },
            )
            assert started["ok"] is True
            return RunResult(0, "done", "", 0.1, session_id="native-cli-session")

    integration = Integration()
    authority, record = _workflow_team_manual_authority(env, integration)
    _patch_workflow_execution_context(monkeypatch, record.spec, integration)

    result = execute_job(authority)

    assert result.status == "complete"
    assert env.read(ticket.ref).record.active_run is not None
    stored = read_job(authority.path)
    assert stored.result_metadata["ticket_cleanup"]["status"] == "pending"
    assert stored.result_metadata["ticket_cleanup"]["confirmed"] is False
    failed = captured["client"].call("get_ticket", {"ref": captured["ref"]})
    assert failed["ok"] is False
    assert failed["error"]["code"] == "unavailable"


def test_execute_job_workflow_team_retains_active_on_later_generation(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.integrations import RunResult
    from flowgency.jobs.processes import ProcessStopEvidence
    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
            )
            started = client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-later-generation",
                },
            )
            assert started["ok"] is True
            return RunResult(
                0,
                "done",
                "",
                0.1,
                session_id="native-cli-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=request.ticket_tools.lifecycle.generation + "-later",
                    confirmed=True,
                    reason="exited",
                ),
            )

    integration = Integration()
    authority, record = _workflow_team_manual_authority(env, integration)
    _patch_workflow_execution_context(monkeypatch, record.spec, integration)

    result = execute_job(authority)

    assert result.status == "complete"
    assert env.read(ticket.ref).record.active_run is not None
    stored = read_job(authority.path)
    assert stored.result_metadata["ticket_cleanup"]["status"] == "pending"
    assert stored.result_metadata["ticket_cleanup"]["confirmed"] is True


def test_execute_job_workflow_team_records_pending_cleanup_when_original_storage_is_unavailable(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.integrations import RunResult
    from flowgency.jobs.processes import ProcessStopEvidence
    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    backup = env.tmp_path / "tickets-a-backup"

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
            )
            started = client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-missing-original-root",
                },
            )
            assert started["ok"] is True
            env.root_a.rename(backup)
            return RunResult(
                0,
                "done",
                "",
                0.1,
                session_id="native-cli-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=request.ticket_tools.lifecycle.generation,
                    confirmed=True,
                    reason="exited",
                ),
            )

    integration = Integration()
    authority, record = _workflow_team_manual_authority(env, integration)
    _patch_workflow_execution_context(monkeypatch, record.spec, integration)

    try:
        result = execute_job(authority)
    finally:
        if backup.exists():
            backup.rename(env.root_a)

    assert result.status == "complete"
    assert env.read(ticket.ref).record.active_run is not None
    stored = read_job(authority.path)
    assert stored.result_metadata["ticket_cleanup"]["status"] == "pending"
    assert stored.result_metadata["ticket_cleanup"]["confirmed"] is True


def test_execute_job_workflow_team_runtime_exception_keeps_pending_cleanup_visible(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            client = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                request.ticket_tools.headers["Authorization"].removeprefix("Bearer "),
            )
            started = client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-then-crash",
                },
            )
            assert started["ok"] is True
            raise RuntimeError("boom after broker work")

    integration = Integration()
    authority, record = _workflow_team_manual_authority(env, integration)
    _patch_workflow_execution_context(monkeypatch, record.spec, integration)

    result = execute_job(authority)

    assert result.status == "failed"
    assert "boom after broker work" in (result.execution_summary or "")
    assert env.read(ticket.ref).record.active_run is not None
    stored = read_job(authority.path)
    assert stored.result_metadata["ticket_cleanup"]["status"] == "pending"
    assert stored.result_metadata["ticket_cleanup"]["confirmed"] is False


def test_execute_job_workflow_team_cleanup_exception_persists_sanitized_cleanup_metadata(
    tmp_path,
    raw_config,
    monkeypatch,
):
    import flowgency.jobs.execution as execution_module

    from flowgency.tickets.broker import TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    captured = {}

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            captured["token"] = request.ticket_tools.headers["Authorization"].removeprefix("Bearer ")
            captured["client"] = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                captured["token"],
            )
            started = captured["client"].call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-then-runtime-fails",
                },
            )
            assert started["ok"] is True
            raise RuntimeError("primary boom")

    integration = Integration()
    authority, record = _workflow_team_manual_authority(env, integration)
    _patch_workflow_execution_context(monkeypatch, record.spec, integration)

    real_ticket_runtime = execution_module._ticket_runtime

    def exploding_ticket_runtime(*args, **kwargs):
        runtime = real_ticket_runtime(*args, **kwargs)
        assert runtime is not None

        def explode_cleanup(authority, stopped):
            raise RuntimeError(f"cleanup leaked {captured['token']}")

        runtime.coordinator.cleanup = explode_cleanup
        return runtime

    monkeypatch.setattr(execution_module, "_ticket_runtime", exploding_ticket_runtime)

    result = execute_job(authority)

    assert result.status == "failed"
    assert "primary boom" in (result.execution_summary or "")
    assert "cleanup leaked" not in (result.execution_summary or "")
    assert env.read(ticket.ref).record.active_run is not None
    stored = read_job(authority.path)
    cleanup = stored.result_metadata["ticket_cleanup"]
    assert cleanup["status"] == "error"
    assert cleanup["confirmed"] is False
    assert cleanup["requires_retry"] is True
    assert cleanup["error"]["phase"] == "cleanup"
    assert captured["token"] not in str(cleanup)
    failed = captured["client"].call("get_ticket", {"ref": ticket.ref.model_dump(mode="json")})
    assert failed["ok"] is False
    assert failed["error"]["code"] == "unavailable"


def test_execute_job_workflow_team_broker_close_exception_persists_cleanup_outcome(
    tmp_path,
    raw_config,
    monkeypatch,
):
    import flowgency.jobs.execution as execution_module

    from flowgency.jobs.processes import ProcessStopEvidence
    from flowgency.tickets.broker import TicketBroker, TicketToolClient

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    captured = {}

    class Integration(TicketRuntimeIntegration):
        def run(self, request: IntegrationRunRequest):
            captured["token"] = request.ticket_tools.headers["Authorization"].removeprefix("Bearer ")
            captured["client"] = TicketToolClient(
                request.ticket_tools.url.removesuffix("/mcp"),
                captured["token"],
            )
            started = captured["client"].call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": "start-then-close-fails",
                },
            )
            assert started["ok"] is True
            return RunResult(
                0,
                "done",
                "",
                0.1,
                session_id="native-cli-session",
                process_stop_evidence=ProcessStopEvidence(
                    job_id=record.spec.job_id,
                    generation=request.ticket_tools.lifecycle.generation,
                    confirmed=True,
                    reason="exited",
                ),
            )

    integration = Integration()
    authority, record = _workflow_team_manual_authority(env, integration)
    _patch_workflow_execution_context(monkeypatch, record.spec, integration)

    real_close = TicketBroker.close

    def exploding_close(self):
        real_close(self)
        raise RuntimeError(f"broker close leaked {captured['token']}")

    monkeypatch.setattr(execution_module.TicketBroker, "close", exploding_close)

    result = execute_job(authority)

    assert result.status == "failed"
    assert "broker shutdown failed" in (result.execution_summary or "").lower()
    assert "broker close leaked" not in (result.execution_summary or "")
    assert env.read(ticket.ref).record.active_run is None
    stored = read_job(authority.path)
    cleanup = stored.result_metadata["ticket_cleanup"]
    assert cleanup["status"] == "cleared"
    assert cleanup["confirmed"] is True
    assert cleanup["requires_retry"] is False
    assert cleanup["error"]["phase"] == "broker_close"
    assert captured["token"] not in str(cleanup)
    failed = captured["client"].call("get_ticket", {"ref": ticket.ref.model_dump(mode="json")})
    assert failed["ok"] is False
    assert failed["error"]["code"] == "unavailable"


def test_execute_job_strips_authored_write_on_instructions_zone(tmp_path, monkeypatch):
    """execution.py must call with_launch_zones so an authored write rule on the
    instructions zone cannot reach the integration.  This pins the property at
    the execution level, not just at the EffectiveRuntimePolicy unit level."""
    path, spec = queued_job(tmp_path)
    expected_launch_dir = (Path(path).with_suffix("") / "launch").resolve()

    # Authored rule: grant write on the instructions zone — the property under test.
    authored_policy = EffectiveRuntimePolicy(
        timeout=30,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(
                path=expected_launch_dir / ZONE_INSTRUCTIONS,
                tools=("read", "write"),
            ),
        ),
    )
    captured: dict = {}

    class Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest):
            captured["request"] = request
            return RunResult(0, "done", "", 0.1)

    workspace_root = tmp_path / "team"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=workspace_root,
            integration=Integration(),
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=authored_policy,
        ),
    )
    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert "request" in captured, "Integration.run was not called"
    policy = captured["request"].runtime_policy
    launch_dir = captured["request"].launch_dir

    # The authored write on instructions must be stripped.
    assert policy.tools_for(launch_dir / ZONE_INSTRUCTIONS / "AGENTS.md") == ("read",)
    # Agent-writable zones must remain read+write.
    assert policy.tools_for(launch_dir / ZONE_OUTBOX / "observations") == ("read", "write")
    assert policy.tools_for(launch_dir / ZONE_MEMORY / "memory.md") == ("read", "write")


def test_execute_job_zoned_policy_passes_real_integration_validation(tmp_path, monkeypatch):
    """A real integration's require_valid_run must accept a zoned policy.

    Previous tests substitute fakes that never call require_valid_run; this
    test drives the script integration (which calls self.require_valid_run in
    its run()) to prove generated zone rules do not trigger rejection."""
    from flowgency.integrations.flowgency.script import ScriptIntegration

    path, spec = queued_job(tmp_path)

    authored_policy = EffectiveRuntimePolicy(
        timeout=30,
        mode="unrestricted",
        rules=(
            ResolvedPermissionRule(
                path=tmp_path / "workspace",
                tools=("read",),
            ),
        ),
    )

    integration = ScriptIntegration({"command": "echo ok"})
    captured: dict = {}

    def fake_subprocess_run(*args, **kwargs):
        captured["called"] = True
        return subprocess.CompletedProcess(args[0], 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "flowgency.jobs.execution.resolve_job_context",
        lambda ignored: SimpleNamespace(
            workspace_root=workspace_root,
            integration=integration,
            timeout=30,
            sandbox_root=None,
            team_root=tmp_path / "team",
            runtime_policy=authored_policy,
        ),
    )
    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert captured.get("called"), "subprocess.run was reached — require_valid_run passed"


