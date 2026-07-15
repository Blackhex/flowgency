from multiprocessing import Event, Process
from pathlib import Path

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
