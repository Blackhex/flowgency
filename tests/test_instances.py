from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading
from uuid import uuid4

import pytest
import yaml

from agency.blueprints.library import BlueprintLibrary
from agency.configuration.models import MemorySelector
from agency.configuration.store import ConfigConflictError, ConfigStore
from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobRequest,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)
from agency.jobs.resolution import JobValidationError
from agency.jobs.store import job_path, transition_job, write_job
from agency.jobs.submission import submit_job_request
from agency.memory import MemoryStore, resolve_memory_selector


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _write_skill(
    path: Path,
    name: str,
    description: str = "Review daily editorial work.",
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        (
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            "Run the review.\n"
        ),
        encoding="utf-8",
    )


def _write_blueprint(root: Path, key: str) -> None:
    blueprint = root / key
    blueprint.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {key}\n", encoding="utf-8")
    _write_skill(
        blueprint / ".agents" / "skills" / "daily-review",
        "daily-review",
    )


def _resolved_memory(
    memory_root: Path,
    selector: MemorySelector,
    *,
    group_key: str,
    agent_name: str,
    routine_id: str | None = None,
) -> object:
    return resolve_memory_selector(
        selector,
        job_id="preview-job",
        group_key=group_key,
        agent_name=agent_name,
        routine_id=routine_id,
        channels={"support": {"display_name": "Support"}},
        store_root=memory_root,
    )


def _make_spec(
    group_path: Path,
    *,
    agent_name: str,
    group_key: str,
) -> JobSpec:
    config_path = group_path.parent / "config.yaml"
    return JobSpec(
        schema_version=2,
        job_id=uuid4().hex,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key=group_key,
        group_path=str(group_path.resolve()),
        agent_name=agent_name,
        workspace_dir=str(group_path.resolve()),
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path="C:/cache/copilot/v1/digest-1",
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=("--fast",),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=(str(group_path.resolve()),),
            tool_mode="allowlist",
            tool_names=("shell", "write"),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "placeholder"},
            canonical_json='{"job":"placeholder","scope":"run","version":1}',
            memory_hash="a" * 64,
            path="C:/memory/placeholder",
        ),
        trigger_context={"source": "test"},
        prompt_source={"type": "routine", "routine_id": "daily-review"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )


@pytest.fixture
def instance_env(tmp_path, canonical_raw_config):
    library_root = tmp_path / "agent-library"
    _write_blueprint(library_root, "builder-blueprint")
    _write_blueprint(library_root, "advisor")

    newsletter_path = tmp_path / "groups" / "newsletter"
    other_path = tmp_path / "groups" / "other"
    for group_path in (newsletter_path, other_path):
        (group_path / "shared" / "jobs").mkdir(parents=True, exist_ok=True)
        (group_path / "shared" / "prompts").mkdir(parents=True, exist_ok=True)
        (group_path / "shared" / "memory.md").write_text(
            "# Shared Memory\n",
            encoding="utf-8",
        )

    raw = deepcopy(canonical_raw_config)
    raw["agency"]["agent_library"] = str(library_root)
    raw["agency"]["memory_store"] = str(tmp_path / "memory-store")
    raw["agency"]["compilation_cache"] = str(tmp_path / "compiled-agents")
    raw["groups"]["newsletter"]["path"] = str(newsletter_path)
    agent = raw["groups"]["newsletter"]["agents"][0]
    agent["default_memory"] = {"scope": "agent"}
    agent["routines"] = [
        {
            "id": "daily-review",
            "skill": "daily-review",
            "schedule": {"at": "09:00"},
            "memory": {"scope": "routine"},
        },
        {
            "id": "group-sync",
            "skill": "daily-review",
            "schedule": {"every": "6h"},
            "memory": {"scope": "group"},
        },
        {
            "id": "announcements",
            "skill": "daily-review",
            "schedule": {"every": "12h"},
            "memory": {"scope": "channel", "channel": "support"},
        },
    ]
    raw["groups"]["other"] = {
        "name": "Other",
        "path": str(other_path),
        "default_integration": "copilot",
        "agents": [],
    }

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    return {
        "config_store": ConfigStore(config_path),
        "library": BlueprintLibrary(library_root),
        "memory_store": MemoryStore(tmp_path / "memory-store"),
        "newsletter_path": newsletter_path,
        "other_path": other_path,
        "memory_root": tmp_path / "memory-store",
    }


@pytest.fixture
def instance_service(instance_env):
    from agency.instances import InstanceService

    return InstanceService(
        config_store=instance_env["config_store"],
        library=instance_env["library"],
        memory_store=instance_env["memory_store"],
    )


