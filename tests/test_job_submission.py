from pathlib import Path, PurePosixPath
import subprocess
from unittest.mock import Mock, patch

import pytest

from agency.blueprints import CompilationCache
from agency.blueprints.library import BlueprintLibrary
from agency.blueprints.projectors import StaticRuntimeProjector
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration
from agency.integrations.models import ProjectorCapabilities, RuntimeCapabilities
from agency.jobs import JobSpec, JobSubmissionError, submit_job
from agency.jobs.resolution import JobRequest, resolve_job_request
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
        "            schedule:\n"
        "              at: '09:00'\n",
        encoding="utf-8",
    )
    return config


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
        integration_name="script",
        integration_config={"command": "echo ok"},
        config_revision="cfg-1",
        blueprint={
            "key": "superseded",
            "source_digest": "digest-1",
            "integration": "script",
            "projector_version": "v1",
            "cache_path": "C:/cache/script/v1/digest-1",
        },
        runtime_policy={
            "timeout": 1800,
            "sandbox_mode": "unrestricted",
            "sandbox_roots": (),
            "tool_mode": "all",
            "tool_names": (),
        },
        memory={
            "selector": {"scope": "run", "version": 1, "job": "placeholder"},
            "canonical_json": '{"job":"placeholder","scope":"run","version":1}',
            "memory_hash": "memory-hash-superseded",
            "path": "C:/memory/memory-hash-superseded",
        },
        routine_id="routine-1",
        skill="superseded",
        skill_arguments=(),
        task_input="Run it",
        trigger_context={"source": "test"},
    )


def _compat_spec_without_resolved_snapshots(tmp_path: Path) -> JobSpec:
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    return JobSpec.create(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"command": "echo ok"},
        config_revision="compat-unresolved",
        blueprint={
            "key": "superseded",
            "source_digest": "digest-1",
            "integration": "copilot",
            "projector_version": "v1",
            "cache_path": "C:/cache/copilot/v1/digest-1",
        },
        runtime_policy={
            "timeout": 1800,
            "sandbox_mode": "restricted",
            "sandbox_roots": (str((tmp_path / "repo").resolve()),),
            "tool_mode": "allowlist",
            "tool_names": ("shell", "write"),
        },
        memory={
            "selector": {"scope": "run", "version": 1, "job": "placeholder"},
            "canonical_json": '{"job":"placeholder","scope":"run","version":1}',
            "memory_hash": "memory-hash-superseded",
            "path": "C:/memory/memory-hash-superseded",
        },
        prompt_source={"type": "saved_prompt", "path": str((tmp_path / "agents" / "newsletter" / "shared" / "prompts" / "daily-review.md"))},
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


def test_submit_compat_spec_persists_validated_canonical_snapshot_without_bypass_marker(tmp_path):
    spec = _compat_spec_without_resolved_snapshots(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job(spec, launcher)

    record = read_job(handle.path)
    assert record.spec.config_revision not in {"compat-unresolved", "compat-submission-resolved"}
    assert record.spec.workspace_dir == str((tmp_path / "agents" / "newsletter").resolve())
    assert record.spec.agent_dir == record.spec.workspace_dir
    assert record.spec.skill == "daily-review"
    assert record.spec.routine_id == "daily-review"


def test_submit_marks_record_failed_when_launch_fails(tmp_path):
    spec = configured_spec(tmp_path)
    launcher = Mock()
    launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied") as error:
        submit_job(spec, launcher)

    record = read_job(error.value.job_path)
    assert record.status == "failed"
    assert "spawn denied" in record.execution_summary


def test_resolve_job_request_snapshots_runtime_authority_at_submission(tmp_path):
    config = _write_config(tmp_path, timeout=1800, command="echo first")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input="Run it",
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
    assert spec.agent_dir == spec.workspace_dir


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
    assert spec.agent_dir == spec.workspace_dir


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
        submit_job(spec, launcher)

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
