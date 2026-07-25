from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from agency.blueprints.projectors import get_projector
from agency.fs.snapshot import (
    SnapshotFile,
    TreeSnapshot,
    compute_source_digest,
)
from agency.integrations import REGISTRY
from tests._runtime_probe_helpers import AI_CLI_COMMANDS, LIVE_SCENARIOS
from tests._runtime_probe_helpers import InstalledRuntime, installed_ai_cli_runtimes


def test_live_scenario_contract_covers_all_builtin_ai_clis():
    assert AI_CLI_COMMANDS == {
        "copilot": "copilot",
        "claude-code": "claude",
        "gemini": "gemini",
        "codex": "codex",
        "aider": "aider",
        "goose": "goose",
        "opencode": "opencode",
        "pi": "pi",
    }
    assert LIVE_SCENARIOS == (
        "basic",
        "root-instructions",
        "selected-skill",
        "write-boundary",
    )
    assert {name: REGISTRY[name].cli_command for name in AI_CLI_COMMANDS} == AI_CLI_COMMANDS


def test_installed_runtime_collection_omits_unavailable_clis(monkeypatch):
    monkeypatch.setattr(
        REGISTRY["copilot"],
        "resolve_executable",
        lambda: "C:/bin/copilot.exe",
    )
    for name in AI_CLI_COMMANDS.keys() - {"copilot"}:
        monkeypatch.setattr(REGISTRY[name], "resolve_executable", lambda: None)

    assert installed_ai_cli_runtimes() == (
        InstalledRuntime("copilot", "C:/bin/copilot.exe"),
    )


@pytest.fixture
def blueprint_snapshot() -> TreeSnapshot:
    files = (
        SnapshotFile(PurePosixPath("AGENTS.md"), b"# Shared instructions\n"),
        SnapshotFile(
            PurePosixPath(".agents/skills/daily-review/SKILL.md"),
            b"---\nname: daily-review\n---\nreview\n",
        ),
        SnapshotFile(
            PurePosixPath(".agents/skills/daily-review/prompt.txt"),
            b"prompt body\n",
        ),
        SnapshotFile(PurePosixPath("notes/ignored.md"), b"keep source only\n"),
    )
    return TreeSnapshot(files=files, digest=compute_source_digest(files))


@pytest.mark.parametrize(
    ("integration", "instruction", "skills"),
    [
        ("copilot", "AGENTS.md", ".agents/skills"),
        ("claude-code", "CLAUDE.md", ".claude/skills"),
        ("gemini", "GEMINI.md", ".agents/skills"),
    ],
)
def test_projector_relocates_without_rewriting(
    blueprint_snapshot: TreeSnapshot,
    tmp_path,
    integration: str,
    instruction: str,
    skills: str,
):
    projector = get_projector(integration)

    # Root instruction discovery must be explicit for AI CLI projectors
    assert projector.capabilities.discovers_instructions is True

    projector.project(blueprint_snapshot, tmp_path)

    assert (
        (tmp_path / instruction).read_bytes()
        == blueprint_snapshot.file("AGENTS.md").content
    )
    assert (
        tmp_path / skills / "daily-review" / "SKILL.md"
    ).read_bytes() == blueprint_snapshot.file(
        ".agents/skills/daily-review/SKILL.md"
    ).content
    assert (
        tmp_path / skills / "daily-review" / "prompt.txt"
    ).read_bytes() == blueprint_snapshot.file(
        ".agents/skills/daily-review/prompt.txt"
    ).content


def test_projector_validation_rejects_missing_and_extra_projection_paths(
    blueprint_snapshot: TreeSnapshot, tmp_path
):
    projector = get_projector("copilot")
    projector.project(blueprint_snapshot, tmp_path)
    (tmp_path / ".agents" / "skills" / "daily-review" / "SKILL.md").unlink()
    (tmp_path / ".agents" / "skills" / "unexpected.txt").write_text(
        "extra", encoding="utf-8"
    )

    issues = projector.validate_output(blueprint_snapshot, tmp_path)

    assert {issue.code for issue in issues} == {
        "projector-missing-path",
        "projector-unexpected-path",
    }
