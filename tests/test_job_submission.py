from pathlib import Path, PurePosixPath
from copy import deepcopy
import threading
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from agency.blueprints import CompilationCache
from agency.blueprints.library import BlueprintLibrary
from agency.blueprints.projectors import StaticRuntimeProjector
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration
from agency.integrations.models import ProjectorCapabilities, RuntimeCapabilities
import agency.jobs as jobs_package
from agency.jobs import JobSpec, JobSubmissionError, submit_job_request
from agency.jobs.authority import JobStore
from agency.jobs.resolution import JobRequest, JobValidationError, resolve_job_request
from agency.jobs.models import BlueprintRef, MemoryBinding, RuntimePolicySnapshot
from agency.prompts.assets import parse_prompt_document, prompt_source_path
from agency.prompts import build_prompt_task_input
from agency.prompts.catalog import effective_prompt_catalog, resolve_catalog_prompt
from agency.prompts.store import PromptStore, StoredPrompt
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


@pytest.fixture
def prompt_env(tmp_path, raw_config):
    raw = deepcopy(raw_config)
    library_root = Path(raw["agency"]["agent_library"])
    blueprint = library_root / "reviewer"
    prompt_dir = blueprint / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Reviewer\n", encoding="utf-8")
    (prompt_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Review PRs.\n---\n\nReview pull requests.\n",
        encoding="utf-8",
    )
    agent = raw["groups"]["newsletter"]["agents"][0]
    agent["name"] = "reviewer"
    agent["blueprint"] = "reviewer"
    agent["prompts"] = ["local-triage"]
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    store = PromptStore(Path(raw["agency"]["prompt_store"]))
    store.create(
        "newsletter",
        "reviewer",
        "local-triage",
        (
            "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
            "Review local work.\n"
        ).encode("utf-8"),
    )
    snapshot = ConfigStore(config_path).load()
    return SimpleNamespace(
        snapshot=snapshot,
        library=BlueprintLibrary(library_root),
        store=store,
    )


def test_effective_catalog_contains_shared_and_only_registered_private_prompts(prompt_env):
    catalog = effective_prompt_catalog(
        prompt_env.snapshot,
        prompt_env.library,
        prompt_env.store,
        "newsletter",
        "reviewer",
    )

    assert [(item.scope, item.document.name) for item in catalog] == [
        ("blueprint", "pr-review"),
        ("instance", "local-triage"),
    ]
    assert "unregistered" not in {item.document.name for item in catalog}


def test_effective_catalog_ignores_unregistered_private_files(prompt_env):
    prompt_env.store.create(
        "newsletter",
        "reviewer",
        "unregistered",
        (
            "---\nname: unregistered\ndescription: Hidden.\n---\n\n"
            "Ignore this prompt.\n"
        ).encode("utf-8"),
    )

    catalog = effective_prompt_catalog(
        prompt_env.snapshot,
        prompt_env.library,
        prompt_env.store,
        "newsletter",
        "reviewer",
    )

    assert [item.document.name for item in catalog] == ["pr-review", "local-triage"]


def test_effective_catalog_rejects_shared_private_name_collisions(prompt_env):
    prompt_env.store.create(
        "newsletter",
        "reviewer",
        "pr-review",
        (
            "---\nname: pr-review\ndescription: Collision.\n---\n\n"
            "Review local work.\n"
        ).encode("utf-8"),
    )
    prompt_env.snapshot = ConfigStore(prompt_env.snapshot.path).patch(
        prompt_env.snapshot.revision,
        lambda raw: raw["groups"]["newsletter"]["agents"][0].update({"prompts": ["local-triage", "pr-review"]}),
    )

    with pytest.raises(ValueError, match="exists in both blueprint and instance scopes"):
        effective_prompt_catalog(
            prompt_env.snapshot,
            prompt_env.library,
            prompt_env.store,
            "newsletter",
            "reviewer",
        )


