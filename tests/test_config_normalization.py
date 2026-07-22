from pathlib import Path

import yaml

import agency.config as strict_config_module
from agency.configuration import ConfigStore
from agency.web.state import runtime_group


def test_runtime_config_module_exposes_no_retired_agent_helper_surface():
    for name in (
        "agent_can_write",
        "agent_names",
        "get_agent_dir",
        "normalize_agents",
        "SandboxSpec",
        "save_config_path",
    ):
        assert not hasattr(strict_config_module, name)


def test_runtime_group_exposes_resolved_agent_instances_without_mutating_raw_input():
    raw_config = {
        "schema_version": 3,

        "agency": {
            "title": "Agency",
            "default_group": "team",
            "ai_backend": "copilot",
            "agent_library": "/library",
            "compilation_cache": "/cache",
            "memory_store": "/memory",
        },
        "memory": {"channels": {}},
        "groups": {
            "team": {
                "name": "Team",
                "workspace_path": "/groups/team",
                "path": "/groups/team",
                "default_integration": "copilot",
                "agents": [
                    {
                        "name": "builder",
                        "blueprint": "builder",
                        "integration": "copilot",
                        "integration_config": {"model": "gpt-5"},
                    }
                ],
            }
        },
    }

    config_path = Path("config.yaml")
    snapshot = ConfigStore(config_path)._snapshot(
        yaml.safe_dump(raw_config, sort_keys=False).encode("utf-8")
    )
    runtime = runtime_group(snapshot, "team")

    assert raw_config["groups"]["team"]["agents"] == [
        {
            "name": "builder",
            "blueprint": "builder",
            "integration": "copilot",
            "integration_config": {"model": "gpt-5"},
        }
    ]
    assert runtime["agents"] == ["builder"]
    assert runtime["agents_full"] == [
        {
            "name": "builder",
            "blueprint": "builder",
            "integration": "copilot",
            "integration_config": {"model": "gpt-5"},
            "identity": {"display_name": "", "title": "", "emoji": ""},
            "capabilities": {"write": False},
            "runtime": {
                "timeout": 1800,
                "sandbox": {"mode": "unrestricted", "additional_roots": []},
                "tools": {"mode": "all", "names": []},
            },
            "default_memory": None,
            "routines": [],
        }
    ]
    assert runtime["job_paths"] == ()
