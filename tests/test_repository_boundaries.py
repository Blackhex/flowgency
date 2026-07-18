from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).parents[1]
PROHIBITED_TERMS = (
    "".join(("v", "2")),
    "".join(("leg", "acy")),
)
COORDINATION_PATHS = (
    ":(exclude)docs/superpowers/plans/2026-07-18-*.md",
    ":(exclude)docs/superpowers/specs/2026-07-18-first-run-setup-launcher-design.md",
)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.mark.parametrize("term", PROHIBITED_TERMS)
def test_tracked_tree_omits_prohibited_terms(repo_root: Path, term: str):
    completed = subprocess.run(
        ["git", "grep", "-Iil", term, "--", ".", *COORDINATION_PATHS],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout


def test_tracked_paths_omit_prohibited_terms(repo_root: Path):
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_paths = [
        path
        for path in completed.stdout.splitlines()
        if not path.startswith("docs/superpowers/plans/2026-07-18-")
        and path
        != "docs/superpowers/specs/2026-07-18-first-run-setup-launcher-design.md"
    ]
    lowered = "\n".join(tracked_paths).lower()
    for term in PROHIBITED_TERMS:
        assert term not in lowered
