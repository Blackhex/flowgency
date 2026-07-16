from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from agency.configuration.models import MemorySelector
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
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
        spec = JobSpec(
            schema_version=2,
            job_id="recovery-job",
            config_path=str(config_path.resolve()),
            config_revision="cfg-1",
            group_key="news",
            group_path=str(self.group_path.resolve()),
            agent_name="writer",
            workspace_dir=str(self.group_path.resolve()),
            trigger="manual_prompt",
            integration_name="script",
            integration_config={},
            blueprint=BlueprintRef(
                key="writer-blueprint",
                source_digest="digest-1",
                integration="script",
                projector_version="v1",
                cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
            ),
            routine_id="publish-memory",
            skill="publish-memory",
            skill_arguments=(),
            task_input="Publish memory",
            runtime_policy=RuntimePolicySnapshot(
                timeout=1800,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tool_mode="all",
                tool_names=(),
            ),
            memory=MemoryBinding(
                selector={"scope": "agent"},
                canonical_json='{"scope":"agent"}',
                memory_hash="memory-hash-1",
                path=str((tmp_path / "memory-store" / "agent").resolve()),
            ),
            trigger_context=None,
            prompt_source={
                "type": "saved_prompt",
                "path": "shared/prompts/routine.md",
            },
            timeout_override=None,
            created_at="2026-07-15T00:00:00+00:00",
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
        prepared = prepare_publication(self.stage, job_store=self.job_store)
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


def test_recovery_rejects_corrupted_absolute_paths_without_touching_sentinel(
    recovery_fixture,
):
    recovery_fixture.crash_at("backed_up")
    sentinel = recovery_fixture.group_path / "sentinel.txt"
    sentinel.write_text("do-not-touch", encoding="utf-8")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    payload = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    payload["job_path"] = str(sentinel)
    payload["stage_path"] = str(sentinel)
    payload["backup_path"] = str(sentinel)
    journal_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="journal|unsafe|invalid|job"):
        recover_publications(
            recovery_fixture.store_root,
            recovery_fixture.job_store,
        )

    assert sentinel.read_text(encoding="utf-8") == "do-not-touch"
    assert sentinel.exists()
    assert not journal_path.exists()
    assert list(
        (recovery_fixture.store_root / ".journals" / "_quarantine").glob(
            "*/*.yaml"
        )
    )
    assert read_job(recovery_fixture.job_path).status == "running"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("memory_hash", "not-a-hash"),
        ("job_id", "bad/job"),
    ],
)
def test_recovery_rejects_mismatched_journal_identity(
    recovery_fixture,
    field,
    value,
):
    recovery_fixture.crash_at("prepared")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    payload = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    payload[field] = value
    journal_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="journal|hash|job"):
        recover_publications(
            recovery_fixture.store_root,
            recovery_fixture.job_store,
        )

    assert not journal_path.exists()
    assert list(
        (recovery_fixture.store_root / ".journals" / "_quarantine").glob(
            "*/*.yaml"
        )
    )
    assert read_job(recovery_fixture.job_path).status == "running"


def test_recovery_rejects_journal_filename_mismatch(recovery_fixture):
    recovery_fixture.crash_at("published")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    mismatched = journal_path.with_name("otherjob.yaml")
    journal_path.rename(mismatched)

    with pytest.raises(ValueError, match="journal|filename|job"):
        recover_publications(
            recovery_fixture.store_root,
            recovery_fixture.job_store,
        )

    assert not mismatched.exists()
    assert list(
        (recovery_fixture.store_root / ".journals" / "_quarantine").glob(
            "*/*.yaml"
        )
    )
    assert read_job(recovery_fixture.job_path).status == "running"


def test_recovery_is_idempotent_for_valid_old_and_new_journals(
    recovery_fixture,
):
    recovery_fixture.crash_at("published")

    first = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.job_store,
    )
    second = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.job_store,
    )

    assert first.recovered == 1
    assert second.recovered == 0
    assert read_job(recovery_fixture.job_path).status == "complete"