def test_resolve_catalog_prompt_requires_matching_explicit_scope(prompt_env):
    prompt = resolve_catalog_prompt(
        prompt_env.snapshot,
        prompt_env.library,
        prompt_env.store,
        "newsletter",
        "reviewer",
        scope="instance",
        name="local-triage",
    )

    assert prompt.scope == "instance"
    assert prompt.document.name == "local-triage"

    with pytest.raises(KeyError):
        resolve_catalog_prompt(
            prompt_env.snapshot,
            prompt_env.library,
            prompt_env.store,
            "newsletter",
            "reviewer",
            scope="instance",
            name="pr-review",
        )


def test_resolve_job_request_reads_each_private_prompt_once_for_snapshot(tmp_path):
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["prompts"] = ["local-triage"]
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    first_payload = (
        b"---\nname: local-triage\ndescription: Local triage.\n---\n\n"
        b"Review local work.\n"
    )
    second_payload = (
        b"---\nname: local-triage\ndescription: Local triage.\n---\n\n"
        b"Review changed local work.\n"
    )
    expected = parse_prompt_document(prompt_source_path("local-triage"), first_payload)

    class ChangingPromptStore:
        def __init__(self) -> None:
            self.read_count = 0

        def read(self, group: str, instance: str, name: str) -> StoredPrompt:
            assert (group, instance, name) == ("newsletter", "builder", "local-triage")
            self.read_count += 1
            payload = first_payload if self.read_count == 1 else second_payload
            return StoredPrompt(
                document=parse_prompt_document(prompt_source_path(name), payload),
                path=tmp_path / "prompts" / f"{name}.prompt.md",
            )

    prompt_store = ChangingPromptStore()

    spec = resolve_job_request(
        JobRequest(
            config_path=config,
            group_key="newsletter",
            agent_name="builder",
            trigger="decision",
            task_input="Decide what changed.",
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
        prompt_store=prompt_store,
        integrations={"copilot": FakeIntegration()},
    )

    assert prompt_store.read_count == 1
    assert len(spec.private_prompts) == 1
    assert spec.private_prompts[0].name == "local-triage"
    assert spec.private_prompts[0].content == expected.source.decode("utf-8")
    assert spec.private_prompts[0].source_digest == expected.digest


def test_resolve_job_request_translates_missing_blueprint_prompt_to_validation_error(tmp_path):
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    config_store = ConfigStore(config)
    library = BlueprintLibrary(tmp_path / "agent-library")
    prompt_store = PromptStore(tmp_path / "prompts")
    snapshot = config_store.load()

    assert resolve_catalog_prompt(
        snapshot,
        library,
        prompt_store,
        "newsletter",
        "builder",
        scope="blueprint",
        name="daily-review",
    ).document.name == "daily-review"

    prompt_path = (
        tmp_path / "agent-library" / "builder-blueprint" / ".agents" / "prompts" / "daily-review.prompt.md"
    )
    prompt_path.unlink()

    with pytest.raises(JobValidationError, match="daily-review"):
        resolve_job_request(
            JobRequest(
                config_path=config,
                group_key="newsletter",
                agent_name="builder",
                trigger="manual_prompt",
                task_input="",
                routine_id="daily-review",
            ),
            config_store=config_store,
            library=library,
            cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
            prompt_store=prompt_store,
            integrations={"copilot": FakeIntegration()},
            snapshot=snapshot,
        )


def test_resolve_job_request_translates_missing_private_prompt_to_validation_error(tmp_path):
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["groups"]["newsletter"]["agents"][0]["prompts"] = ["local-triage"]
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    config_store = ConfigStore(config)
    library = BlueprintLibrary(tmp_path / "agent-library")
    prompt_store = PromptStore(tmp_path / "prompts")
    created = prompt_store.create(
        "newsletter",
        "builder",
        "local-triage",
        (
            "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
            "Review local work.\n"
        ).encode("utf-8"),
    )
    snapshot = config_store.load()

    assert prompt_store.read("newsletter", "builder", "local-triage").document.name == "local-triage"

    prompt_store.delete(
        "newsletter",
        "builder",
        "local-triage",
        expected_digest=created.document.digest,
    )

    with pytest.raises(JobValidationError, match="local-triage"):
        resolve_job_request(
            JobRequest(
                config_path=config,
                group_key="newsletter",
                agent_name="builder",
                trigger="decision",
                task_input="Decide what changed.",
            ),
            config_store=config_store,
            library=library,
            cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
            prompt_store=prompt_store,
            integrations={"copilot": FakeIntegration()},
            snapshot=snapshot,
        )


def test_resolve_job_request_uses_routine_prompt_and_clears_skill_fields(tmp_path):
    library_root = tmp_path / "agent-library"
    blueprint = library_root / "builder-blueprint"
    prompt_dir = blueprint / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review daily work.\n---\n\nRun it.\n",
        encoding="utf-8",
    )
    (tmp_path / "workspaces" / "newsletter" / "repo").mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.yaml"
    config.write_text(
        "schema_version: 4\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_group: newsletter\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "  prompt_store: prompts\n"
        "groups:\n"
        "  newsletter:\n"
        "    name: Newsletter\n"
        "    workspace_path: workspaces/newsletter\n"
        "    path: agents/newsletter\n"
        "    default_integration: copilot\n"
        "    runtime:\n"
        "      timeout: 1800\n"
        "      sandbox:\n"
        "        mode: restricted\n"
        "        roots:\n"
        "          - repo\n"
        "      tools:\n"
        "        mode: allowlist\n"
        "        names:\n"
        "          - shell\n"
        "          - write\n"
        "    agents:\n"
        "      - name: builder\n"
        "        blueprint: builder-blueprint\n"
        "        integration: copilot\n"
        "        integration_config:\n"
        "          command: echo ok\n"
        "        default_memory:\n"
        "          scope: agent\n"
        "        routines:\n"
        "          - id: daily-review\n"
        "            prompt:\n"
        "              scope: blueprint\n"
        "              name: daily-review\n"
        "            arguments:\n"
        "              - --mode=review\n"
        "              - literal value\n"
        "            schedule:\n"
        "              at: '09:00'\n",
        encoding="utf-8",
    )

    spec = resolve_job_request(
        JobRequest(
            config_path=config,
            group_key="newsletter",
            agent_name="builder",
            trigger="manual_prompt",
            routine_id="daily-review",
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(library_root),
        cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
        prompt_store=PromptStore(tmp_path / "prompts"),
        integrations={"copilot": FakeIntegration()},
    )

    assert spec.skill is None
    assert spec.skill_arguments == ()
    assert spec.prompt_source["type"] == "blueprint_prompt"
    assert "Run it." in spec.task_input


def _projector(version: str = "v-test") -> StaticRuntimeProjector:
    return StaticRuntimeProjector(
        version=version,
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("AGENTS.md"),
            skills_target=PurePosixPath(".agents/skills"),
            prompts_target=PurePosixPath(".github/prompts"),
            prompt_format="prompt-markdown",
            discovers_instructions=True,
            discovers_skills=True,
            discovers_prompts=True,
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
            prompts_target=PurePosixPath(".github/prompts"),
            prompt_format="prompt-markdown",
            discovers_instructions=True,
            discovers_skills=True,
            discovers_prompts=True,
            activates_selected_skill=False,
        ),
    )