def test_create_instance_pins_group_and_validates_blueprint_and_integration(
    instance_service,
):
    from agency.configuration.issues import ValidationFailed
    from agency.fs.snapshot import AssetValidationError
    from agency.instances import AgentInstanceCreate

    result = instance_service.create(
        "newsletter",
        AgentInstanceCreate(
            name="advisor",
            blueprint="advisor",
            integration="copilot",
            display_name="Advisor",
        ),
    )

    assert result.instance.name == "advisor"
    assert result.instance.blueprint == "advisor"
    assert result.instance.integration == "copilot"
    assert (
        result.snapshot.config.groups["newsletter"].agents["advisor"].name
        == "advisor"
    )
    assert "advisor" not in result.snapshot.config.groups["other"].agents

    with pytest.raises(AssetValidationError):
        instance_service.create(
            "newsletter",
            AgentInstanceCreate(
                name="missing-blueprint",
                blueprint="does-not-exist",
                integration="copilot",
                display_name="Missing",
            ),
        )

    with pytest.raises(ValidationFailed):
        instance_service.create(
            "newsletter",
            AgentInstanceCreate(
                name="sdk-agent",
                blueprint="advisor",
                integration="sdk",
                display_name="SDK",
            ),
        )


def test_remove_instance_patches_config_only_and_reports_orphaned_memories(
    instance_service,
    instance_env,
):
    agent_memory = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="agent"),
        group_key="newsletter",
        agent_name="builder",
    )
    routine_memory = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="routine"),
        group_key="newsletter",
        agent_name="builder",
        routine_id="daily-review",
    )
    group_memory = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="group"),
        group_key="newsletter",
        agent_name="builder",
    )
    channel_memory = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="channel", channel="support"),
        group_key="newsletter",
        agent_name="builder",
    )
    instance_env["memory_store"].ensure(agent_memory)
    instance_env["memory_store"].ensure(routine_memory)
    instance_env["memory_store"].ensure(group_memory)
    instance_env["memory_store"].ensure(channel_memory)

    result = instance_service.remove("newsletter", "builder")

    assert "builder" not in result.snapshot.config.groups["newsletter"].agents
    assert agent_memory.directory.exists()
    assert routine_memory.directory.exists()
    assert group_memory.directory.exists()
    assert channel_memory.directory.exists()
    assert {item.memory_hash for item in result.orphaned_memories} == {
        agent_memory.memory_hash,
        routine_memory.memory_hash,
    }


def test_preview_move_reports_only_agent_and_routine_memories(
    instance_service,
    instance_env,
):
    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "copy",
    )

    assert preview.blocked_by == ()
    assert {item.selector.scope for item in preview.source_memories} == {
        "agent",
        "routine",
    }
    assert {item.selector.scope for item in preview.destination_memories} == {
        "agent",
        "routine",
    }
    assert {item.canonical_json for item in preview.source_memories} == {
        '{"agent":"builder","group":"newsletter","scope":"agent","version":1}',
        (
            '{"agent":"builder","group":"newsletter","routine":'
            '"daily-review","scope":"routine","version":1}'
        ),
    }
    assert {item.canonical_json for item in preview.destination_memories} == {
        '{"agent":"builder","group":"other","scope":"agent","version":1}',
        (
            '{"agent":"builder","group":"other","routine":'
            '"daily-review","scope":"routine","version":1}'
        ),
    }


def test_move_refuses_existing_destination_memory(
    instance_service,
    instance_env,
):
    target = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="agent"),
        group_key="other",
        agent_name="builder",
    )
    instance_env["memory_store"].ensure(target)

    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "copy",
    )

    assert preview.blocked_by == ("destination-memory-exists",)

    from agency.instances import InstanceMoveConflict

    with pytest.raises(InstanceMoveConflict):
        instance_service.move(preview)


@pytest.mark.parametrize("status", ["queued", "waiting_for_memory", "running"])
@pytest.mark.parametrize("group_key", ["newsletter", "other"])
def test_move_blocks_when_relevant_jobs_are_active(
    instance_service,
    instance_env,
    status,
    group_key,
):
    group_path = (
        instance_env["newsletter_path"]
        if group_key == "newsletter"
        else instance_env["other_path"]
    )
    spec = _make_spec(group_path, agent_name="builder", group_key=group_key)
    record_path = job_path(group_path, spec.job_id)
    write_job(record_path, JobRecord.from_spec(spec))
    if status != "queued":
        transition_job(record_path, "queued", status)

    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "copy",
    )

    assert preview.blocked_by == ("active-jobs",)


