from multiprocessing import Event, Process
from pathlib import Path
import stat

import pytest

from agency.configuration.models import MemorySelector
from agency.fs.locks import ResourceBusyError
from agency.memory import (
    MemoryConflictError,
    MemoryStage,
    MemoryStore,
    memory_content_revision,
    read_memory,
    resolve_memory_selector,
    try_save_memory,
)


def _hold_memory_lock(lock_path: str, acquired: Event, release: Event) -> None:
    from agency.fs.locks import exclusive_lock

    with exclusive_lock(Path(lock_path), wait=True):
        acquired.set()
        release.wait(5)


def _make_hostile_infra_entry(path: Path, target: Path, monkeypatch) -> str:
    try:
        path.symlink_to(target, target_is_directory=True)
        return "real-link"
    except OSError:
        original = Path.lstat
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

        class FakeStatResult:
            def __init__(self, result):
                self.st_mode = result.st_mode
                self.st_file_attributes = reparse_flag

        def fake_lstat(self):
            result = original(self)
            if self == path and reparse_flag:
                return FakeStatResult(result)
            return result

        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "lstat", fake_lstat)
        return "simulated-reparse"


@pytest.fixture
def memory_root(tmp_path):
    return tmp_path / "memory-store"


@pytest.fixture
def memory_store(memory_root):
    return MemoryStore(memory_root)


@pytest.fixture
def resolved_memory(memory_root):
    return resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="job-a",
        group_key="news",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=memory_root,
    )


def test_first_use_seeds_empty_memory_md(memory_store, resolved_memory):
    snapshot = memory_store.ensure(resolved_memory)

    assert snapshot.files == {"memory.md": b""}
    assert (resolved_memory.directory / "memory.md").read_bytes() == b""


def test_read_returns_last_canonical_snapshot(memory_store, resolved_memory):
    seeded = memory_store.ensure(resolved_memory)

    saved = memory_store.try_save(
        resolved_memory,
        seeded.revision,
        {"memory.md": b"hello", "notes.md": b"world"},
    )
    loaded = memory_store.read(resolved_memory)

    expected = {"memory.md": b"hello", "notes.md": b"world"}

    assert loaded.files == saved.files == expected
    assert loaded.revision == saved.revision


def test_content_revision_uses_sorted_direct_filenames_and_exact_bytes():
    files = {"z.md": b"z\n", "a.md": b"a\r\n"}
    first = memory_content_revision(files)
    second = memory_content_revision({"a.md": b"a\r\n", "z.md": b"z\n"})
    third = memory_content_revision({"a.md": b"a\n", "z.md": b"z\n"})

    assert first == second
    assert third != first


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"nested/memory.md": b""},
        {"nested\\memory.md": b""},
        {"memory.txt": b""},
        {".hidden": b""},
        {"memory.md ": b""},
        {"memory.md.": b""},
        {"CON.md": b""},
    ],
)
def test_try_save_rejects_invalid_canonical_file_sets(
    memory_store,
    resolved_memory,
    files,
):
    seeded = memory_store.ensure(resolved_memory)

    with pytest.raises(ValueError):
        memory_store.try_save(resolved_memory, seeded.revision, files)


def test_try_save_rejects_case_fold_collisions(memory_store, resolved_memory):
    seeded = memory_store.ensure(resolved_memory)

    with pytest.raises(ValueError, match="case-fold"):
        memory_store.try_save(
            resolved_memory,
            seeded.revision,
            {"Memory.md": b"", "memory.md": b""},
        )


def test_read_rejects_nested_non_markdown_and_symlink_entries(
    memory_store,
    resolved_memory,
):
    memory_store.ensure(resolved_memory)
    (resolved_memory.directory / "nested").mkdir()
    (resolved_memory.directory / "nested" / "memory.md").write_text(
        "bad",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="nested"):
        read_memory(resolved_memory)


def test_try_save_rejects_stale_revision_and_preserves_current_snapshot(
    memory_store,
    resolved_memory,
):
    seeded = memory_store.ensure(resolved_memory)
    current = memory_store.try_save(
        resolved_memory,
        seeded.revision,
        {"memory.md": b"new"},
    )

    with pytest.raises(MemoryConflictError) as excinfo:
        memory_store.try_save(
            resolved_memory,
            seeded.revision,
            {"memory.md": b"stale"},
        )

    assert excinfo.value.expected_revision == seeded.revision
    assert excinfo.value.current.revision == current.revision
    assert excinfo.value.current.files == {"memory.md": b"new"}
    assert excinfo.value.attempted_files == {"memory.md": b"stale"}


