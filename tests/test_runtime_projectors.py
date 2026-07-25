from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from agency.blueprints.projectors import get_projector
from agency.integrations import RunResult
from agency.fs.snapshot import (
    SnapshotFile,
    TreeSnapshot,
    compute_source_digest,
)
from agency.integrations import REGISTRY
from tests._runtime_probe_helpers import (
    AI_CLI_COMMANDS,
    LIVE_SCENARIOS,
    InstalledRuntime,
    assert_live_success,
    assert_projection_valid,
    assert_protected_state_unchanged,
    capture_protected_state,
    create_probe_directories,
    installed_ai_cli_runtimes,
    selected_skill_supported,
    write_boundary_supported,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


def test_all_builtin_ai_clis_have_four_capability_aware_scenarios():
    for name in AI_CLI_COMMANDS:
        integration = REGISTRY[name]
        assert integration.projector.capabilities.discovers_instructions is True
        assert isinstance(selected_skill_supported(integration), bool)
        assert isinstance(write_boundary_supported(integration), bool)
    assert LIVE_SCENARIOS == (
        "basic",
        "root-instructions",
        "selected-skill",
        "write-boundary",
    )


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


def test_live_success_failure_uses_full_runtime_label():
    runtime = InstalledRuntime("copilot", "C:/bin/copilot.exe")
    result = RunResult(
        exit_code=1,
        stdout="",
        stderr="boom",
        duration_seconds=0.1,
    )

    with pytest.raises(AssertionError, match=r"copilot/basic \(C:/bin/copilot\.exe\)"):
        assert_live_success(result, runtime, "basic", "AGENCY_TOKEN")


def test_projection_validation_failure_reports_full_label_and_issues(
    blueprint_snapshot: TreeSnapshot, tmp_path
):
    runtime = InstalledRuntime("copilot", "C:/bin/copilot.exe")
    projector = get_projector("copilot")
    projector.project(blueprint_snapshot, tmp_path)
    (tmp_path / ".agents" / "skills" / "daily-review" / "SKILL.md").unlink()
    (tmp_path / ".agents" / "skills" / "unexpected.txt").write_text(
        "extra", encoding="utf-8"
    )

    with pytest.raises(AssertionError) as excinfo:
        assert_projection_valid(projector, blueprint_snapshot, tmp_path, runtime, "basic")

    assert "copilot/basic (C:/bin/copilot.exe)" in str(excinfo.value)
    assert "projector-missing-path" in str(excinfo.value)
    assert "projector-unexpected-path" in str(excinfo.value)


def test_protected_state_failure_uses_full_runtime_label(tmp_path):
    runtime = InstalledRuntime("copilot", "C:/bin/copilot.exe")
    launch_dir, workspace_root, task_dir = create_probe_directories(tmp_path)
    projected_file = launch_dir / "AGENTS.md"
    projected_file.write_text("instructions\n", encoding="utf-8")
    workspace_file = workspace_root / "notes.txt"
    workspace_file.write_text("before\n", encoding="utf-8")
    task_file = task_dir / "task.md"
    task_file.write_text("task\n", encoding="utf-8")
    before = capture_protected_state(
        launch_dir,
        workspace_root,
        task_file,
        REPOSITORY_ROOT,
    )
    workspace_file.write_text("after\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=r"copilot/root-instructions \(C:/bin/copilot\.exe\): workspace changed"):
        assert_protected_state_unchanged(
            before,
            launch_dir,
            workspace_root,
            task_file,
            REPOSITORY_ROOT,
            runtime=runtime,
            scenario="root-instructions",
        )
