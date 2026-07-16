from pathlib import Path, PurePosixPath
import threading
import subprocess
from unittest.mock import Mock, patch

import pytest

from agency.blueprints import CompilationCache
from agency.blueprints.library import BlueprintLibrary
from agency.blueprints.projectors import StaticRuntimeProjector
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration
from agency.integrations.models import ProjectorCapabilities, RuntimeCapabilities
import agency.jobs as jobs_package
from agency.jobs import JobSpec, JobSubmissionError, submit_job_request
from agency.jobs.prompts import build_routine_task_input
from agency.jobs.resolution import JobRequest, resolve_job_request
from agency.jobs.models import BlueprintRef, MemoryBinding, RuntimePolicySnapshot
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
from agency.memory import MemoryStore


def _projector(version: str = "v-test") -> StaticRuntimeProjector:
    return StaticRuntimeProjector(
        version=version,
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("AGENTS.md"),
            skills_target=PurePosixPath(".agents/skills"),
            discovers_skills=True,
            activates_selected_skill=True,
        ),
    )


class FakeIntegration(BaseIntegration):
    name = "copilot"
    display_name = "Copilot"
    supports_execution = True
    projector = _projector()
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"allowlist", "all"}),
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


class NoSkillIntegration(FakeIntegration):
    projector = StaticRuntimeProjector(
        version="v-no-skill",
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("AGENTS.md"),
            skills_target=PurePosixPath(".agents/skills"),
            discovers_skills=True,
            activates_selected_skill=False,
        ),
    )


def _write_blueprint(root: Path, key: str = "builder-blueprint") -> None:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review daily work.\n---\n\nRun it.\n",
        encoding="utf-8",
    )


def _write_config(tmp_path: Path, *, timeout: int = 1800, command: str = "echo ok") -> Path:
    group = tmp_path / "agents" / "newsletter"
    (group / "builder").mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.yaml"
    config.write_text(
        "schema_version: 2\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_group: newsletter\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "groups:\n"
        "  newsletter:\n"
        "    name: Newsletter\n"
        "    path: agents/newsletter\n"
        "    default_integration: copilot\n"
        f"    runtime:\n      timeout: {timeout}\n"
        "      sandbox:\n        mode: restricted\n        roots:\n          - repo\n"
        "      tools:\n        mode: allowlist\n        names:\n          - shell\n          - write\n"
        "    agents:\n"
        "      - name: builder\n"
        "        blueprint: builder-blueprint\n"
        "        integration: copilot\n"
        "        integration_config:\n"
        f"          command: {command}\n"
        "        default_memory:\n          scope: agent\n"
        "        routines:\n"
        "          - id: daily-review\n"
        "            skill: daily-review\n"
        "            arguments:\n"
        "              - --mode=review\n"
        "              - literal value\n"
        "            schedule:\n"
        "              at: '09:00'\n",
        encoding="utf-8",
    )
    return config


def configured_request(tmp_path: Path) -> JobRequest:
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    return JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        routine_id="daily-review",
        task_input="Run it",
        trigger_context={"source": "test"},
    )


def test_submit_persists_then_launches(tmp_path):
    request = configured_request(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job_request(request, launcher)

    record = read_job(handle.path)
    assert record.status == "queued"
    assert launcher.launch.call_args.args == (handle.path,)
    assert handle.worker_pid == 4321


def test_submit_request_persists_validated_canonical_snapshot(tmp_path):
    request = configured_request(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job_request(request, launcher)

    record = read_job(handle.path)
    assert record.spec.config_revision not in {"compat-unresolved", "compat-submission-resolved"}
    assert record.spec.workspace_dir == str((tmp_path / "agents" / "newsletter").resolve())
    assert record.spec.agent_dir == record.spec.workspace_path
    assert record.spec.skill == "daily-review"
    assert record.spec.routine_id == "daily-review"


def test_submit_request_with_missing_routine_fails_before_job_write(tmp_path):
    config = _write_config(tmp_path, timeout=1800, command="echo first")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input="Run it",
        routine_id="missing-routine",
    )
    launcher = Mock()

    with pytest.raises(ValueError, match="existing routine"):
        submit_job_request(request, launcher)

    jobs_dir = tmp_path / "agents" / "newsletter" / "shared" / "jobs"
    if jobs_dir.exists():
        assert not any(jobs_dir.glob("*.yaml"))
    pins_root = tmp_path / "compiled-agents" / "_pins"
    assert not pins_root.exists()
    assert launcher.launch.call_count == 0


def test_full_run_validation_rejects_unsupported_skill_before_pin_or_job(
    tmp_path,
):
    config = _write_config(tmp_path)
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        routine_id="daily-review",
        task_input="Run it",
    )
    launcher = Mock()

    with patch.dict("agency.jobs.submission.REGISTRY", {"copilot": NoSkillIntegration()}, clear=True):
        with pytest.raises(Exception, match="activate routine skills|unsupported-skill"):
            submit_job_request(request, launcher)

    jobs_dir = tmp_path / "agents" / "newsletter" / "shared" / "jobs"
    assert not jobs_dir.exists() or not any(jobs_dir.glob("*.yaml"))
    pins_root = tmp_path / "compiled-agents" / "_pins"
    assert not pins_root.exists() or not any(pins_root.rglob("*"))
    assert launcher.launch.call_count == 0


