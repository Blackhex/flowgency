from __future__ import annotations

import os
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from agency.blueprints import BlueprintInspection
from agency.blueprints import cache as cache_module
from agency.blueprints.projectors import StaticRuntimeProjector
from agency.fs.snapshot import capture_tree
from agency.integrations.models import ProjectorCapabilities


def _write_blueprint(root: Path, key: str = "advisor") -> Path:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    (skill / "SKILL.md").write_bytes(
        (
            b"---\nname: daily-review\n"
            b"description: Review daily editorial work.\n"
            b"---\n\nRun the review.\n"
        ),
    )
    return blueprint


def _inspection(root: Path, key: str = "advisor") -> BlueprintInspection:
    blueprint = _write_blueprint(root, key=key)
    snapshot = capture_tree(blueprint)
    return BlueprintInspection(
        key=key,
        path=blueprint,
        title="Advisor",
        skills=("daily-review",),
        snapshot=snapshot,
    )


def _projector(version: str = "v-test") -> StaticRuntimeProjector:
    return StaticRuntimeProjector(
        version=version,
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("AGENTS.md"),
            skills_target=PurePosixPath(".agents/skills"),
            discovers_skills=True,
            activates_selected_skill=True,
        ),
    )


@pytest.fixture
def inspection(tmp_path: Path) -> BlueprintInspection:
    return _inspection(tmp_path)


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "compiled-agents"


@pytest.fixture
def cache(cache_root: Path):
    from agency.blueprints.cache import CompilationCache

    return CompilationCache(cache_root, {"copilot": _projector()})


def _windows_permission_error(winerror: int) -> PermissionError:
    error = PermissionError(13, "Access is denied")
    error.winerror = winerror
    return error


def test_publish_directory_retries_transient_windows_sharing_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "complete.txt").write_text("complete", encoding="utf-8")
    real_replace = os.replace
    attempts = 0

    def transient_then_success(current_source, current_destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _windows_permission_error(32)
        real_replace(current_source, current_destination)

    monkeypatch.setattr(cache_module.os, "replace", transient_then_success)
    monkeypatch.setattr(
        cache_module,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _: None),
        raising=False,
    )

    cache_module._publish_directory(source, destination)

    assert attempts == 3
    assert destination.joinpath("complete.txt").read_text(encoding="utf-8") == "complete"
    assert not source.exists()


def test_publish_directory_exhaustion_leaves_destination_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    attempts = 0

    def always_busy(*_args):
        nonlocal attempts
        attempts += 1
        raise _windows_permission_error(5)

    def copytree_must_not_run(*_args, **_kwargs):
        raise AssertionError("copytree must never publish a live cache entry")

    monotonic_values = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(cache_module.os, "replace", always_busy)
    monkeypatch.setattr(cache_module.shutil, "copytree", copytree_must_not_run)
    monkeypatch.setattr(
        cache_module,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(monotonic_values),
            sleep=lambda _: None,
        ),
        raising=False,
    )

    with pytest.raises(PermissionError):
        cache_module._publish_directory(source, destination)

    assert attempts == 2
    assert source.exists()
    assert not destination.exists()


def test_publish_directory_does_not_retry_non_transient_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    attempts = 0

    def denied(*_args):
        nonlocal attempts
        attempts += 1
        raise _windows_permission_error(13)

    monkeypatch.setattr(cache_module.os, "replace", denied)

    with pytest.raises(PermissionError):
        cache_module._publish_directory(source, destination)

    assert attempts == 1
    assert source.exists()
    assert not destination.exists()


def test_publish_directory_does_not_overwrite_destination_created_during_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    calls = 0

    def destination_race(*_args):
        nonlocal calls
        calls += 1
        destination.mkdir()
        destination.joinpath("winner.txt").write_text("winner", encoding="utf-8")
        raise _windows_permission_error(32)

    monkeypatch.setattr(cache_module.os, "replace", destination_race)

    with pytest.raises(PermissionError):
        cache_module._publish_directory(source, destination)

    assert calls == 1
    assert source.exists()
    assert destination.joinpath("winner.txt").read_text(encoding="utf-8") == "winner"



