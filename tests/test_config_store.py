from __future__ import annotations

from copy import deepcopy
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
    raw_config, config_paths
):
    from agency.configuration.store import config_revision, load_config_snapshot

    path = _write_yaml(config_paths["config_path"], raw_config)

    snapshot = load_config_snapshot(path)

    payload = path.read_bytes()
    assert snapshot.revision == hashlib.sha256(payload).hexdigest()
    assert snapshot.revision == config_revision(payload)
    assert snapshot.raw == raw_config


def test_config_store_round_trips_canonical_config(tmp_path, raw_config):
    from agency.configuration.store import ConfigStore

    path = tmp_path / "config.yaml"
    snapshot = ConfigStore(path).create(raw_config)

    assert snapshot.raw == raw_config
    assert snapshot.raw["schema_version"] == 3


def test_create_requires_absent_file(raw_config, config_paths):
    from agency.configuration.store import ConfigStore

    store = ConfigStore(config_paths["config_path"])
    created = store.create(raw_config)

    assert created.path == config_paths["config_path"].resolve()
    assert created.raw == raw_config
    assert config_paths["config_path"].read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        store.create(raw_config)


def test_patch_rejects_stale_revision_and_preserves_newer_config(
    raw_config, config_paths
):
    from agency.configuration.store import ConfigConflictError, ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
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


def test_patch_writes_utf8_yaml(raw_config, config_paths):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
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


def test_patch_rejects_unknown_root_key(raw_config, config_paths):
    from agency.configuration import ValidationFailed
    from agency.configuration.store import ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
    store = ConfigStore(path)
    snapshot = store.load()
    original = path.read_text(encoding="utf-8")

    with pytest.raises(ValidationFailed) as excinfo:
        store.patch(
            snapshot.revision,
            lambda raw: raw.__setitem__("extensions", {"beta": True}),
        )

    assert any(issue.field == "extensions" for issue in excinfo.value.issues)
    assert path.read_text(encoding="utf-8") == original


def test_patch_reports_lock_contention(raw_config, config_paths):
    from agency.configuration.store import ConfigStore
    from agency.fs.locks import ResourceBusyError

    path = _write_yaml(config_paths["config_path"], raw_config)
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
    raw_config, config_paths
):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
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


def test_replace_preserves_existing_bytes_when_new_payload_is_invalid(
    raw_config, config_paths
):
    from agency.configuration import ValidationFailed
    from agency.configuration.store import ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
    store = ConfigStore(path)
    snapshot = store.load()
    original = path.read_bytes()

    with pytest.raises(ValidationFailed):
        store.replace(
            snapshot.revision,
            {
                "schema_version": 3,
                "agency": {"title": "Agency"},
                "groups": {},
            },
        )

    assert path.read_bytes() == original


def test_replace_rejects_stale_revision_and_preserves_newer_bytes(
    raw_config, config_paths
):
    from agency.configuration.store import ConfigConflictError, ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
    store = ConfigStore(path)
    first = store.load()

    store.patch(
        first.revision,
        lambda raw: raw["agency"].update({"title": "Elsewhere"}),
    )
    newer = path.read_bytes()

    with pytest.raises(ConfigConflictError):
        store.replace(first.revision, raw_config)

    assert path.read_bytes() == newer


@pytest.mark.parametrize("operation", ["replace", "patch"])
def test_late_conflict_does_not_initialize_candidate_group_storage(
    tmp_path,
    raw_config,
    config_paths,
    monkeypatch,
    operation,
):
    from agency.configuration.store import ConfigConflictError, ConfigStore

    candidate_group = tmp_path / "candidate-group"
    raw = deepcopy(raw_config)
    raw["groups"]["newsletter"]["path"] = str(candidate_group)
    path = _write_yaml(config_paths["config_path"], raw)
    store = ConfigStore(path)
    snapshot = store.load()
    original_encode = ConfigStore._encode

    def encode_and_induce_conflict(self, value):
        payload = original_encode(self, value)
        path.write_bytes(path.read_bytes() + b"\n")
        return payload

    monkeypatch.setattr(ConfigStore, "_encode", encode_and_induce_conflict)

    with pytest.raises(
        ConfigConflictError,
        match="changed outside the Agency lock",
    ):
        if operation == "replace":
            store.replace(snapshot.revision, raw)
        else:
            store.patch(
                snapshot.revision,
                lambda current: current["groups"]["newsletter"].__setitem__(
                    "path", str(candidate_group)
                ),
            )

    assert not candidate_group.exists()


def test_snapshot_raw_alias_isolated_from_disk_and_patch_caller(
    raw_config, config_paths
):
    from agency.configuration.store import ConfigStore

    path = _write_yaml(config_paths["config_path"], raw_config)
    store = ConfigStore(path)

    first = store.load()
    alias = first.raw
    alias["agency"]["title"] = "Mutated in memory"

    assert path.read_text(encoding="utf-8") != "Mutated in memory"
    assert store.load().raw["agency"]["title"] == raw_config["agency"]["title"]

    second = store.load()
    assert second.raw["agency"]["title"] == raw_config["agency"]["title"]

    updated = store.patch(
        second.revision,
        lambda raw: raw["agency"].update({"title": "Patched"}),
    )

    assert first.raw["agency"]["title"] == "Mutated in memory"
    assert second.raw["agency"]["title"] == raw_config["agency"]["title"]
    assert updated.raw["agency"]["title"] == "Patched"


def test_create_validates_before_initializing_storage(raw_config, config_paths):
    from agency.configuration import ValidationFailed
    from agency.configuration.paths import job_store_root
    from agency.configuration.store import ConfigStore

    raw = deepcopy(raw_config)
    raw["groups"]["newsletter"]["workspace_path"] = str(
        config_paths["config_dir"] / "missing-workspace"
    )

    with pytest.raises(ValidationFailed):
        ConfigStore(config_paths["config_path"]).create(raw)

    assert not config_paths["compilation_cache"].exists()
    assert not config_paths["memory_store"].exists()
    assert not job_store_root(config_paths["memory_store"]).exists()
    assert not config_paths["group_path"].exists()
    assert not config_paths["config_path"].exists()