def test_submit_marks_record_failed_when_launch_fails(tmp_path):
    request = configured_request(tmp_path)
    launcher = Mock()
    launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied") as error:
        submit_job_request(request, launcher)

    record = read_job(error.value.job_path)
    assert record.status == "failed"
    assert "spawn denied" in record.execution_summary


def test_submit_blocks_move_and_move_then_observes_active_job(
    tmp_path,
    monkeypatch,
):
    from agency.instances import InstanceService, InstanceMoveConflict
    import agency.jobs.submission as submission

    request = configured_request(tmp_path)
    config_store = ConfigStore(request.config_path)
    snapshot = config_store.load()
    config_store.patch(
        snapshot.revision,
        lambda raw: raw["groups"].update(
            {
                "other": {
                    "name": "Other",
                    "path": str((tmp_path / "agents" / "other").resolve()),
                    "default_integration": "copilot",
                    "agents": [],
                }
            }
        ),
    )
    launcher = Mock()
    launch_started = threading.Event()
    release_launch = threading.Event()

    def hold_launch(job_file: Path):
        launch_started.set()
        assert release_launch.wait(timeout=5)
        return LaunchResult(worker_pid=4321)

    launcher.launch.side_effect = hold_launch
    service = InstanceService(
        config_store=config_store,
        library=BlueprintLibrary(tmp_path / "agent-library"),
        memory_store=MemoryStore(tmp_path / "memory"),
    )
    submit_outcome: dict[str, object] = {}
    move_outcome: dict[str, object] = {}

    preview = service.preview_move("newsletter", "builder", "other", "copy")
    assert preview.blocked_by == ()

    resolve_started = threading.Event()
    release_resolve = threading.Event()
    original_resolve = submission._resolve_request

    def gated_resolve(job_request):
        resolve_started.set()
        assert release_resolve.wait(timeout=5)
        return original_resolve(job_request)

    monkeypatch.setattr(submission, "_resolve_request", gated_resolve)

    def submit_job() -> None:
        try:
            submit_outcome["handle"] = submit_job_request(request, launcher)
        except Exception as exc:  # pragma: no cover - asserted below
            submit_outcome["error"] = exc

    submit_thread = threading.Thread(target=submit_job)
    submit_thread.start()
    assert resolve_started.wait(timeout=5)

    def move_agent() -> None:
        try:
            move_outcome["snapshot"] = service.move(preview)
        except Exception as exc:  # pragma: no cover - asserted below
            move_outcome["error"] = exc

    move_thread = threading.Thread(target=move_agent)
    move_thread.start()
    move_thread.join(timeout=0.2)
    assert move_thread.is_alive()
    release_resolve.set()
    assert launch_started.wait(timeout=5)
    release_launch.set()
    submit_thread.join(timeout=5)
    move_thread.join(timeout=5)

    assert isinstance(submit_outcome.get("handle"), jobs_package.JobHandle)
    assert isinstance(move_outcome.get("error"), InstanceMoveConflict)
    assert move_outcome["error"].reasons == ("active-jobs",)


def test_resolve_job_request_snapshots_runtime_authority_at_submission(tmp_path):
    config = _write_config(tmp_path, timeout=1800, command="echo first")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input=build_routine_task_input("daily-review", ("--mode=review", "literal value")),
        routine_id="daily-review",
    )

    spec = resolve_job_request(
        request,
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
        integrations={"copilot": FakeIntegration()},
    )

    _write_config(tmp_path, timeout=45, command="echo second")

    assert spec.runtime_policy.timeout == 1800
    assert spec.integration_config == {"command": "echo first"}
    assert spec.blueprint.source_digest
    assert spec.memory.selector["scope"] == "agent"
    assert spec.workspace_dir == str((tmp_path / "agents" / "newsletter").resolve())
    assert spec.agent_dir == spec.workspace_path
    assert spec.skill_arguments == ("--mode=review", "literal value")
    assert spec.task_input == "Run routine 'daily-review' with arguments: --mode=review, literal value."


