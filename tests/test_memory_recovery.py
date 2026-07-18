from pathlib import Path
import subprocess
import sys

from dataclasses import replace

import pytest
import yaml

from agency.configuration.models import MemorySelector
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import read_job, write_job
from agency.memory import MemoryStore, resolve_memory_selector
from agency.memory.publication import apply_publication, prepare_publication
from agency.memory.recovery import recover_publications


class RecoveryFixture:
    def __init__(self, tmp_path: Path, *, group_key: str = "news"):
        self.group_key = group_key
        self.group_path = tmp_path / "group"
        self.group_path.mkdir(parents=True)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("groups: {}\n", encoding="utf-8")
        self.store_root = tmp_path / "memory-store"
        self.job_store_root = JobStore(self.store_root)
        self.store = MemoryStore(self.store_root)
        self.resolved = resolve_memory_selector(
            MemorySelector(scope="agent"),
            job_id="recovery-job",
            group_key=group_key,
            agent_name="writer",
            routine_id="publish-memory",
            channels={},
            store_root=self.store_root,
        )
        spec = JobSpec(
            schema_version=2,
            job_id="recovery-job",
            config_path=str(config_path.resolve()),
            config_revision="cfg-1",
            group_key=group_key,
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
                selector=self.resolved.selector.model_dump(mode="python"),
                canonical_json=self.resolved.canonical_json,
                memory_hash=self.resolved.memory_hash,
                path=str(self.resolved.directory.resolve()),
            ),
            trigger_context=None,
            prompt_source={
                "type": "saved_prompt",
                "path": "shared/prompts/routine.md",
            },
            timeout_override=None,
            created_at="2026-07-15T00:00:00+00:00",
        )
        self.job_store = self.job_store_root.group_root(group_key)
        self.job_store.mkdir(parents=True, exist_ok=True)
        self.job_path = self.job_store_root.path(group_key, spec.job_id)
        queued = JobRecord.from_spec(spec)
        write_job(self.job_path, queued)
        write_job(self.job_path, replace(queued, status="running"))
        seeded = self.store.ensure(self.resolved)
        self.store.try_save(
            self.resolved,
            seeded.revision,
            {"memory.md": b"old\n", "notes.md": b"stable\n"},
        )
        self.stage = self.store.stage(self.resolved, job_id=spec.job_id)
        (self.stage.directory / "memory.md").write_bytes(b"new\n")
        self.canonical_is_new = False

    @property
    def owner_mapping(self) -> dict[str, object]:
        return {
            self.group_key: {
                "job_store": self.job_store,
                "group_path": self.group_path,
            }
        }

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
        recovery_fixture.owner_mapping,
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
            {},
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

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )

    assert sentinel.read_text(encoding="utf-8") == "do-not-touch"
    assert sentinel.exists()
    assert journal_path.exists()
    assert result.blocked_job_ids == ("recovery-job",)
    assert result.errors
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

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )

    assert journal_path.exists()
    assert result.blocked_job_ids == ("recovery-job",)
    assert result.errors
    assert read_job(recovery_fixture.job_path).status == "running"


def test_recovery_rejects_journal_filename_mismatch(recovery_fixture):
    recovery_fixture.crash_at("published")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    mismatched = journal_path.with_name("otherjob.yaml")
    journal_path.rename(mismatched)

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )

    assert mismatched.exists()
    assert result.blocked_job_ids == ("recovery-job",)
    assert result.errors
    assert read_job(recovery_fixture.job_path).status == "running"


def test_recovery_rejects_job_owned_by_wrong_configured_group(tmp_path):
    forged = RecoveryFixture(tmp_path, group_key="forged")
    forged.crash_at("published")
    journal_path = next(
        (forged.store_root / ".journals").glob("*/*.yaml")
    )

    result = recover_publications(
        forged.store_root,
        {"news": {"job_store": forged.job_store, "group_path": forged.group_path}},
    )

    assert result.recovered == 0
    assert result.blocked_job_ids == ("recovery-job",)
    assert "configured group" in result.errors[0]
    assert journal_path.exists()
    assert forged.read_job().status == "running"


def test_recovery_rejects_changed_journal_with_equal_revisions(
    recovery_fixture,
):
    recovery_fixture.crash_at("published")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    payload = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    payload["old_revision"] = payload["new_revision"]
    journal_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    backup_path = (
        recovery_fixture.store_root
        / ".publication-backups"
        / recovery_fixture.resolved.memory_hash
        / "recovery-job"
    )
    for path in backup_path.iterdir():
        if path.is_file():
            path.unlink()
    for path in recovery_fixture.stage.directory.iterdir():
        if path.is_file():
            (backup_path / path.name).write_bytes(path.read_bytes())

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )

    assert result.recovered == 0
    assert result.blocked_job_ids == ("recovery-job",)
    assert "revision" in result.errors[0]
    assert journal_path.exists()
    assert recovery_fixture.read_job().status == "running"


def test_recovery_is_idempotent_for_valid_old_and_new_journals(
    recovery_fixture,
):
    recovery_fixture.crash_at("published")

    first = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )
    second = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
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
        recovery_fixture.owner_mapping,
    )

    assert result.recovered == 0
    assert read_job(recovery_fixture.job_path).status == "running"


