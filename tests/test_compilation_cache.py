from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from agency.blueprints import BlueprintInspection
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

    first_cache = CompilationCache(cache_root, {"copilot": _projector("v1")})
    second_cache = CompilationCache(cache_root, {"copilot": _projector("canonical")})

    first = first_cache.ensure_compiled("copilot", inspection)
    second = second_cache.ensure_compiled("copilot", inspection)

    assert first.entry_path != second.entry_path
    assert (
        first.entry_path
        == cache_root / "copilot" / "v1" / inspection.snapshot.digest
    )
    assert (
        second.entry_path
        == cache_root / "copilot" / "canonical" / inspection.snapshot.digest
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
