import pytest
from pathlib import Path


@pytest.fixture
def tmp_agent_dir(tmp_path):
    """Create a temporary agent directory."""
    agent_dir = tmp_path / "test-agent"
    agent_dir.mkdir()
    return agent_dir


@pytest.fixture
def tmp_group_path(tmp_path):
    """Create a temporary group directory with shared/ structure."""
    group = tmp_path / "group"
    group.mkdir()
    (group / "shared" / "clues").mkdir(parents=True)
    (group / "shared" / "curiosities").mkdir(parents=True)
    (group / "shared" / "decisions").mkdir(parents=True)
    (group / "shared" / "prompts").mkdir(parents=True)
    (group / "shared" / "logs").mkdir(parents=True)
    (group / "shared" / "memory.md").write_text("# Shared Memory\n")
    return group