def test_stage_copies_last_canonical_snapshot(memory_store, resolved_memory):
    seeded = memory_store.ensure(resolved_memory)
    memory_store.try_save(
        resolved_memory,
        seeded.revision,
        {"memory.md": b"old", "context.md": b"keep"},
    )

    stage = memory_store.stage(resolved_memory, job_id="job-123")

    assert isinstance(stage, MemoryStage)
    assert stage.job_id == "job-123"
    assert stage.directory.parent.parent == memory_store.root / ".staging"
    assert (stage.directory / "memory.md").read_bytes() == b"old"
    assert (stage.directory / "context.md").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        ".",
        "..",
        "nested/job",
        "nested\\job",
        "/absolute",
        "C:\\escape",
        "CON",
        "job ",
        "job.",
        "Job-123",
    ],
)
def test_stage_rejects_unsafe_job_ids_without_touching_neighbor_stage(
    memory_store,
    resolved_memory,
    job_id,
):
    seeded = memory_store.ensure(resolved_memory)
    memory_store.try_save(
        resolved_memory,
        seeded.revision,
        {"memory.md": b"canonical"},
    )
    neighbor = (
        memory_store.root
        / ".staging"
        / resolved_memory.memory_hash
        / "job-123"
    )
    neighbor.mkdir(parents=True, exist_ok=True)
    (neighbor / "sentinel.md").write_bytes(b"keep")

    with pytest.raises(ValueError, match="job id"):
        memory_store.stage(resolved_memory, job_id=job_id)

    assert (neighbor / "sentinel.md").read_bytes() == b"keep"


def test_stage_rejects_unicode_normalization_ambiguous_job_id(
    memory_store,
    resolved_memory,
):
    memory_store.ensure(resolved_memory)

    with pytest.raises(ValueError, match="job id"):
        memory_store.stage(resolved_memory, job_id="jo\u0301b")


def test_stage_rejects_hostile_staging_symlink_and_preserves_sentinel(
    memory_store,
    resolved_memory,
    monkeypatch,
    tmp_path,
):
    memory_store.ensure(resolved_memory)
    external = tmp_path / "external-staging"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    hostile = memory_store.root / ".staging"

    mode = _make_hostile_infra_entry(hostile, external, monkeypatch)

    with pytest.raises(ValueError, match="staging"):
        memory_store.stage(resolved_memory, job_id="job-123")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert mode in {"real-link", "simulated-reparse"}


def test_stage_rejects_hostile_hash_directory_and_preserves_sentinel(
    memory_store,
    resolved_memory,
    monkeypatch,
    tmp_path,
):
    memory_store.ensure(resolved_memory)
    external = tmp_path / "external-hash-staging"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    staging_root = memory_store.root / ".staging"
    staging_root.mkdir(parents=True)
    hostile = staging_root / resolved_memory.memory_hash

    mode = _make_hostile_infra_entry(hostile, external, monkeypatch)

    with pytest.raises(ValueError, match="staging"):
        memory_store.stage(resolved_memory, job_id="job-123")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert mode in {"real-link", "simulated-reparse"}


def test_try_save_reports_nonblocking_busy_ui_save(
    memory_store,
    resolved_memory,
):
    revision = memory_store.ensure(resolved_memory).revision
    acquired = Event()
    release = Event()
    process = Process(
        target=_hold_memory_lock,
        args=(
            str(memory_store._lock_path(resolved_memory)),
            acquired,
            release,
        ),
    )
    process.start()
    assert acquired.wait(5)

    try:
        with pytest.raises(ResourceBusyError):
            try_save_memory(
                resolved_memory,
                revision,
                {"memory.md": b"busy"},
            )
    finally:
        release.set()
        process.join(5)
        assert process.exitcode == 0