def _write_blueprint(root: Path, key: str = "builder-blueprint") -> None:
    blueprint = root / key
    prompt_dir = blueprint / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review daily work.\n---\n\nRun it.\n",
        encoding="utf-8",
    )


def _write_config(tmp_path: Path, *, timeout: int = 1800, command: str = "echo ok") -> Path:
    workspace = tmp_path / "workspaces" / "newsletter"
    group = tmp_path / "agents" / "newsletter"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "repo").mkdir(parents=True, exist_ok=True)
    (group / "builder").mkdir(parents=True, exist_ok=True)
    (group / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "agent-library").mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.yaml"
    config.write_text(
        "schema_version: 4\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_group: newsletter\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "  prompt_store: prompts\n"
        "groups:\n"
        "  newsletter:\n"
        "    name: Newsletter\n"
        "    workspace_path: workspaces/newsletter\n"
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
        "            prompt:\n"
        "              scope: blueprint\n"
        "              name: daily-review\n"
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
        task_input="",
        trigger_context={"source": "test"},
    )


def test_submit_persists_then_launches(tmp_path):
    request = configured_request(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job_request(request, launcher)

    record = read_job(handle.path)
    assert record.status == "queued"
    authority = launcher.launch.call_args.args[0]
    assert authority.path == handle.path
    assert authority.group_id == "newsletter"
    assert authority.job_id == handle.job_id
    assert handle.worker_pid == 4321


def test_submit_request_persists_validated_current_snapshot(tmp_path):
    request = configured_request(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job_request(request, launcher)

    record = read_job(handle.path)
    assert record.spec.config_revision not in {"compat-unresolved", "compat-submission-resolved"}
    assert record.spec.workspace_root == str((tmp_path / "workspaces" / "newsletter").resolve())
    assert record.spec.group_root == str((tmp_path / "agents" / "newsletter").resolve())
    assert not hasattr(record.spec, "agent_dir")
    assert not hasattr(record.spec, "workspace_path")
    assert record.spec.runtime_policy.sandbox_roots == (
        str((tmp_path / "workspaces" / "newsletter").resolve()),
        str((tmp_path / "agents" / "newsletter").resolve()),
        str((tmp_path / "workspaces" / "newsletter" / "repo").resolve()),
    )
    assert record.spec.skill is None
    assert record.spec.routine_id == "daily-review"
    assert record.spec.prompt_source["type"] == "blueprint_prompt"


def test_submit_resolves_from_locked_second_snapshot_without_third_load(
    tmp_path,
    monkeypatch,
):
    request = configured_request(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)
    original_load = ConfigStore.load
    load_count = 0

    def counted_load(self, *args, **kwargs):
        nonlocal load_count
        load_count += 1
        return original_load(self, *args, **kwargs)

    monkeypatch.setattr(ConfigStore, "load", counted_load)

    submit_job_request(request, launcher)

    assert load_count == 2


def test_submit_request_with_missing_routine_fails_before_job_write(tmp_path):
    config = _write_config(tmp_path, timeout=1800, command="echo first")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input="",
        routine_id="missing-routine",
    )
    launcher = Mock()

    with pytest.raises(ValueError, match="existing routine"):
        submit_job_request(request, launcher)

    jobs_dir = JobStore(tmp_path / "memory").group_root("newsletter")
    if jobs_dir.exists():
        assert not any(jobs_dir.glob("*.yaml"))
    pins_root = tmp_path / "compiled-agents" / "_pins"
    assert not pins_root.exists()
    assert launcher.launch.call_count == 0


def test_full_run_validation_does_not_require_skill_activation_for_prompt_jobs(
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
        task_input="",
    )
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    with patch.dict("agency.jobs.submission.REGISTRY", {"copilot": NoSkillIntegration()}, clear=True):
        handle = submit_job_request(request, launcher)

    assert handle.worker_pid == 4321
    assert launcher.launch.call_count == 1


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
    (tmp_path / "workspaces" / "other").mkdir(parents=True, exist_ok=True)
    (tmp_path / "agents" / "other").mkdir(parents=True, exist_ok=True)
    config_store.patch(
        snapshot.revision,
        lambda raw: raw["groups"].update(
            {
                "other": {
                    "name": "Other",
                    "workspace_path": str((tmp_path / "workspaces" / "other").resolve()),
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

    def gated_resolve(job_request, locked_snapshot):
        resolve_started.set()
        assert release_resolve.wait(timeout=5)
        return original_resolve(job_request, locked_snapshot)

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
        task_input="",
        routine_id="daily-review",
    )

    spec = resolve_job_request(
        request,
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
        prompt_store=PromptStore(tmp_path / "prompts"),
        integrations={"copilot": FakeIntegration()},
    )

    _write_config(tmp_path, timeout=45, command="echo second")

    assert spec.runtime_policy.timeout == 1800
    assert spec.integration_config == {"command": "echo first"}
    assert spec.blueprint.source_digest
    assert spec.memory.selector["scope"] == "agent"
    assert spec.workspace_root == str((tmp_path / "workspaces" / "newsletter").resolve())
    assert spec.group_root == str((tmp_path / "agents" / "newsletter").resolve())
    assert not hasattr(spec, "agent_dir")
    assert not hasattr(spec, "workspace_path")
    assert spec.runtime_policy.sandbox_roots == (
        str((tmp_path / "workspaces" / "newsletter").resolve()),
        str((tmp_path / "agents" / "newsletter").resolve()),
        str((tmp_path / "workspaces" / "newsletter" / "repo").resolve()),
    )
    assert spec.skill_arguments == ()
    assert spec.task_input == build_prompt_task_input(
        "Run it.\n",
        arguments=("--mode=review", "literal value"),
    )


def test_submit_freezes_routine_arguments_despite_later_config_edit(tmp_path):
    config = _write_config(tmp_path, command="echo first")
    _write_blueprint(tmp_path / "agent-library")
    request = JobRequest(
        config_path=config,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        task_input="",
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
    assert record.spec.skill_arguments == ()
    assert record.spec.task_input == build_prompt_task_input(
        "Run it.\n",
        arguments=("--mode=review", "literal value"),
    )


def test_decision_jobs_keep_empty_skill_arguments(tmp_path):
    spec = queued_decision_like_spec(tmp_path)

    assert spec.routine_id is None
    assert spec.skill is None
    assert spec.skill_arguments == ()


def queued_decision_like_spec(tmp_path: Path) -> JobSpec:
    config = _write_config(tmp_path)
    _write_blueprint(tmp_path / "agent-library")
    return JobSpec(
        schema_version=4,
        job_id="decision-job",
        config_path=str(config.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_root=str((tmp_path / "agents" / "newsletter").resolve()),
        agent_name="builder",
        workspace_root=str((tmp_path / "agents" / "newsletter").resolve()),
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
            memory_hash="memory-hash-archive",
            path=str((tmp_path / "memory" / "memory-hash-archive").resolve()),
        ),
        routine_id=None,
        skill=None,
        skill_arguments=(),
        trigger_context={"decision_path": "decision.md"},
        prompt_source={"type": "decision"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
        private_prompts=(),
    )


def test_resolve_job_request_snapshots_distinct_configured_roots(tmp_path):
    config = _write_config(tmp_path)
    _write_blueprint(tmp_path / "agent-library")

    spec = resolve_job_request(
        JobRequest(
            config_path=config,
            group_key="newsletter",
            agent_name="builder",
            trigger="manual_prompt",
            task_input="",
            routine_id="daily-review",
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
        prompt_store=PromptStore(tmp_path / "prompts"),
        integrations={"copilot": FakeIntegration()},
    )

    assert spec.workspace_root == str((tmp_path / "workspaces" / "newsletter").resolve())
    assert spec.group_root == str((tmp_path / "agents" / "newsletter").resolve())
    assert not hasattr(spec, "agent_dir")
    assert not hasattr(spec, "workspace_path")


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
            task_input="",
            routine_id="daily-review",
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=cache,
        prompt_store=PromptStore(tmp_path / "prompts"),
        integrations={"copilot": FakeIntegration()},
    )
    launcher = Mock()
    launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied"):
        jobs_package.submission._submit_resolved(
            spec,
            JobStore(tmp_path / "memory"),
            launcher,
            config=ConfigStore(config).load().config,
        )

    pins_root = tmp_path / "compiled-agents" / "_pins"
    assert list(pins_root.rglob("*")) == []


def test_windows_launcher_uses_detached_flags(tmp_path):
    authority = JobStore(tmp_path / "memory").reference("newsletter", "job", "a" * 64)
    with patch("agency.jobs.launcher.os.name", "nt"), patch(
        "agency.jobs.launcher.subprocess.Popen"
    ) as popen:
        popen.return_value.pid = 77
        result = DetachedProcessLauncher().launch(authority)
    flags = popen.call_args.kwargs["creationflags"]
    assert flags & DETACHED_PROCESS
    assert flags & CREATE_NEW_PROCESS_GROUP
    assert result.worker_pid == 77


def test_posix_launcher_starts_new_session(tmp_path):
    authority = JobStore(tmp_path / "memory").reference("newsletter", "job", "a" * 64)
    with patch("agency.jobs.launcher.os.name", "posix"), patch(
        "agency.jobs.launcher.subprocess.Popen"
    ) as popen:
        popen.return_value.pid = 78
        DetachedProcessLauncher().launch(authority)
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["shell"] is False


# --- SystemdRunLauncher tests ---


def test_systemd_launcher_argv_and_shell_false(tmp_path):
    """SystemdRunLauncher uses correct systemd-run argv with shell=False."""
    authority = JobStore(tmp_path / "memory").reference("newsletter", "abc-123", "a" * 64)
    with patch("agency.jobs.launcher.subprocess.run") as run_mock:
        result = SystemdRunLauncher().launch(authority)
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
    assert "--store-root" in worker_part
    assert str(authority.store_root) in worker_part
    assert "--job-id" in worker_part
    assert authority.job_id in worker_part
    # shell=False, no stream inheritance
    assert call_args.kwargs["shell"] is False
    assert call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert call_args.kwargs["stdout"] == subprocess.DEVNULL
    assert call_args.kwargs["stderr"] == subprocess.DEVNULL
    # returns None pid (systemd owns the process)
    assert result.worker_pid is None


def test_systemd_launcher_no_stream_inheritance(tmp_path):
    """Streams are explicitly DEVNULL — no stdin/stdout/stderr leak."""
    authority = JobStore(tmp_path / "memory").reference("newsletter", "x", "a" * 64)
    with patch("agency.jobs.launcher.subprocess.run") as run_mock:
        SystemdRunLauncher().launch(authority)
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
    authority = JobStore(tmp_path / "memory").reference("newsletter", "job", "a" * 64)
    with patch("agency.jobs.launcher.subprocess.run"):
        result = SystemdRunLauncher().launch(authority)
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
        task_input="",
    )

    with pytest.raises(
        ValueError,
        match="routine, saved prompt, or nonblank task_input",
    ):
        resolve_job_request(
            request,
            config_store=ConfigStore(config),
            library=BlueprintLibrary(tmp_path / "agent-library"),
            cache=CompilationCache(tmp_path / "compiled-agents", {"copilot": _projector()}),
            prompt_store=PromptStore(tmp_path / "prompts"),
            integrations={"copilot": FakeIntegration()},
        )


# --- Pool-aware submission ---

import os
from dataclasses import replace as dc_replace

from agency.configuration.models import parse_config
from agency.jobs.models import RuntimePolicySnapshot, MemoryBinding, BlueprintRef, JobRecord
from agency.jobs.store import write_job


class _RecordingLauncher:
    def __init__(self):
        self.launched: list[str] = []

    def launch(self, reference):
        self.launched.append(reference.job_id)
        return LaunchResult(worker_pid=os.getpid())


def _pool_config(tmp_path, *, pool=1):
    workspace = tmp_path / "workspaces" / "newsletter"
    group = tmp_path / "agents" / "newsletter"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "repo").mkdir(parents=True, exist_ok=True)
    group.mkdir(parents=True, exist_ok=True)
    memory_store = tmp_path / "memory"

    raw = {
        "schema_version": 4,
        "agency": {
            "title": "Agency",
            "default_group": "newsletter",
            "ai_backend": "claude-code",
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(memory_store),
            "prompt_store": str(tmp_path / "prompts"),
            "jobs": {"pool": pool},
        },
        "groups": {
            "newsletter": {
                "name": "Newsletter",
                "workspace_path": str(workspace),
                "path": str(group),
                "default_integration": "copilot",
                "runtime": {
                    "timeout": 1800,
                    "sandbox": {"mode": "restricted", "roots": ["repo"]},
                    "tools": {"mode": "allowlist", "names": ["shell", "write"]},
                },
                "agents": [
                    {
                        "name": "builder",
                        "blueprint": "builder-blueprint",
                        "integration": "copilot",
                        "integration_config": {"command": "echo ok"},
                        "default_memory": {"scope": "agent"},
                        "routines": [
                            {
                                "id": "daily-review",
                                "prompt": {
                                    "scope": "blueprint",
                                    "name": "daily-review",
                                },
                                "schedule": {"at": "09:00"},
                            }
                        ],
                    }
                ],
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    _write_blueprint(tmp_path / "agent-library")
    return config_path, memory_store, parse_config(raw, config_path)


def _queue_spec(tmp_path, job_id):
    config_path = tmp_path / "config.yaml"
    return JobSpec(
        schema_version=4,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_root=str((tmp_path / "agents" / "newsletter").resolve()),
        agent_name="builder",
        workspace_root=str((tmp_path / "workspaces" / "newsletter").resolve()),
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"command": "echo ok"},
        blueprint=BlueprintRef(
            key="builder-blueprint",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v-test",
            cache_path=str(
                (tmp_path / "compiled-agents" / "copilot" / "v-test" / "digest-1").resolve()
            ),
        ),
        routine_id="daily-review",
        skill=None,
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=(str(tmp_path.resolve()),),
            tool_mode="allowlist",
            tool_names=("shell", "write"),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "placeholder"},
            canonical_json='{"job":"placeholder","scope":"run","version":1}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context={"source": "test"},
        prompt_source={
            "type": "blueprint_prompt",
            "scope": "blueprint",
            "name": "daily-review",
            "source_path": ".agents/prompts/daily-review.prompt.md",
            "source_digest": "digest-1",
        },
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
        private_prompts=(),
    )


class _SubmissionEnv:
    def __init__(self, tmp_path, config_path, memory_store, config):
        self._tmp_path = tmp_path
        self.config_path = config_path
        self.memory_store = memory_store
        self.config = config
        self.launcher = _RecordingLauncher()
        self._store = JobStore(memory_store)

    def request(self, **overrides):
        defaults = dict(
            config_path=self.config_path,
            group_key="newsletter",
            agent_name="builder",
            trigger="manual_prompt",
            task_input="",
            routine_id="daily-review",
        )
        defaults.update(overrides)
        return JobRequest(**defaults)

    def record(self, job_id):
        return read_job(self._store.path("newsletter", job_id))

    def status(self, job_id):
        return self.record(job_id).status

    def fill_pool(self):
        """Write running records with live PIDs to fill the pool."""
        pool_size = self.config.agency.jobs.pool
        for i in range(pool_size):
            spec = _queue_spec(self._tmp_path, f"running-{i}")
            record = dc_replace(
                JobRecord.from_spec(spec),
                status="running",
                worker_pid=os.getpid(),
            )
            write_job(self._store.path("newsletter", spec.job_id), record)

    def fill_pool_with_dead_workers(self):
        """Write running records with dead PIDs to fill the pool."""
        pool_size = self.config.agency.jobs.pool
        for i in range(pool_size):
            spec = _queue_spec(self._tmp_path, f"dead-{i}")
            record = dc_replace(
                JobRecord.from_spec(spec),
                status="running",
                worker_pid=999999,
            )
            write_job(self._store.path("newsletter", spec.job_id), record)


@pytest.fixture
def submission_env(tmp_path):
    config_path, memory_store, config = _pool_config(tmp_path, pool=1)
    return _SubmissionEnv(tmp_path, config_path, memory_store, config)


def test_submission_launches_immediately_when_the_pool_has_room(submission_env):
    handle = submit_job_request(submission_env.request(), submission_env.launcher)
    assert submission_env.launcher.launched == [handle.job_id]


def test_submission_waits_when_the_pool_is_full(submission_env):
    submission_env.fill_pool()
    handle = submit_job_request(submission_env.request(), submission_env.launcher)
    assert submission_env.launcher.launched == []
    assert submission_env.status(handle.job_id) == "queued"


def test_a_waiting_job_records_its_due_time(submission_env):
    handle = submit_job_request(
        submission_env.request(due_at="2026-07-29T08:00:00"),
        submission_env.launcher,
    )
    assert submission_env.record(handle.job_id).due_at == "2026-07-29T08:00:00"


def test_submission_is_refused_when_nothing_can_drain(submission_env, monkeypatch):
    submission_env.fill_pool_with_dead_workers()
    monkeypatch.setattr(
        "agency.jobs.queue.has_drainer", lambda *a, **k: False
    )
    with pytest.raises(JobSubmissionError):
        submit_job_request(submission_env.request(), submission_env.launcher)
