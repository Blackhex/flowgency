from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from agency.setup_assets import copilot_discovery_root


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = (
    REPO_ROOT
    / "agency"
    / "setup_assets"
    / "copilot"
    / ".github"
    / "skills"
    / "agency-setup"
)


def test_copilot_discovery_root_is_package_owned():
    assert copilot_discovery_root() == (
        REPO_ROOT / "agency" / "setup_assets" / "copilot"
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
    wheels = list(tmp_path.glob("christag_agency-*.whl"))
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