def test_move_copy_mode_copies_exact_snapshot_and_leaves_source_memory(
    instance_service,
    instance_env,
):
    agent_memory = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="agent"),
        group_key="newsletter",
        agent_name="builder",
    )
    routine_memory = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="routine"),
        group_key="newsletter",
        agent_name="builder",
        routine_id="daily-review",
    )
    seeded_agent = instance_env["memory_store"].ensure(agent_memory)
    seeded_routine = instance_env["memory_store"].ensure(routine_memory)
    instance_env["memory_store"].try_save(
        agent_memory,
        seeded_agent.revision,
        {"memory.md": b"agent\n", "notes.md": b"keep\n"},
    )
    instance_env["memory_store"].try_save(
        routine_memory,
        seeded_routine.revision,
        {"memory.md": b"routine\n", "context.md": b"same\n"},
    )

    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "copy",
    )
    snapshot = instance_service.move(preview)

    moved = snapshot.config.groups["other"].agents["builder"]
    assert moved.default_memory == MemorySelector(scope="agent")
    assert moved.routines[0].memory == MemorySelector(scope="routine")
    assert "builder" not in snapshot.config.groups["newsletter"].agents

    target_agent = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="agent"),
        group_key="other",
        agent_name="builder",
    )
    target_routine = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="routine"),
        group_key="other",
        agent_name="builder",
        routine_id="daily-review",
    )
    assert instance_env["memory_store"].read(target_agent).files == {
        "memory.md": b"agent\n",
        "notes.md": b"keep\n",
    }
    assert instance_env["memory_store"].read(target_routine).files == {
        "memory.md": b"routine\n",
        "context.md": b"same\n",
    }
    assert instance_env["memory_store"].read(agent_memory).files == {
        "memory.md": b"agent\n",
        "notes.md": b"keep\n",
    }
    assert instance_env["memory_store"].read(routine_memory).files == {
        "memory.md": b"routine\n",
        "context.md": b"same\n",
    }


def test_move_empty_mode_seeds_memory_md_only(instance_service, instance_env):
    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "empty",
    )
    snapshot = instance_service.move(preview)

    assert "builder" in snapshot.config.groups["other"].agents
    target_agent = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="agent"),
        group_key="other",
        agent_name="builder",
    )
    target_routine = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="routine"),
        group_key="other",
        agent_name="builder",
        routine_id="daily-review",
    )
    assert instance_env["memory_store"].read(target_agent).files == {
        "memory.md": b""
    }
    assert instance_env["memory_store"].read(target_routine).files == {
        "memory.md": b""
    }


def test_move_revalidates_revision_and_rolls_back_new_target_memory(
    instance_service,
    instance_env,
):
    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "copy",
    )

    snapshot = instance_env["config_store"].load()
    instance_env["config_store"].patch(
        snapshot.revision,
        lambda raw: raw["agency"].update({"title": "Changed"}),
    )

    with pytest.raises(ConfigConflictError):
        instance_service.move(preview)

    target_agent = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="agent"),
        group_key="other",
        agent_name="builder",
    )
    target_routine = _resolved_memory(
        instance_env["memory_root"],
        MemorySelector(scope="routine"),
        group_key="other",
        agent_name="builder",
        routine_id="daily-review",
    )
    assert not target_agent.directory.exists()
    assert not target_routine.directory.exists()