def test_try_save_rejects_hostile_backups_symlink_and_preserves_sentinel(
    memory_store,
    resolved_memory,
    monkeypatch,
    tmp_path,
):
    seeded = memory_store.ensure(resolved_memory)
    external = tmp_path / "external-backups"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    hostile = memory_store.root / ".backups"

    mode = _make_hostile_infra_entry(hostile, external, monkeypatch)

    with pytest.raises(ValueError, match="backup"):
        memory_store.try_save(
            resolved_memory,
            seeded.revision,
            {"memory.md": b"updated"},
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert mode in {"real-link", "simulated-reparse"}


def test_store_paths_stay_under_hash_directory(memory_store, resolved_memory):
    assert (
        resolved_memory.directory
        == memory_store.root / resolved_memory.memory_hash
    )
    assert resolved_memory.directory.parent == memory_store.root
    assert len(resolved_memory.memory_hash) == 64
    int(resolved_memory.memory_hash, 16)


def test_try_save_replaces_canonical_markdown_set_atomically(
    memory_store,
    resolved_memory,
):
    seeded = memory_store.ensure(resolved_memory)
    memory_store.try_save(
        resolved_memory,
        seeded.revision,
        {"memory.md": b"old", "extra.md": b"remove me"},
    )
    updated = memory_store.try_save(
        resolved_memory,
        read_memory(resolved_memory).revision,
        {"memory.md": b"new only"},
    )

    assert updated.files == {"memory.md": b"new only"}
    assert sorted(
        item.name for item in resolved_memory.directory.iterdir()
    ) == ["memory.md"]


def test_try_save_rolls_back_if_install_fails_after_evacuating_old_files(
    monkeypatch,
    memory_store,
    resolved_memory,
):
    seeded = memory_store.ensure(resolved_memory)
    original = {"memory.md": b"old", "notes.md": b"keep"}
    current = memory_store.try_save(
        resolved_memory,
        seeded.revision,
        original,
    )
    from agency.memory import store as memory_store_module

    real_move = memory_store_module._install_path
    state = {"new_moves": 0}

    def fail_after_first_new_move(src, dst):
        target = Path(dst)
        result = real_move(src, dst)
        if target.parent == resolved_memory.directory:
            state["new_moves"] += 1
            if state["new_moves"] == 1:
                raise OSError("install failed after first new file")
        return result

    monkeypatch.setattr(
        "agency.memory.store._install_path",
        fail_after_first_new_move,
    )

    with pytest.raises(RuntimeError, match="rolled back"):
        memory_store.try_save(
            resolved_memory,
            current.revision,
            {"memory.md": b"new", "extra.md": b"other"},
        )

    restored = memory_store.read(resolved_memory)

    assert restored.files == original
    assert restored.revision == current.revision
    assert sorted(
        item.name for item in resolved_memory.directory.iterdir()
    ) == [
        "memory.md",
        "notes.md",
    ]
    assert not any((memory_store.root / ".backups").iterdir())


def test_try_save_rolls_back_if_install_fails_immediately_after_evacuation(
    monkeypatch,
    memory_store,
    resolved_memory,
):
    seeded = memory_store.ensure(resolved_memory)
    original = {"memory.md": b"old", "notes.md": b"keep"}
    current = memory_store.try_save(
        resolved_memory,
        seeded.revision,
        original,
    )
    from agency.memory import store as memory_store_module

    real_move = memory_store_module._install_path

    def fail_on_first_new_move(src, dst):
        target = Path(dst)
        if target.parent == resolved_memory.directory:
            raise OSError("install failed before any new file landed")
        return real_move(src, dst)

    monkeypatch.setattr(
        "agency.memory.store._install_path",
        fail_on_first_new_move,
    )

    with pytest.raises(RuntimeError, match="rolled back"):
        memory_store.try_save(
            resolved_memory,
            current.revision,
            {"memory.md": b"new", "extra.md": b"other"},
        )

    restored = memory_store.read(resolved_memory)

    assert restored.files == original
    assert restored.revision == current.revision


def test_try_save_preserves_backup_if_rollback_recovery_fails(
    monkeypatch,
    memory_store,
    resolved_memory,
):
    seeded = memory_store.ensure(resolved_memory)
    original = {"memory.md": b"old", "notes.md": b"keep"}
    current = memory_store.try_save(
        resolved_memory,
        seeded.revision,
        original,
    )
    from agency.memory import store as memory_store_module

    real_install = memory_store_module._install_path
    state = {"new_failed": False, "restore_attempts": 0}

    def fail_install(src, dst):
        target = Path(dst)
        if (
            target.parent == resolved_memory.directory
            and not state["new_failed"]
        ):
            state["new_failed"] = True
            raise OSError("install failed")
        return real_install(src, dst)

    def fail_restore(src, dst):
        state["restore_attempts"] += 1
        raise OSError("restore failed")

    monkeypatch.setattr("agency.memory.store._install_path", fail_install)
    monkeypatch.setattr("agency.memory.store._restore_path", fail_restore)

    with pytest.raises(RuntimeError, match="recovery failed") as excinfo:
        memory_store.try_save(
            resolved_memory,
            current.revision,
            {"memory.md": b"new", "extra.md": b"other"},
        )

    backups = list((memory_store.root / ".backups").iterdir())

    assert state["restore_attempts"] >= 1
    assert backups
    assert "install failed" in str(excinfo.value)
    assert "restore failed" in str(excinfo.value)
    preserved = [item.name for item in backups[0].iterdir()]
    assert sorted(preserved) == ["memory.md", "notes.md"]
