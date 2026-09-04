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
    team_path = tmp_path / "teams" / "newsletter"
    agent_library.mkdir(parents=True)
    workspace_path.mkdir()
    return {
        "config_path": config_path,
        "config_dir": config_path.parent,
        "agent_library": agent_library,
        "workspace_path": workspace_path,
        "team_path": team_path,
        "compilation_cache": tmp_path / "compiled-agents",
        "memory_store": tmp_path / "memory",
        "prompt_store": tmp_path / "prompts",
    }


@pytest.fixture
def raw_config(config_paths):
    return {
        "schema_version": 6,
        "agency": {
            "title": "Agency",
            "default_team": "newsletter",
            "ai_backend": "claude-code",
            "agent_library": str(config_paths["agent_library"]),
            "compilation_cache": str(config_paths["compilation_cache"]),
            "memory_store": str(config_paths["memory_store"]),
            "prompt_store": str(config_paths["prompt_store"]),
        },
        "memory": {
            "channels": {
                "support": {"display_name": "Support"},
            },
        },
        "teams": {
            "newsletter": {
                "name": "Newsletter",
                "workspace_path": str(config_paths["workspace_path"]),
                "path": str(config_paths["team_path"]),
                "default_integration": "claude-code",
                "runtime": {
                    "permissions": {
                        "mode": "unrestricted",
                        "rules": [{"tools": None}],
                    },
                },
                "agents": [
                    {
                        "name": "builder",
                        "blueprint": "builder-blueprint",
                        "integration": "claude-code",
                        "prompts": [],
                        "routines": [
                            {
                                "id": "daily-review",
                                "prompt": {"scope": "blueprint", "name": "daily-review"},
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
