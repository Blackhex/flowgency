from pathlib import Path

import yaml

import flowgency.config as strict_config_module
from flowgency.configuration import ConfigStore
from flowgency.web.state import runtime_team


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


def test_runtime_team_exposes_resolved_agent_instances_without_mutating_raw_input():
    raw_config = {
        "schema_version": 1,

        "flowgency": {
            "title": "Flowgency",
            "default_team": "team",
            "ai_backend": "copilot",
            "agent_library": "/library",
            "compilation_cache": "/cache",
            "memory_store": "/memory",
            "prompt_store": "/prompts",
        },
        "memory": {"channels": {}},
        "teams": {
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
    runtime = runtime_team(snapshot, "team")

    assert raw_config["teams"]["team"]["agents"] == [
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
            "prompts": [],
            "identity": {"display_name": "", "title": "", "emoji": ""},
            "runtime": {"timeout": 1800},
            "permissions": {"mode": "unrestricted", "rules": []},
            "default_memory": None,
            "routines": [],
        }
    ]
    assert runtime["job_paths"] == ()
    assert runtime["dispatch_interval"] == 15
    root = Path("/groups/team").resolve(strict=False)
    assert runtime["workspace_root"] == root
    assert runtime["team_root"] == root
    assert runtime["observations"] == root / "observations"
    assert runtime["proposals"] == root / "proposals"
    assert runtime["decisions"] == root / "decisions"
    assert runtime["locks"] == root / "locks"
    assert runtime["logs"] == root / "logs"
    assert "path" not in runtime
    assert "shared" not in runtime


def _config_with_workflow():
    return {
        "schema_version": 1,
        "flowgency": {
            "title": "Flowgency",
            "default_team": "team",
            "ai_backend": "copilot",
            "agent_library": "/library",
            "compilation_cache": "/cache",
            "memory_store": "/memory",
            "prompt_store": "/prompts",
            "workflow_library": "workflow-library",
        },
        "memory": {"channels": {}},
        "teams": {
            "team": {
                "name": "Team",
                "workspace_path": "/groups/team",
                "path": "/groups/team",
                "default_integration": "copilot",
                "agents": [],
                "workflows": {
                    "workflow-one": {
                        "name": "Delivery",
                        "blueprint": "blueprint-one",
                        "integration": "local",
                        "integration_config": {"root": "tickets"},
                    }
                },
            }
        },
    }


def test_workflow_instance_normalizes_paths_without_mutating_raw():
    raw_config = _config_with_workflow()
    config_path = Path("config.yaml")
    snapshot = ConfigStore(config_path)._snapshot(
        yaml.safe_dump(raw_config, sort_keys=False).encode("utf-8")
    )

    stored = snapshot.raw["teams"]["team"]["workflows"]["workflow-one"]
    assert stored["integration_config"]["root"] == "tickets"
    assert snapshot.raw["flowgency"]["workflow_library"] == "workflow-library"

    workflow = snapshot.config.teams["team"].workflows["workflow-one"]
    assert Path(workflow.integration_config["root"]).is_absolute()
    assert snapshot.config.flowgency.workflow_library.is_absolute()
    assert workflow.context_generation == 0


def test_malformed_workflows_value_is_rejected_not_silently_accepted():
    from flowgency.configuration.models import validate_config

    raw_config = _config_with_workflow()
    raw_config["teams"]["team"]["workflows"] = ["not", "a", "mapping"]
    issues = validate_config(raw_config, Path("config.yaml"))
    assert any(
        issue.field == "teams.team.workflows" and issue.code == "invalid-field-shape"
        for issue in issues
    )
