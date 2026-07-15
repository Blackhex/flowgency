from __future__ import annotations

import json
from multiprocessing import Event, Process, Queue
from pathlib import Path, PurePosixPath

from agency.blueprints import BlueprintInspection
from agency.blueprints.projectors import StaticRuntimeProjector
from agency.fs.snapshot import capture_tree
from agency.integrations.models import ProjectorCapabilities


def _write_blueprint(root: Path, key: str = "advisor") -> Path:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")
    (skill / "SKILL.md").write_bytes(
        (
            b"---\nname: daily-review\n"
            b"description: Review daily editorial work.\n"
            b"---\n\nRun the review.\n"
        ),
    )
    return blueprint


def _projector() -> StaticRuntimeProjector:
    return StaticRuntimeProjector(
        version="v-lock",
        capabilities=ProjectorCapabilities(
            instruction_target=PurePosixPath("AGENTS.md"),
            skills_target=PurePosixPath(".agents/skills"),
            discovers_skills=True,
            activates_selected_skill=True,
        ),
    )


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


def _compile_once(
    cache_root: str,
    source_root: str,
    queue: Queue,
    acquired: Event,
    release: Event,
) -> None:
    from agency.blueprints.cache import CompilationCache

    class BlockingProjector(StaticRuntimeProjector):
        def project(self, source, destination):
            acquired.set()
            release.wait(5)
            return super().project(source, destination)

    inspection = _inspection(Path(source_root))
    cache = CompilationCache(
        Path(cache_root),
        {
            "copilot": BlockingProjector(
                version="v-lock",
                capabilities=ProjectorCapabilities(
                    instruction_target=PurePosixPath("AGENTS.md"),
                    skills_target=PurePosixPath(".agents/skills"),
                    discovers_skills=True,
                    activates_selected_skill=True,
                ),
            )
        },
    )
    artifact = cache.ensure_compiled("copilot", inspection)
    queue.put(str(artifact.entry_path))


def test_two_process_cache_miss_builds_one_artifact(tmp_path: Path):
    cache_root = tmp_path / "compiled-agents"
    source_root = tmp_path / "source"
    _write_blueprint(source_root)

    acquired = Event()
    release = Event()
    queue: Queue[str] = Queue()

    first = Process(
        target=_compile_once,
        args=(str(cache_root), str(source_root), queue, acquired, release),
    )
    second = Process(
        target=_compile_once,
        args=(str(cache_root), str(source_root), queue, Event(), Event()),
    )

    first.start()
    assert acquired.wait(5)
    second.start()
    release.set()

    first.join(10)
    second.join(10)

    assert first.exitcode == 0
    assert second.exitcode == 0

    path_one = Path(queue.get(timeout=1))
    path_two = Path(queue.get(timeout=1))
    assert path_one == path_two
    assert len(list((cache_root / "copilot" / "v-lock").iterdir())) == 1
    manifest = json.loads(
        (path_one / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["ref"] == {
        "integration": "copilot",
        "projector_version": "v-lock",
        "source_digest": manifest["ref"]["source_digest"],
    }