def test_identical_blueprint_and_projector_reuse_one_artifact(
    cache,
    inspection,
):
    first = cache.ensure_compiled("copilot", inspection)
    second = cache.ensure_compiled("copilot", inspection)

    assert first.entry_path == second.entry_path
    assert first.runtime_path == second.runtime_path
    assert first.ref.integration == "copilot"
    assert first.ref.projector_version == "v-test"
    assert first.ref.source_digest == inspection.snapshot.digest


def test_projector_version_separates_cache_keys(
    cache_root: Path,
    inspection: BlueprintInspection,
):
    from agency.blueprints.cache import CompilationCache

    first_cache = CompilationCache(cache_root, {"copilot": _projector("baseline")})
    second_cache = CompilationCache(cache_root, {"copilot": _projector("updated")})

    first = first_cache.ensure_compiled("copilot", inspection)
    second = second_cache.ensure_compiled("copilot", inspection)

    assert first.entry_path != second.entry_path
    assert (
        first.entry_path
        == cache_root / "copilot" / "baseline" / inspection.snapshot.digest
    )
    assert (
        second.entry_path
        == cache_root / "copilot" / "updated" / inspection.snapshot.digest
    )


def test_corrupt_artifact_is_quarantined_and_rebuilt(
    cache,
    inspection,
    cache_root: Path,
):
    artifact = cache.ensure_compiled("copilot", inspection)
    artifact.runtime_path.joinpath("AGENTS.md").write_text(
        "corrupt",
        encoding="utf-8",
    )

    rebuilt = cache.ensure_compiled("copilot", inspection)

    assert (
        rebuilt.runtime_path.joinpath("AGENTS.md").read_bytes()
        == inspection.snapshot.file("AGENTS.md").content
    )
    quarantined = list((cache_root / "_quarantine").iterdir())
    assert quarantined
    assert any(
        item.name.endswith(f"copilot--v-test--{inspection.snapshot.digest}")
        for item in quarantined
    )


def test_validate_artifact_rejects_manifest_runtime_mismatch(
    cache,
    inspection,
):
    from agency.blueprints.cache import validate_artifact

    artifact = cache.ensure_compiled("copilot", inspection)
    payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    payload["runtime_files"][0]["sha256"] = "0" * 64
    artifact.manifest_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        validate_artifact(
            artifact.entry_path,
            artifact.ref,
            inspection.snapshot,
            _projector(),
        )


def test_manifest_is_deterministic(cache, inspection):
    first = cache.ensure_compiled("copilot", inspection)
    manifest_one = first.manifest_path.read_text(encoding="utf-8")
    first.entry_path.joinpath("manifest.json").unlink()

    rebuilt = cache.ensure_compiled("copilot", inspection)
    manifest_two = rebuilt.manifest_path.read_text(encoding="utf-8")

    assert manifest_one == manifest_two


def test_pin_lifecycle_and_active_pins(cache, inspection):
    from agency.blueprints.cache import active_pins

    artifact = cache.ensure_compiled("copilot", inspection)
    pin_path = cache.pin(artifact, "job-123")

    assert pin_path == (
        cache.root
        / "_pins"
        / f"copilot--v-test--{inspection.snapshot.digest}"
        / "job-123"
    )
    assert active_pins(cache.root, artifact.ref) == ("job-123",)

    cache.release(artifact, "job-123")

    assert active_pins(cache.root, artifact.ref) == ()


