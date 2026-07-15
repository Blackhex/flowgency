from __future__ import annotations

from pathlib import Path, PurePosixPath
import stat

import pytest

from agency.fs.snapshot import AssetValidationError, SnapshotFile, capture_tree, compute_source_digest


def _write_blueprint(root, key: str = "advisor"):
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    (skill / "SKILL.md").write_bytes(
        b"---\nname: daily-review\ndescription: Review daily editorial work.\n---\n\nRun the review.\n",
    )
    return blueprint


class _FakeEntry:
    def __init__(self, name: str, path, mode: int):
        self.name = name
        self.path = str(path)
        self._stat = type(
            "FakeStat",
            (),
            {
                "st_mode": mode,
                "st_size": path.stat().st_size,
                "st_mtime_ns": path.stat().st_mtime_ns,
                "st_dev": getattr(path.stat(), "st_dev", 0),
                "st_ino": getattr(path.stat(), "st_ino", 0),
                "st_file_attributes": 0,
            },
        )()

    def stat(self, *, follow_symlinks=False):
        return self._stat


def _inject_entries(monkeypatch, blueprint, names):
    import agency.fs.snapshot as snapshot_module

    backing_file = blueprint / "AGENTS.md"
    original_scandir = snapshot_module.os.scandir

    def fake_scandir(directory):
        entries = list(original_scandir(directory))
        if Path(directory) == blueprint:
            entries.extend(_FakeEntry(name, backing_file, stat.S_IFREG | 0o644) for name in names)
        return entries

    monkeypatch.setattr(snapshot_module.os, "scandir", fake_scandir)


def test_compute_source_digest_uses_posix_paths_and_length_framing():
    first = (
        SnapshotFile(PurePosixPath("a/b.txt"), b"c"),
        SnapshotFile(PurePosixPath("a"), b"b.txtc"),
    )
    second = tuple(reversed(first))

    assert compute_source_digest(first) == compute_source_digest(second)
    assert compute_source_digest(first) != compute_source_digest(
        (
            SnapshotFile(PurePosixPath("a/b.txt"), b"bc"),
            SnapshotFile(PurePosixPath("a"), b"txtc"),
        )
    )


def test_capture_tree_preserves_bytes_and_normalizes_relative_paths(tmp_path):
    blueprint = _write_blueprint(tmp_path)
    payload = b"\xff\x00resource\r\n"
    resource = blueprint / ".agents" / "skills" / "daily-review" / "icon.bin"
    resource.write_bytes(payload)

    snapshot = capture_tree(blueprint)

    assert snapshot.file("AGENTS.md").content == b"# Advisor\n"
    assert snapshot.file(".agents/skills/daily-review/SKILL.md").path == PurePosixPath(
        ".agents/skills/daily-review/SKILL.md"
    )
    assert snapshot.file(".agents/skills/daily-review/icon.bin").content == payload
    assert snapshot.digest == compute_source_digest(snapshot.files)


def test_capture_tree_rejects_symlink_or_windows_reparse_point(tmp_path):
    blueprint = _write_blueprint(tmp_path)
    link = blueprint / "linked.md"
    try:
        link.symlink_to(blueprint / "AGENTS.md")
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(AssetValidationError):
        capture_tree(blueprint)


def test_capture_tree_rejects_case_fold_collisions(tmp_path, monkeypatch):
    blueprint = _write_blueprint(tmp_path)
    _inject_entries(monkeypatch, blueprint, ["Readme.md", "README.md"])

    with pytest.raises(AssetValidationError):
        capture_tree(blueprint)


@pytest.mark.parametrize("name", ["CON", "nul.txt", "trailingspace ", "trailingdot."])
def test_capture_tree_rejects_windows_reserved_or_unstable_names(tmp_path, monkeypatch, name):
    blueprint = _write_blueprint(tmp_path)
    _inject_entries(monkeypatch, blueprint, [name])

    with pytest.raises(AssetValidationError):
        capture_tree(blueprint)


def test_capture_tree_retries_changed_source_then_succeeds(tmp_path, monkeypatch):
    import agency.fs.snapshot as snapshot_module

    blueprint = _write_blueprint(tmp_path)
    target = blueprint / "AGENTS.md"
    original = snapshot_module._scan_tree
    state = {"count": 0}

    def flaky(root):
        result = original(root)
        state["count"] += 1
        if state["count"] == 1:
            target.write_bytes(b"# Advisor updated\n")
        return result

    monkeypatch.setattr(snapshot_module, "_scan_tree", flaky)

    snapshot = capture_tree(blueprint)

    assert state["count"] >= 2
    assert snapshot.file("AGENTS.md").content == b"# Advisor updated\n"


def test_capture_tree_fails_after_three_changed_attempts(tmp_path, monkeypatch):
    import agency.fs.snapshot as snapshot_module

    blueprint = _write_blueprint(tmp_path)
    original = snapshot_module._scan_tree
    counter = {"value": 0}

    def always_changing(root):
        result = original(root)
        counter["value"] += 1
        inventory = list(result.inventory)
        inventory[0] = snapshot_module._InventoryEntry(
            path=inventory[0].path,
            size=inventory[0].size + counter["value"],
            mtime_ns=inventory[0].mtime_ns + counter["value"],
            file_id=inventory[0].file_id,
        )
        return snapshot_module._ScanResult(files=result.files, inventory=tuple(inventory))

    monkeypatch.setattr(snapshot_module, "_scan_tree", always_changing)

    with pytest.raises(AssetValidationError):
        capture_tree(blueprint)
