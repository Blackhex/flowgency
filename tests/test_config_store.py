from __future__ import annotations

import hashlib
from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest
import yaml


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _hold_config_lock(lock_path: str, acquired: Event, release: Event) -> None:
    from agency.fs.locks import exclusive_lock

    with exclusive_lock(Path(lock_path), wait=True):
        acquired.set()
        release.wait(5)


def _patch_with_external_write(
    path_str: str, revision: str, queue: Queue
) -> None:
    from agency.configuration.store import ConfigConflictError, ConfigStore

    path = Path(path_str)
    store = ConfigStore(path)

    try:
        def apply(raw: dict) -> None:
            raw["agency"]["title"] = "Updated"
            path.write_text(
                path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )

        store.patch(revision, apply)
    except ConfigConflictError as exc:
        queue.put(str(exc))
        return

    queue.put("missing-conflict")


def test_load_config_snapshot_uses_exact_file_bytes_for_revision(
    canonical_raw_config, canonical_paths
):
    from agency.configuration.store import config_revision, load_config_snapshot

    path = _write_yaml(canonical_paths["config_path"], canonical_raw_config)

    snapshot = load_config_snapshot(path)

    payload = path.read_bytes()
    assert snapshot.revision == hashlib.sha256(payload).hexdigest()
    assert snapshot.revision == config_revision(payload)
    assert snapshot.raw == canonical_raw_config


def test_create_requires_absent_file(canonical_raw_config, canonical_paths):
    from agency.configuration.store import ConfigStore

    store = ConfigStore(canonical_paths["config_path"])
    created = store.create(canonical_raw_config)

    assert created.path == canonical_paths["config_path"].resolve()
    assert created.raw == canonical_raw_config
    assert canonical_paths["config_path"].read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        store.create(canonical_raw_config)


def test_patch_rejects_stale_revision_and_preserves_newer_config(
    canonical_raw_config, canonical_paths
):
    from agency.configuration.store import ConfigConflictError, ConfigStore

    path = _write_yaml(canonical_paths["config_path"], canonical_raw_config)
    store = ConfigStore(path)
    first = store.load()

    store.patch(
        first.revision,
        lambda raw: raw["agency"].update({"title": "New"}),
    )

    with pytest.raises(ConfigConflictError):
        store.patch(
            first.revision,
            lambda raw: raw["agency"].update({"ai_backend": "copilot"}),
        )

    assert store.load().raw["agency"]["title"] == "New"


def test_patch_writes_utf8_yaml(canonical_raw_config, canonical_paths):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(canonical_paths["config_path"], canonical_raw_config)
    store = ConfigStore(path)
    snapshot = store.load()

    updated = store.patch(
        snapshot.revision,
        lambda raw: raw["groups"]["newsletter"]["agents"][0].__setitem__(
            "identity", {"display_name": "Zażółć", "title": "", "emoji": ""}
        ),
    )

    payload = path.read_bytes()
    assert "Zażółć" in payload.decode("utf-8")
    assert (
        updated.raw["groups"]["newsletter"]["agents"][0]["identity"][
            "display_name"
        ]
        == "Zażółć"
    )


def test_patch_reports_lock_contention(canonical_raw_config, canonical_paths):
    from agency.configuration.store import ConfigStore
    from agency.fs.locks import ResourceBusyError

    path = _write_yaml(canonical_paths["config_path"], canonical_raw_config)
    store = ConfigStore(path)
    acquired = Event()
    release = Event()
    process = Process(
        target=_hold_config_lock,
        args=(str(store.lock_path), acquired, release),
    )
    process.start()
    assert acquired.wait(5)

    try:
        with pytest.raises(ResourceBusyError):
            store.load(wait_for_lock=False)
    finally:
        release.set()
        process.join(5)
        assert process.exitcode == 0


def test_patch_detects_external_uncoordinated_edit_before_replace(
    canonical_raw_config, canonical_paths
):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(canonical_paths["config_path"], canonical_raw_config)
    snapshot = ConfigStore(path).load()
    queue: Queue[str] = Queue()
    process = Process(
        target=_patch_with_external_write,
        args=(str(path), snapshot.revision, queue),
    )
    process.start()
    process.join(5)
    assert process.exitcode == 0
    assert (
        queue.get(timeout=1) == "config.yaml changed outside the Agency lock"
    )


def test_snapshot_raw_alias_isolated_from_disk_and_patch_caller(
    canonical_raw_config, canonical_paths
):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(canonical_paths["config_path"], canonical_raw_config)
    store = ConfigStore(path)

    first = store.load()
    alias = first.raw
    alias["agency"]["title"] = "Mutated in memory"

    assert path.read_text(encoding="utf-8") != "Mutated in memory"
    assert store.load().raw["agency"]["title"] == canonical_raw_config["agency"]["title"]

    second = store.load()
    assert second.raw["agency"]["title"] == canonical_raw_config["agency"]["title"]

    updated = store.patch(
        second.revision,
        lambda raw: raw["agency"].update({"title": "Patched"}),
    )

    assert first.raw["agency"]["title"] == "Mutated in memory"
    assert second.raw["agency"]["title"] == canonical_raw_config["agency"]["title"]
    assert updated.raw["agency"]["title"] == "Patched"
