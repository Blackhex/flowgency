from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest

from agency.blueprints.projectors import PROJECTORS, get_projector
from agency.fs.snapshot import (
    SnapshotFile,
    TreeSnapshot,
    compute_source_digest,
)
from agency.integrations import get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedToolPolicy,
)


LIVE_RUNTIME_PROBE_INTEGRATIONS = ("copilot",)


def _real_runtime_probes_enabled() -> bool:
    return os.environ.get("AGENCY_REAL_RUNTIME_PROBES") == "1"


def _tree_bytes(root: Path) -> dict[PurePosixPath, bytes]:
    return {
        PurePosixPath(*path.relative_to(root).parts): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _repository_state(root: Path) -> tuple[bytes, bytes]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return status, diff


def _available_real_executable(integration) -> Path | None:
    detected = integration._find_cmd()
    resolved = integration._resolve_real_cmd(detected)
    executable = Path(resolved)
    if not executable.is_file():
        located = shutil.which(resolved)
        if located is None:
            return None
        executable = Path(located)
    if sys.platform.startswith("win") and executable.suffix.lower() != ".exe":
        return None
    return executable.resolve()


def test_live_probe_cases_cover_every_compatible_projector(pytestconfig):
    compatible = {
        name
        for name, projector in PROJECTORS.items()
        if projector.capabilities.discovers_skills
        and projector.capabilities.activates_selected_skill
    }

    assert set(LIVE_RUNTIME_PROBE_INTEGRATIONS) == compatible


def test_live_probe_marker_and_opt_in_contract(pytestconfig, monkeypatch):
    assert any(
        marker.split(":", 1)[0].strip() == "real_runtime"
        for marker in pytestconfig.getini("markers")
    )
    monkeypatch.delenv("AGENCY_REAL_RUNTIME_PROBES", raising=False)
    assert not _real_runtime_probes_enabled()
    monkeypatch.setenv("AGENCY_REAL_RUNTIME_PROBES", "1")
    assert _real_runtime_probes_enabled()


@pytest.mark.real_runtime
@pytest.mark.parametrize("integration_name", LIVE_RUNTIME_PROBE_INTEGRATIONS)
def test_compatible_projector_discovers_instructions_and_selected_skill(
    tmp_path: Path,
    integration_name: str,
):
    if not _real_runtime_probes_enabled():
        pytest.skip(
            "set AGENCY_REAL_RUNTIME_PROBES=1 to run live runtime probes"
        )

    integration = get_integration(integration_name)
    executable = _available_real_executable(integration)
    if executable is None:
        pytest.skip(f"real {integration_name} executable is unavailable")

    unique = uuid4().hex.upper()
    instruction_token = f"AGENCY_INSTRUCTION_{unique}"
    skill_token = f"AGENCY_SKILL_{unique}"
    instruction_content = (
        "# Runtime release probe\n\n"
        "Obey the task and include this exact instruction token in the "
        "response: "
        f"{instruction_token}\n"
    ).encode()
    skill_content = (
        "---\n"
        "name: runtime-probe\n"
        "description: Return the runtime release probe token when explicitly "
        "invoked.\n"
        "---\n\n"
        "When explicitly invoked, include this exact skill token in the "
        "response: "
        f"{skill_token}\n"
    ).encode()
    files = (
        SnapshotFile(PurePosixPath("AGENTS.md"), instruction_content),
        SnapshotFile(
            PurePosixPath(".agents/skills/runtime-probe/SKILL.md"),
            skill_content,
        ),
    )
    snapshot = TreeSnapshot(files=files, digest=compute_source_digest(files))
    launch_dir = tmp_path / "launch"
    workspace_root = tmp_path / "workspace"
    task_dir = tmp_path / "task"
    launch_dir.mkdir()
    workspace_root.mkdir()
    task_dir.mkdir()

    integration.projector.project(snapshot, launch_dir)
    assert integration.projector.validate_output(snapshot, launch_dir) == ()
    projected_before = _tree_bytes(launch_dir)

    task_file = task_dir / "runtime-probe.md"
    task_file.write_text(
        "Explicitly invoke and use the runtime-probe skill, and obey the "
        "project instructions. Reply with both exact unique tokens from those "
        "sources and minimal extra text. Do not use tools, modify files, or "
        "request secrets.\n",
        encoding="utf-8",
    )
    task_before = task_file.read_bytes()
    repository_root = Path(__file__).resolve().parents[1]
    repository_before = _repository_state(repository_root)
    request = IntegrationRunRequest(
        workspace_root=workspace_root,
        launch_dir=launch_dir,
        task_file=task_file,
        timeout=180,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=180,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tools=ResolvedToolPolicy("all", ()),
        ),
        skill="runtime-probe",
        skill_arguments=(),
        enforce_validation=True,
        memory_working_dir=None,
    )

    result = integration.run(request)

    assert result.exit_code == 0, result.stderr
    assert instruction_token in result.stdout
    assert skill_token in result.stdout
    assert _tree_bytes(launch_dir) == projected_before
    assert list(workspace_root.rglob("*")) == []
    assert task_file.read_bytes() == task_before
    assert _repository_state(repository_root) == repository_before


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
