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
    (group / "shared" / "observations").mkdir(parents=True)
    (group / "shared" / "proposals").mkdir(parents=True)
    (group / "shared" / "decisions").mkdir(parents=True)
    (group / "shared" / "prompts").mkdir(parents=True)
    (group / "shared" / "logs").mkdir(parents=True)
    (group / "shared" / "memory.md").write_text("# Shared Memory\n")
    return group


@pytest.fixture
def canonical_paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    agent_library = tmp_path / "agent-library"
    group_path = tmp_path / "agents" / "newsletter"
    agent_library.mkdir(parents=True, exist_ok=True)
    group_path.mkdir(parents=True, exist_ok=True)
    return {
        "config_path": config_path,
        "config_dir": config_path.parent,
        "agent_library": agent_library,
        "group_path": group_path,
        "compilation_cache": tmp_path / "compiled-agents",
        "memory_store": tmp_path / "memory",
    }


@pytest.fixture
def canonical_raw_config(canonical_paths):
    return {
        "schema_version": 2,
        "agency": {
            "title": "Agency",
            "default_group": "newsletter",
            "ai_backend": "claude-code",
            "agent_library": str(canonical_paths["agent_library"]),
            "compilation_cache": str(canonical_paths["compilation_cache"]),
            "memory_store": str(canonical_paths["memory_store"]),
        },
        "memory": {
            "channels": {
                "support": {"display_name": "Support"},
            },
        },
        "groups": {
            "newsletter": {
                "name": "Newsletter",
                "path": str(canonical_paths["group_path"]),
                "default_integration": "claude-code",
                "agents": [
                    {
                        "name": "builder",
                        "blueprint": "builder-blueprint",
                        "integration": "claude-code",
                        "routines": [
                            {
                                "id": "daily-review",
                                "skill": "daily-review",
                                "schedule": {"at": "09:00"},
                                "memory": {"scope": "routine"},
                            }
                        ],
                    }
                ],
                "workspaces": [
                    {
                        "name": "Terminal Grid",
                        "type": "tmux",
                        "config": {"script_path": "tmux-agents.sh"},
                    }
                ],
            }
        },
    }
