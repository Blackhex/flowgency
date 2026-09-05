from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowgency.jobs.launch_view import create_launch_view
from flowgency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX


def test_zone_names_are_distinct():
    assert len({ZONE_INSTRUCTIONS, ZONE_OUTBOX, ZONE_MEMORY}) == 3


def test_launch_zones_use_flowgency_directory():
    assert ZONE_OUTBOX == ".flowgency/outbox"
    assert ZONE_MEMORY == ".flowgency/memory"


def test_outbox_and_memory_live_under_the_flowgency_directory():
    assert ZONE_OUTBOX.startswith(".flowgency/")
    assert ZONE_MEMORY.startswith(".flowgency/")


def test_instructions_zone_is_not_under_the_flowgency_directory():
    assert not ZONE_INSTRUCTIONS.startswith(".flowgency")


def test_previous_config_environment_variable_is_ignored(tmp_path, monkeypatch):
    from flowgency.web.dependencies import build_services

    previous_name = "".join(("A", "GENCY_CONFIG"))
    monkeypatch.setenv(previous_name, str(tmp_path / "ignored.yaml"))
    monkeypatch.delenv("FLOWGENCY_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    services = build_services()

    assert services.config_path == (tmp_path / "config.yaml").resolve()


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
