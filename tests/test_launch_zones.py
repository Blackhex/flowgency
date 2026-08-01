from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agency.jobs.launch_view import create_launch_view
from agency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX


def test_zone_names_are_distinct():
    assert len({ZONE_INSTRUCTIONS, ZONE_OUTBOX, ZONE_MEMORY}) == 3


def test_outbox_and_memory_live_under_the_agency_directory():
    assert ZONE_OUTBOX.startswith(".agency/")
    assert ZONE_MEMORY.startswith(".agency/")


def test_instructions_zone_is_not_under_the_agency_directory():
    assert not ZONE_INSTRUCTIONS.startswith(".agency")


def _artifact(tmp_path: Path):
    entry = tmp_path / "entry"
    runtime = entry / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    (runtime / ".agents").mkdir()
    (runtime / ".agents" / "skill.md").write_text("skill\n", encoding="utf-8")
    return SimpleNamespace(entry_path=entry, runtime_path=runtime)


def test_projected_runtime_lands_under_the_instructions_zone(tmp_path: Path):
    launch = create_launch_view(_artifact(tmp_path), tmp_path / "launch")

    assert (launch / ZONE_INSTRUCTIONS / "AGENTS.md").is_file()
    assert (launch / ZONE_INSTRUCTIONS / ".agents" / "skill.md").is_file()
    assert not (launch / "AGENTS.md").exists()


def test_zone_directories_are_created(tmp_path: Path):
    launch = create_launch_view(_artifact(tmp_path), tmp_path / "launch")

    assert launch.joinpath(*ZONE_OUTBOX.split("/")).is_dir()
    assert launch.joinpath(*ZONE_MEMORY.split("/")).is_dir()