def test_create_launch_view_copies_runtime_tree_and_is_private(
    cache,
    inspection,
    tmp_path: Path,
):
    from agency.jobs.launch_view import create_launch_view

    artifact = cache.ensure_compiled("copilot", inspection)
    launch_root = tmp_path / "jobs" / "job-1" / "launch"

    launch_view = create_launch_view(artifact, launch_root)

    assert launch_view != artifact.runtime_path
    assert launch_view == launch_root
    assert (
        launch_view.joinpath("AGENTS.md").read_bytes()
        == artifact.runtime_path.joinpath("AGENTS.md").read_bytes()
    )

    launch_view.joinpath("AGENTS.md").write_text("mutated", encoding="utf-8")

    assert (
        artifact.runtime_path.joinpath("AGENTS.md").read_bytes()
        == inspection.snapshot.file("AGENTS.md").content
    )


@pytest.mark.parametrize(
    ("destination_factory", "expected_fragment"),
    [
        (
            lambda artifact, tmp_path: artifact.runtime_path,
            "overlap",
        ),
        (
            lambda artifact, tmp_path: artifact.entry_path,
            "overlap",
        ),
        (
            lambda artifact, tmp_path: artifact.entry_path.parent,
            "overlap",
        ),
        (
            lambda artifact, tmp_path: artifact.runtime_path / "nested-launch",
            "overlap",
        ),
    ],
)
def test_create_launch_view_rejects_overlapping_destinations_without_mutating_cache(
    cache,
    inspection,
    tmp_path: Path,
    destination_factory,
    expected_fragment: str,
):
    from agency.jobs.launch_view import create_launch_view

    artifact = cache.ensure_compiled("copilot", inspection)
    cache_bytes_before = {
        path.relative_to(artifact.entry_path): path.read_bytes()
        for path in artifact.entry_path.rglob("*")
        if path.is_file()
    }
    destination = destination_factory(artifact, tmp_path)

    with pytest.raises(ValueError, match=expected_fragment):
        create_launch_view(artifact, destination)

    cache_bytes_after = {
        path.relative_to(artifact.entry_path): path.read_bytes()
        for path in artifact.entry_path.rglob("*")
        if path.is_file()
    }
    assert cache_bytes_after == cache_bytes_before


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        ".",
        "..",
        "nested/job",
        "nested\\job",
        "/absolute",
        "\\absolute",
        "C:\\absolute",
        "job.",
        "job ",
        "CON",
        "nul.txt",
    ],
)
def test_pin_and_release_reject_unsafe_job_ids(
    cache,
    inspection,
    job_id: str,
):
    artifact = cache.ensure_compiled("copilot", inspection)

    with pytest.raises(ValueError, match="job_id"):
        cache.pin(artifact, job_id)

    with pytest.raises(ValueError, match="job_id"):
        cache.release(artifact, job_id)


def test_active_pins_ignores_unexpected_entries(cache, inspection):
    from agency.blueprints.cache import active_pins

    artifact = cache.ensure_compiled("copilot", inspection)
    pins_dir = (
        cache.root
        / "_pins"
        / f"copilot--v-test--{inspection.snapshot.digest}"
    )
    pins_dir.mkdir(parents=True, exist_ok=True)
    (pins_dir / "job-123").write_text("", encoding="utf-8")
    (pins_dir / "job-456").write_text("", encoding="utf-8")
    (pins_dir / "nested").mkdir()
    (pins_dir / "nested" / "job-999").write_text("", encoding="utf-8")

    link = pins_dir / "job-link"
    try:
        os.symlink(pins_dir / "job-123", link)
    except (AttributeError, NotImplementedError, OSError):
        link = None

    active = active_pins(cache.root, artifact.ref)

    assert active == ("job-123", "job-456")
    assert "job-999" not in active
    if link is not None:
        assert "job-link" not in active


def test_validate_artifact_rejects_symlink_or_special_files(cache, inspection):
    from agency.blueprints.cache import validate_artifact

    artifact = cache.ensure_compiled("copilot", inspection)
    extra = artifact.runtime_path / "linked.md"
    try:
        extra.symlink_to(artifact.runtime_path / "AGENTS.md")
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(ValueError):
        validate_artifact(
            artifact.entry_path,
            artifact.ref,
            inspection.snapshot,
            _projector(),
        )