def test_submit_freezes_routine_arguments_despite_later_config_edit(tmp_path):
    config = _write_config(tmp_path, command="echo first")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input="Run routine 'daily-review' with arguments: --mode=review, literal value",
        routine_id="daily-review",
    )

    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job_request(request, launcher)

    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "              - literal value\n",
            "              - changed later\n",
        ),
        encoding="utf-8",
    )

    record = read_job(handle.path)
    assert record.spec.skill_arguments == ("--mode=review", "literal value")
    assert record.spec.task_input == "Run routine 'daily-review' with arguments: --mode=review, literal value"


def test_decision_jobs_keep_empty_skill_arguments(tmp_path):
    spec = queued_decision_like_spec(tmp_path)

    assert spec.routine_id is None
    assert spec.skill is None
    assert spec.skill_arguments == ()


def queued_decision_like_spec(tmp_path: Path) -> JobSpec:
    config = _write_config(tmp_path)
    _write_blueprint(tmp_path / "agent-library")
    return JobSpec(
        schema_version=2,
        job_id="decision-job",
        config_path=str(config.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_path=str((tmp_path / "agents" / "newsletter").resolve()),
        agent_name="builder",
        workspace_dir=str((tmp_path / "agents" / "newsletter").resolve()),
        trigger="decision",
        integration_name="copilot",
        integration_config={"command": "echo ok"},
        blueprint=BlueprintRef(
            key="builder-blueprint",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v-test",
            cache_path=str((tmp_path / "compiled-agents" / "copilot" / "v-test" / "digest-1" / "entry.py").resolve()),
        ),
        task_input="Immutable decision instructions",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=(str((tmp_path / "repo").resolve()),),
            tool_mode="allowlist",
            tool_names=("shell", "write"),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "placeholder"},
            canonical_json='{"job":"placeholder","scope":"run","version":1}',
            memory_hash="memory-hash-superseded",
            path=str((tmp_path / "memory" / "memory-hash-superseded").resolve()),
        ),
        routine_id=None,
        skill=None,
        skill_arguments=(),
        trigger_context={"decision_path": "decision.md"},
        prompt_source={"type": "decision"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )


def test_resolve_job_request_snapshots_workspace_dir_despite_external_agent_path_inputs(tmp_path):
    config = _write_config(tmp_path)
    _write_blueprint(tmp_path / "agent-library")

    spec = resolve_job_request(
        JobRequest(
            config_path=config,
            group_key="newsletter",
            agent_name="builder",
            trigger="manual_prompt",
            task_input="Run it",
            routine_id="daily-review",
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
        integrations={"copilot": FakeIntegration()},
    )

    assert spec.workspace_dir == str((tmp_path / "agents" / "newsletter").resolve())
    assert spec.agent_dir == spec.workspace_path


def test_submit_releases_cache_pin_when_launch_fails(tmp_path):
    config = _write_config(tmp_path)
    _write_blueprint(tmp_path / "agent-library")
    cache = CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()})
    spec = resolve_job_request(
        JobRequest(
            config_path=config,
            group_key="newsletter",
            agent_name="builder",
            trigger="manual_prompt",
            task_input="Run it",
            routine_id="daily-review",
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=cache,
        integrations={"copilot": FakeIntegration()},
    )
    launcher = Mock()
    launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied"):
        jobs_package.submission._submit_resolved(spec, launcher)

    pins_root = tmp_path / "compiled-agents" / "_pins"
    assert list(pins_root.rglob("*")) == []


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


# --- submit_job_request uses default_launcher ---


def test_submit_job_uses_default_launcher_when_none_provided(tmp_path):
    """submit_job_request with no explicit launcher uses default_launcher factory."""
    request = configured_request(tmp_path)
    fake_launcher = Mock()
    fake_launcher.launch.return_value = LaunchResult(worker_pid=999)
    with patch("agency.jobs.submission.default_launcher", return_value=fake_launcher):
        handle = submit_job_request(request)
    assert fake_launcher.launch.called
    assert handle.worker_pid == 999


def test_jobs_package_no_longer_exports_submit_job():
    assert not hasattr(jobs_package, "submit_job")


def test_resolution_does_not_infer_routine_or_skill_from_prompt_source_path(tmp_path):
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input="Run it",
    )

    with pytest.raises(ValueError, match="existing routine"):
        resolve_job_request(
            request,
            config_store=ConfigStore(config),
            library=BlueprintLibrary(tmp_path / "agent-library"),
            cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
            integrations={"copilot": FakeIntegration()},
        )
