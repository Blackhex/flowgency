from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from agency.blueprints.projectors import get_projector
from agency.fs.snapshot import SnapshotFile, TreeSnapshot, compute_source_digest


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

    projector.project(blueprint_snapshot, tmp_path)

    assert (tmp_path / instruction).read_bytes() == blueprint_snapshot.file("AGENTS.md").content
    assert (
        tmp_path / skills / "daily-review" / "SKILL.md"
    ).read_bytes() == blueprint_snapshot.file(".agents/skills/daily-review/SKILL.md").content
    assert (
        tmp_path / skills / "daily-review" / "prompt.txt"
    ).read_bytes() == blueprint_snapshot.file(".agents/skills/daily-review/prompt.txt").content


def test_projector_validation_rejects_missing_and_extra_projection_paths(
    blueprint_snapshot: TreeSnapshot, tmp_path
):
    projector = get_projector("copilot")
    projector.project(blueprint_snapshot, tmp_path)
    (tmp_path / ".agents" / "skills" / "daily-review" / "SKILL.md").unlink()
    (tmp_path / ".agents" / "skills" / "unexpected.txt").write_text("extra", encoding="utf-8")

    issues = projector.validate_output(blueprint_snapshot, tmp_path)

    assert {issue.code for issue in issues} == {
        "projector-missing-path",
        "projector-unexpected-path",
    }
