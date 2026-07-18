from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_removed_conversion_surfaces_do_not_exist(repo_root: Path):
    removed = [
        repo_root / "agency" / "configuration" / "compat.py",
        repo_root / "tools" / "migrate_agent_model.py",
        repo_root / "skills" / "agency-migration",
        repo_root / ".github" / "skills" / "agency-migration",
    ]
    assert not any(path.exists() for path in removed)