def test_recovery_does_not_complete_prepared_journal_with_matching_new_revision(
    recovery_fixture,
):
    recovery_fixture.crash_at("prepared")
    new_files = {
        item.name: item.read_bytes()
        for item in recovery_fixture.stage.directory.iterdir()
        if item.is_file()
    }
    recovery_fixture.store.try_save(
        recovery_fixture.resolved,
        recovery_fixture.store.read(recovery_fixture.resolved).revision,
        new_files,
    )

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.job_store,
    )

    assert result.recovered == 0
    assert read_job(recovery_fixture.job_path).status == "running"


def test_recovery_does_not_complete_backed_up_journal_with_matching_new_revision(
    recovery_fixture,
):
    recovery_fixture.crash_at("after_replace")

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.job_store,
    )

    assert result.recovered == 1
    assert read_job(recovery_fixture.job_path).status == "complete"


def test_recovery_holds_canonical_memory_lock_before_reading_journal_state(
    recovery_fixture,
):
    recovery_fixture.crash_at("prepared")
    lock_path = recovery_fixture.store._lock_path(recovery_fixture.resolved)
    from agency.fs.locks import exclusive_lock
    import threading

    finished = threading.Event()

    def recover():
        recover_publications(
            recovery_fixture.store_root,
            recovery_fixture.job_store,
        )
        finished.set()

    with exclusive_lock(lock_path, wait=True):
        worker = threading.Thread(target=recover)
        worker.start()
        assert not finished.wait(0.2)
    worker.join(5)
    assert not worker.is_alive()
    assert finished.is_set()


def test_recovery_completes_published_journal_with_matching_new_revision(
    recovery_fixture,
):
    recovery_fixture.crash_at("published")

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.job_store,
    )

    assert result.recovered == 1
    assert read_job(recovery_fixture.job_path).status == "complete"


def _run_python(code: str, *args: str, timeout: int = 20):
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("prepared", b"old\n"),
        ("backed_up", b"old\n"),
        ("after_replace", b"new\n"),
        ("published", b"new\n"),
    ],
)
def test_direct_save_crash_recovery_subprocess_keeps_complete_old_or_new(
    tmp_path,
    phase,
    expected,
):
    store_root = tmp_path / "memory-store"
    jobs_dir = tmp_path / "jobs"
    crash_code = """
from pathlib import Path
import os
import sys
from agency.configuration.models import MemorySelector
from agency.memory import MemoryStore, resolve_memory_selector
from agency.memory.publication import _prepare_direct_transaction, _run_transaction_locked
from agency.memory.store import _memory_lock

root = Path(sys.argv[1])
phase = sys.argv[2]
store = MemoryStore(root)
resolved = resolve_memory_selector(
    MemorySelector(scope='agent'),
    job_id='job-direct',
    group_key='news',
    agent_name='writer',
    routine_id=None,
    channels={},
    store_root=root,
)
seeded = store.ensure(resolved)
current = store.try_save(resolved, seeded.revision, {'memory.md': b'old\\n'})
operation = _prepare_direct_transaction(resolved, current.files, {'memory.md': b'new\\n'})
with _memory_lock(resolved, wait=True):
    try:
        _run_transaction_locked(operation, crash_at=phase)
    except Exception:
        os._exit(75)
os._exit(0)
"""
    recover_code = """
from pathlib import Path
import sys
from agency.memory.recovery import recover_publications

recover_publications(Path(sys.argv[1]), Path(sys.argv[2]))
"""

    crashed = _run_python(crash_code, str(store_root), phase)
    assert crashed.returncode == 75

    recovered = _run_python(recover_code, str(store_root), str(jobs_dir))
    assert recovered.returncode == 0, recovered.stderr

    store = MemoryStore(store_root)
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="job-direct",
        group_key="news",
        agent_name="writer",
        routine_id=None,
        channels={},
        store_root=store_root,
    )
    assert store.read(resolved).files == {"memory.md": expected}
    assert not list((store_root / ".journals").glob("*/*.yaml"))