def test_recovery_does_not_complete_backed_up_journal_with_matching_new_revision(
    recovery_fixture,
):
    recovery_fixture.crash_at("after_replace")

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
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
            recovery_fixture.owner_mapping,
        )
        finished.set()

    with exclusive_lock(lock_path, wait=True):
        worker = threading.Thread(target=recover)
        worker.start()
        assert not finished.wait(0.2)
    worker.join(5)
    assert not worker.is_alive()
    assert finished.is_set()


def test_recovery_rereads_journal_after_waiting_for_live_publisher(
    recovery_fixture,
    monkeypatch,
):
    recovery_fixture.crash_at("prepared")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    identity_read = __import__("threading").Event()
    original_read_identity = __import__(
        "agency.memory.recovery",
        fromlist=["_read_identity"],
    )._read_identity

    def observed_identity(*args):
        result = original_read_identity(*args)
        identity_read.set()
        return result

    monkeypatch.setattr(
        "agency.memory.recovery._read_identity",
        observed_identity,
    )
    from agency.fs.locks import exclusive_lock
    import threading

    outcome = {}
    lock_path = recovery_fixture.store._lock_path(recovery_fixture.resolved)
    with exclusive_lock(lock_path, wait=True):
        worker = threading.Thread(
            target=lambda: outcome.setdefault(
                "result",
                recover_publications(
                    recovery_fixture.store_root,
                    recovery_fixture.owner_mapping,
                ),
            )
        )
        worker.start()
        assert identity_read.wait(5)
        journal_path.unlink()
    worker.join(5)

    assert not worker.is_alive()
    assert outcome["result"].errors == ()
    assert outcome["result"].blocked_job_ids == ()
    assert outcome["result"].recovered == 0


def test_recovery_completes_published_journal_with_matching_new_revision(
    recovery_fixture,
):
    recovery_fixture.crash_at("published")

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )

    assert result.recovered == 1
    assert read_job(recovery_fixture.job_path).status == "complete"


def test_direct_save_only_recovery_needs_no_job_store(tmp_path):
    store_root = tmp_path / "memory-store"
    store = MemoryStore(store_root)
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="direct-job",
        group_key="news",
        agent_name="writer",
        routine_id=None,
        channels={},
        store_root=store_root,
    )
    seeded = store.ensure(resolved)
    current = store.try_save(resolved, seeded.revision, {"memory.md": b"old\n"})
    from agency.memory.publication import _prepare_direct_transaction, _run_transaction_locked
    from agency.memory.store import _memory_lock

    operation = _prepare_direct_transaction(
        resolved,
        current.files,
        {"memory.md": b"new\n"},
    )
    with _memory_lock(resolved, wait=True):
        with pytest.raises(Exception):
            _run_transaction_locked(operation, crash_at="published")

    result = recover_publications(store_root, {})

    assert result.recovered == 1
    assert result.blocked_job_ids == ()
    assert store.read(resolved).files == {"memory.md": b"new\n"}


def test_duplicate_job_id_across_allowed_stores_establishes_barrier(
    recovery_fixture,
    tmp_path,
):
    recovery_fixture.crash_at("published")
    duplicate_store = tmp_path / "other" / ".jobs" / "other"
    duplicate_store.mkdir(parents=True)
    duplicate_path = duplicate_store / recovery_fixture.job_path.name
    duplicate_path.write_bytes(recovery_fixture.job_path.read_bytes())
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )

    result = recover_publications(
        recovery_fixture.store_root,
        {
            "news": {
                "job_store": recovery_fixture.job_store,
                "group_path": recovery_fixture.group_path,
            },
            "other": {
                "job_store": duplicate_store,
                "group_path": tmp_path / "other-group",
            },
        },
    )

    assert result.recovered == 0
    assert result.blocked_job_ids == ("recovery-job",)
    assert result.errors
    assert journal_path.exists()
    assert read_job(recovery_fixture.job_path).status == "running"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.pop("kind"), "kind"),
        (lambda payload: payload.update(phase="unknown"), "phase"),
        (lambda payload: payload.update(old_revision="invalid"), "revision"),
        (lambda payload: payload.update(extra="forbidden"), "keys"),
        (lambda payload: payload.update(stage_directory="../escape"), "stage"),
    ],
)
def test_invalid_journal_schema_is_a_persistent_barrier(
    recovery_fixture,
    mutation,
    message,
):
    recovery_fixture.crash_at("prepared")
    journal_path = next(
        (recovery_fixture.store_root / ".journals").glob("*/*.yaml")
    )
    payload = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    mutation(payload)
    journal_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    result = recover_publications(
        recovery_fixture.store_root,
        recovery_fixture.owner_mapping,
    )

    assert result.recovered == 0
    assert result.blocked_job_ids == ("recovery-job",)
    assert message in result.errors[0]
    assert journal_path.exists()
    assert recovery_fixture.stage.directory.exists()
    assert read_job(recovery_fixture.job_path).status == "running"


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

recover_publications(Path(sys.argv[1]), {})
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
