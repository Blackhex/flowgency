from pathlib import Path

import pytest

from agency.configuration.models import MemorySelector
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import read_job, write_job
from agency.memory import MemoryStore, resolve_memory_selector
from agency.memory.publication import apply_publication, prepare_publication
from agency.memory.recovery import recover_publications


class RecoveryFixture:
    def __init__(self, tmp_path: Path):
        self.group_path = tmp_path / "group"
        self.group_path.mkdir(parents=True)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("groups: {}\n", encoding="utf-8")
        spec = JobSpec.create(
            config_path=config_path,
            group_key="news",
            agent_name="writer",
            trigger="manual_prompt",
            prompt_source={
                "type": "saved_prompt",
                "path": "shared/prompts/routine.md",
            },
            prompt_content="Publish memory",
        )
        self.job_path = (
            self.group_path / "shared" / "jobs" / f"{spec.job_id}.yaml"
        )
        write_job(self.job_path, JobRecord.from_spec(spec))
        write_job(self.job_path, JobRecord(spec=spec, status="running"))
        self.store_root = tmp_path / "memory-store"
        self.store = MemoryStore(self.store_root)
        self.resolved = resolve_memory_selector(
            MemorySelector(scope="agent"),
            job_id=spec.job_id,
            group_key="news",
            agent_name="writer",
            routine_id=None,
            channels={},
            store_root=self.store_root,
        )
        seeded = self.store.ensure(self.resolved)
        self.store.try_save(
            self.resolved,
            seeded.revision,
            {"memory.md": b"old\n", "notes.md": b"stable\n"},
        )
        self.stage = self.store.stage(self.resolved, job_id=spec.job_id)
        (self.stage.directory / "memory.md").write_bytes(b"new\n")
        self.job_store = self.group_path / "shared" / "jobs"
        self.canonical_is_new = False

    def crash_at(self, phase: str) -> None:
        prepared = prepare_publication(self.stage, job_path=self.job_path)
        try:
            apply_publication(prepared, crash_at=phase)
        except Exception:
            pass
        self.canonical_is_new = (
            self.store.read(self.resolved).files["memory.md"] == b"new\n"
        )

    def read_job(self):
        return read_job(self.job_path)


@pytest.fixture
def recovery_fixture(tmp_path):
    return RecoveryFixture(tmp_path)


@pytest.mark.parametrize("phase", ["prepared", "backed_up", "published"])
def test_recovery_resolves_job_and_memory_consistently(
    recovery_fixture,
    phase,
):
    recovery_fixture.crash_at(phase)

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.job_store,
    )
    record = recovery_fixture.read_job()

    assert result.recovered == 1
    if recovery_fixture.canonical_is_new:
        assert record.status == "complete"
        assert record.memory_publication is not None
    else:
        assert record.status == "failed"


def test_recovery_is_noop_without_publication_journals(tmp_path):
    assert (
        recover_publications(
            tmp_path / "missing-store",
            tmp_path / "jobs",
        ).recovered
        == 0
    )
