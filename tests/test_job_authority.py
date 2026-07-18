from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agency.jobs.authority import JobAuthorityError, JobStore
from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)


def _spec(tmp_path: Path) -> JobSpec:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    memory_root = tmp_path / "memory"
    return JobSpec(
        schema_version=2,
        job_id="authority-job",
        config_path=str((tmp_path / "config.yaml").resolve()),
        config_revision="cfg-1",
        group_key="test",
        group_path=str(workspace.resolve()),
        agent_name="builder",
        workspace_dir=str(workspace.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={"command": "echo safe"},
        blueprint=BlueprintRef(
            key="builder",
            source_digest="source-digest",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "cache" / "script" / "v1" / "source-digest").resolve()),
        ),
        routine_id="daily",
        skill="daily",
        skill_arguments=(),
        task_input="Run safely",
        runtime_policy=RuntimePolicySnapshot(
            timeout=30,
            sandbox_mode="restricted",
            sandbox_roots=(str(workspace.resolve()),),
            tool_mode="allowlist",
            tool_names=("shell",),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent"},
            canonical_json='{"agent":"builder","group":"test","scope":"agent","version":1}',
            memory_hash="a" * 64,
            path=str((memory_root / ("a" * 64)).resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "routine", "routine_id": "daily"},
        timeout_override=None,
        created_at="2026-07-17T00:00:00+00:00",
    )


def test_job_store_is_external_and_canonical(tmp_path):
    memory_root = tmp_path / "memory"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JobStore(memory_root)

    reference = store.create(JobRecord.from_spec(_spec(tmp_path)))

    assert reference.path == (memory_root / ".jobs" / "test" / "authority-job.yaml").resolve()
    assert workspace.resolve() not in reference.path.parents
    with pytest.raises(ValueError):
        store.path("../escape", "authority-job")
    with pytest.raises(ValueError):
        store.path("test", "../escape")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda spec: spec["integration_config"].update({"command": "echo hostile"}),
        lambda spec: spec.update({"workspace_dir": "C:/hostile"}),
        lambda spec: spec["blueprint"].update({"cache_path": "C:/hostile-cache"}),
        lambda spec: spec["runtime_policy"].update({"sandbox_roots": ["C:/hostile-root"], "tool_names": ["all"]}),
        lambda spec: spec["memory"].update({"path": "C:/hostile-memory", "memory_hash": "b" * 64}),
    ],
)
def test_authority_reference_rejects_tampered_immutable_spec(tmp_path, mutate):
    store = JobStore(tmp_path / "memory")
    reference = store.create(JobRecord.from_spec(_spec(tmp_path)))
    payload = yaml.safe_load(reference.path.read_text(encoding="utf-8"))
    mutate(payload["spec"])
    reference.path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(JobAuthorityError, match="immutable job authority"):
        store.read(reference)


def test_authority_digest_does_not_change_for_lifecycle_updates(tmp_path):
    store = JobStore(tmp_path / "memory")
    reference = store.create(JobRecord.from_spec(_spec(tmp_path)))

    store.write(reference, replace(store.read(reference), status="waiting_for_memory"))

    assert store.read(reference).status == "waiting_for_memory"
    assert store.reference("test", "authority-job", reference.immutable_digest) == reference