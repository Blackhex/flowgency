import pytest
from pathlib import Path


@pytest.fixture
def tmp_agent_dir(tmp_path):
    """Create a temporary agent directory."""
    agent_dir = tmp_path / "test-agent"
    agent_dir.mkdir()
    return agent_dir


@pytest.fixture
def config_paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    agent_library = tmp_path / "agent-library"
    workspace_path = tmp_path / "workspace"
    group_path = tmp_path / "groups" / "newsletter"
    agent_library.mkdir(parents=True)
    workspace_path.mkdir()
    return {
        "config_path": config_path,
        "config_dir": config_path.parent,
        "agent_library": agent_library,
        "workspace_path": workspace_path,
        "group_path": group_path,
        "compilation_cache": tmp_path / "compiled-agents",
        "memory_store": tmp_path / "memory",
    }


@pytest.fixture
def raw_config(config_paths):
    return {
        "schema_version": 3,
        "agency": {
            "title": "Agency",
            "default_group": "newsletter",
            "ai_backend": "claude-code",
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
        },
        "memory": {
            "channels": {
                "support": {"display_name": "Support"},
            },
        },
        "groups": {
            "newsletter": {
                "name": "Newsletter",
                "workspace_path": str(config_paths["workspace_path"]),
                "path": str(config_paths["group_path"]),
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
