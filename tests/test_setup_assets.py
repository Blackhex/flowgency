from __future__ import annotations

from email import policy as email_policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from zipfile import ZipFile

from packaging.requirements import Requirement
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


def test_the_wheel_declares_the_diff_parser_dependency(built_wheel: Path):
    """Importing unidiff in this checkout's venv would pass even if the wheel
    stopped declaring it, so read the built metadata itself."""
    with ZipFile(built_wheel) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_names) == 1, metadata_names
        metadata = BytesParser(policy=email_policy.compat32).parsebytes(
            archive.read(metadata_names[0])
        )

    requirements = {
        Requirement(value) for value in metadata.get_all("Requires-Dist", [])
    }
    unidiff = {
        requirement for requirement in requirements if requirement.name == "unidiff"
    }
    assert len(unidiff) == 1, sorted(str(item) for item in requirements)
    requirement = unidiff.pop()
    assert requirement.marker is None
    assert {(item.operator, item.version) for item in requirement.specifier} == {
        (">=", "0.7.5"),
        ("<", "0.8"),
    }


def _read_only_git_object(runtime: Path) -> Path:
    """A runtime holding the read-only object file Git leaves behind."""
    objects = runtime / "workspaces" / "newsletter" / ".git" / "objects" / "ab"
    objects.mkdir(parents=True)
    blob = objects / "cdef1234"
    blob.write_bytes(b"object")
    blob.chmod(stat.S_IREAD)
    return blob


def test_the_ui_runtime_removal_deletes_read_only_git_objects(tmp_path: Path):
    from tests.ui.server import _rmtree_including_read_only

    runtime = tmp_path / "current"
    _read_only_git_object(runtime)

    _rmtree_including_read_only(runtime)

    assert not runtime.exists()


def test_the_ui_runtime_removal_uses_an_api_python_311_supports(tmp_path: Path):
    """``shutil.rmtree`` only grew ``onexc`` in 3.12, and this package supports
    3.11, so the removal must call the keyword that interpreter accepts."""
    from tests.ui.server import _rmtree_including_read_only

    runtime = tmp_path / "current"
    blob = _read_only_git_object(runtime)
    handlers: list[object] = []

    def rmtree_as_python_311(path, ignore_errors=False, onerror=None, *, dir_fd=None):
        # CPython 3.11's exact signature: an ``onexc`` keyword is a TypeError.
        handlers.append(onerror)
        assert onerror is not None, "3.11 removal was given no read-only handler"
        onerror(os.unlink, str(blob), (PermissionError, PermissionError(), None))
        shutil.rmtree(path, ignore_errors=True)

    _rmtree_including_read_only(runtime, rmtree=rmtree_as_python_311)

    assert handlers, "the removal never called the interpreter's rmtree"
    assert not runtime.exists()


_ROOT_RETENTION_SCRIPT = """
import json, shutil, sys
from pathlib import Path

sys.path.insert(0, {repo!r})
import tests.ui.server as ui

runtime = Path(sys.argv[1])
delivery = runtime / "tickets" / "delivery"
research = runtime / "tickets" / "research"
seeded = delivery / "newsletter" / "delivery" / "tickets" / "FG-1.json"
seeded.parent.mkdir(parents=True)
seeded.write_text("{{}}", encoding="utf-8")
research.mkdir(parents=True)
(research / "keep.json").write_text("{{}}", encoding="utf-8")
stale = runtime / "tickets" / "stale"
stale.mkdir()
(stale / "leftover.json").write_text("{{}}", encoding="utf-8")

config = {{
    "teams": {{
        "newsletter": {{
            "workflows": {{
                "delivery": {{"integration_config": {{"root": delivery.as_posix()}}}},
                "research-workflow": {{"integration_config": {{"root": research.as_posix()}}}},
            }}
        }}
    }}
}}

observations = []


def observe():
    observations.append([delivery.is_dir(), research.is_dir()])


real_rmtree = shutil.rmtree


def watched_rmtree(path, *args, **kwargs):
    observe()
    return real_rmtree(path, *args, **kwargs)


real_unlink = Path.unlink


def watched_unlink(self, *args, **kwargs):
    observe()
    return real_unlink(self, *args, **kwargs)


shutil.rmtree = watched_rmtree
Path.unlink = watched_unlink
try:
    ui._clear_workflow_roots(runtime, config)
finally:
    shutil.rmtree = real_rmtree
    Path.unlink = real_unlink

print(json.dumps({{
    "observations": observations,
    "delivery": delivery.is_dir(),
    "research": research.is_dir(),
    "seeded": seeded.exists(),
    "stale": stale.exists(),
}}))
"""


def test_the_reset_keeps_every_configured_workflow_root_while_it_clears(tmp_path: Path):
    """A board request racing a reset revalidates its configured root, so that
    directory must never be absent — not even between two removals."""
    runtime = tmp_path / "current"
    runtime.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _ROOT_RETENTION_SCRIPT.format(repo=str(REPO_ROOT)),
            str(runtime),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    observed = json.loads(result.stdout)
    assert observed["observations"], "nothing was removed, so nothing was observed"
    assert all(observed["observations"]), observed["observations"]
    assert observed["delivery"] is True
    assert observed["research"] is True
    assert observed["seeded"] is False
    assert observed["stale"] is False


_RESET_ORDER_SCRIPT = """
import json, os, sys
from pathlib import Path

sys.path.insert(0, {repo!r})
runtime = Path(sys.argv[1])
os.environ["FLOWGENCY_UI_RUNTIME"] = str(runtime)

import tests.ui.server as ui

calls = []
ui._reset_runtime_state = lambda *args, **kwargs: calls.append(kwargs)
ui._install_ui_test_runtime()

from fastapi.testclient import TestClient
from flowgency.app import app

with TestClient(app) as client:
    rejected = client.post("/__ui/reset", json={{"fixture": "not-a-fixture"}})

print(json.dumps({{"status": rejected.status_code, "calls": len(calls)}}))
"""


def test_an_unsupported_fixture_is_rejected_before_any_reset_mutation(tmp_path: Path):
    runtime = tmp_path / "current"
    runtime.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _RESET_ORDER_SCRIPT.format(repo=str(REPO_ROOT)),
            str(runtime),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": 400, "calls": 0}


