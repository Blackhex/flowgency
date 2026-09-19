from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from flowgency.setup_assets import copilot_discovery_root


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = (
    REPO_ROOT
    / "flowgency"
    / "setup_assets"
    / "copilot"
    / ".github"
    / "skills"
    / "flowgency-setup"
)

# Every module and asset the Git-evidence feature added. A packaged deployment
# that misses one of these serves a broken viewer instead of failing to start.
GIT_EVIDENCE_PACKAGE_FILES = (
    "flowgency/git_evidence/__init__.py",
    "flowgency/git_evidence/capture.py",
    "flowgency/git_evidence/git.py",
    "flowgency/git_evidence/models.py",
    "flowgency/git_evidence/publication.py",
    "flowgency/tickets/git_evidence.py",
    "flowgency/web/git_evidence.py",
    "flowgency/web/routes/git_evidence.py",
    "flowgency/templates/git_evidence.html",
    "flowgency/static/git-evidence.css",
)


def _build_wheel(destination: Path) -> Path:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(destination),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(destination.glob("flowgency-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    return _build_wheel(tmp_path_factory.mktemp("wheel"))


def test_copilot_discovery_root_is_package_owned():
    assert copilot_discovery_root() == (
        REPO_ROOT / "flowgency" / "setup_assets" / "copilot"
    ).resolve()


def test_wheel_contains_every_canonical_setup_skill_file(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("flowgency-*.whl"))
    assert len(wheels) == 1

    expected = {
        path.relative_to(REPO_ROOT).as_posix(): path.read_bytes()
        for path in CANONICAL_SKILL_DIR.rglob("*")
        if path.is_file()
    }
    assert expected

    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert set(expected) <= names
        for name, content in expected.items():
            assert archive.read(name) == content


def test_wheel_contains_shipped_workflow_examples(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("flowgency-*.whl"))
    assert len(wheels) == 1

    expected_paths = {
        "flowgency/setup_assets/workflows/software-delivery/workflow.yaml",
        "flowgency/setup_assets/workflows/research/workflow.yaml",
    }
    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert expected_paths <= names


def test_wheel_contains_every_git_evidence_module_and_asset(built_wheel: Path):
    with ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
        assert set(GIT_EVIDENCE_PACKAGE_FILES) <= names
        for name in GIT_EVIDENCE_PACKAGE_FILES:
            assert archive.read(name) == (REPO_ROOT / name).read_bytes()


def test_git_evidence_modules_import_from_the_wheel_not_the_checkout(
    built_wheel: Path, tmp_path: Path
):
    """An installed deployment must resolve these modules from its own files."""
    extracted = tmp_path / "site-packages"
    with ZipFile(built_wheel) as archive:
        archive.extractall(extracted)
    script = (
        "import json\n"
        "import flowgency.git_evidence.capture as capture\n"
        "import flowgency.tickets.git_evidence as retained\n"
        "import flowgency.web.git_evidence as viewer\n"
        "import flowgency.web.routes.git_evidence as routes\n"
        "print(json.dumps([capture.__file__, retained.__file__, viewer.__file__, routes.__file__]))\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(extracted)
    # ``-P`` keeps the current directory off sys.path, so only PYTHONPATH and
    # the interpreter's own site packages can answer the imports.
    result = subprocess.run(
        [sys.executable, "-P", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    resolved = [Path(item) for item in json.loads(result.stdout)]
    assert len(resolved) == 4
    for module_path in resolved:
        assert module_path.is_relative_to(extracted), module_path
        assert not module_path.is_relative_to(REPO_ROOT / "flowgency"), module_path
    assert (extracted / "flowgency" / "templates" / "git_evidence.html").is_file()
    assert (extracted / "flowgency" / "static" / "git-evidence.css").is_file()


def test_the_ui_test_runtime_installs_only_inside_its_own_interpreter(tmp_path: Path):
    """The browser fixture's runtime is global, so it must never be installed
    by the shared pytest process it would otherwise contaminate."""
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from flowgency.integrations import REGISTRY\n"
        "from tests.ui.server import _install_ui_test_runtime\n"
        "before = 'ticket-test' in REGISTRY\n"
        "_install_ui_test_runtime()\n"
        "print(json.dumps({'before': before, 'after': 'ticket-test' in REGISTRY}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"before": False, "after": True}

    from flowgency.integrations import REGISTRY
    from flowgency.jobs import submission

    assert "ticket-test" not in REGISTRY
    assert submission.submit_job_request.__module__.startswith("flowgency.")