def test_move_holds_group_lock_and_concurrent_submit_re_resolves_after_move(
    instance_service,
    instance_env,
    monkeypatch,
):
    import agency.jobs.submission as submission

    preview = instance_service.preview_move(
        "newsletter",
        "builder",
        "other",
        "copy",
    )
    move_checked = threading.Event()
    release_move = threading.Event()
    original_patch = instance_env["config_store"].patch
    submit_outcome: dict[str, object] = {}
    move_outcome: dict[str, object] = {}

    def patched_patch(expected_revision, patcher):
        move_checked.set()
        assert release_move.wait(timeout=5)
        return original_patch(expected_revision, patcher)

    instance_env["config_store"].patch = patched_patch

    request = JobRequest(
        config_path=instance_env["config_store"].path,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        routine_id="daily-review",
        task_input="Run it",
        trigger_context={"source": "test"},
    )

    def move_agent() -> None:
        try:
            move_outcome["snapshot"] = instance_service.move(preview)
        except Exception as exc:  # pragma: no cover - asserted below
            move_outcome["error"] = exc

    def submit_job() -> None:
        try:
            submit_outcome["handle"] = submit_job_request(request)
        except Exception as exc:  # pragma: no cover - asserted below
            submit_outcome["error"] = exc

    resolve_called = threading.Event()
    original_resolve = submission._resolve_request

    def record_resolve(job_request):
        resolve_called.set()
        return original_resolve(job_request)

    monkeypatch.setattr(submission, "_resolve_request", record_resolve)

    move_thread = threading.Thread(target=move_agent)
    move_thread.start()
    assert move_checked.wait(timeout=5)
    submit_thread = threading.Thread(target=submit_job)
    submit_thread.start()
    assert submit_thread.is_alive()
    assert not resolve_called.wait(timeout=0.2)
    release_move.set()
    move_thread.join(timeout=5)
    submit_thread.join(timeout=5)

    assert "snapshot" in move_outcome
    assert isinstance(submit_outcome.get("error"), JobValidationError)
    assert "Unknown agent: builder" in str(submit_outcome["error"])
    source_jobs_dir = instance_env["newsletter_path"] / "shared" / "jobs"
    assert not source_jobs_dir.exists() or not any(
        source_jobs_dir.glob("*.yaml")
    )


def test_opposite_direction_move_lock_order_avoids_deadlock(instance_env):
    from agency.instances import InstanceService
    from agency.instances import AgentInstanceCreate

    _write_blueprint(instance_env["library"].root, "advisor-two")
    service = InstanceService(
        config_store=instance_env["config_store"],
        library=instance_env["library"],
        memory_store=instance_env["memory_store"],
    )
    service.create(
        "other",
        AgentInstanceCreate(
            name="advisor",
            blueprint="advisor-two",
            integration="copilot",
            display_name="Advisor",
        ),
    )

    preview_a = service.preview_move("newsletter", "builder", "other", "copy")
    preview_b = service.preview_move("other", "advisor", "newsletter", "copy")
    outcomes: list[object] = []

    def run_move(preview):
        try:
            outcomes.append(service.move(preview))
        except Exception as exc:  # pragma: no cover - asserted below
            outcomes.append(exc)

    first = threading.Thread(target=run_move, args=(preview_a,))
    second = threading.Thread(target=run_move, args=(preview_b,))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(outcomes) == 2
    assert any(not isinstance(item, Exception) for item in outcomes)


def test_remove_uses_group_lock_to_block_new_submissions(
    instance_service,
    instance_env,
    monkeypatch,
):
    import agency.jobs.submission as submission

    patch_started = threading.Event()
    release_patch = threading.Event()
    original_patch = instance_env["config_store"].patch
    request = JobRequest(
        config_path=instance_env["config_store"].path,
        group_key="newsletter",
        agent_name="builder",
        trigger="manual_prompt",
        routine_id="daily-review",
        task_input="Run it",
        trigger_context={"source": "test"},
    )
    submit_outcome: dict[str, object] = {}
    remove_outcome: dict[str, object] = {}

    def patched_patch(expected_revision, patcher):
        patch_started.set()
        assert release_patch.wait(timeout=5)
        return original_patch(expected_revision, patcher)

    instance_env["config_store"].patch = patched_patch

    def remove_agent() -> None:
        try:
            remove_outcome["result"] = instance_service.remove(
                "newsletter",
                "builder",
            )
        except Exception as exc:  # pragma: no cover - asserted below
            remove_outcome["error"] = exc

    def submit_job() -> None:
        try:
            submit_outcome["handle"] = submit_job_request(request)
        except Exception as exc:  # pragma: no cover - asserted below
            submit_outcome["error"] = exc

    resolve_called = threading.Event()
    original_resolve = submission._resolve_request

    def record_resolve(job_request):
        resolve_called.set()
        return original_resolve(job_request)

    monkeypatch.setattr(submission, "_resolve_request", record_resolve)

    remove_thread = threading.Thread(target=remove_agent)
    remove_thread.start()
    assert patch_started.wait(timeout=5)
    submit_thread = threading.Thread(target=submit_job)
    submit_thread.start()
    assert submit_thread.is_alive()
    assert not resolve_called.wait(timeout=0.2)
    release_patch.set()
    remove_thread.join(timeout=5)
    submit_thread.join(timeout=5)

    assert "result" in remove_outcome
    assert isinstance(submit_outcome.get("error"), JobValidationError)
    assert "Unknown agent: builder" in str(submit_outcome["error"])
