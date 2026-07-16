import pytest
from pathlib import Path

from agency.config import agent_can_write


def test_normalize_bare_string():
    from agency.config import normalize_agents
    result = normalize_agents(["product", "editorial"], "claude-code")
    assert result == [
        {"name": "product", "integration": "claude-code"},
        {"name": "editorial", "integration": "claude-code"},
    ]


def test_normalize_dict_form():
    from agency.config import normalize_agents
    result = normalize_agents(
        [{"name": "bot", "integration": "script", "integration_config": {"command": "./run.sh"}}],
        "claude-code",
    )
    assert result[0]["name"] == "bot"
    assert result[0]["integration"] == "script"
    assert result[0]["integration_config"]["command"] == "./run.sh"


def test_normalize_mixed():
    from agency.config import normalize_agents
    result = normalize_agents(
        ["product", {"name": "bot", "integration": "codex"}],
        "claude-code",
    )
    assert result[0] == {"name": "product", "integration": "claude-code"}
    assert result[1]["name"] == "bot"
    assert result[1]["integration"] == "codex"


def test_normalize_inherits_group_default():
    from agency.config import normalize_agents
    result = normalize_agents(["agent1"], "codex")
    assert result[0]["integration"] == "codex"


def test_normalize_missing_integration_defaults_claude_code():
    from agency.config import normalize_agents
    result = normalize_agents([{"name": "bot"}], "claude-code")
    assert result[0]["integration"] == "claude-code"


def test_agent_names_helper():
    from agency.config import agent_names
    agents = [
        {"name": "product", "integration": "claude-code"},
        {"name": "bot", "integration": "script"},
    ]
    assert agent_names(agents) == ["product", "bot"]


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ({"name": "missing"}, False),
        ({"name": "empty", "capabilities": {}}, False),
        ({"name": "false", "capabilities": {"write": False}}, False),
        ({"name": "string", "capabilities": {"write": "true"}}, False),
        ({"name": "writer", "capabilities": {"write": True}}, True),
    ],
)
def test_agent_can_write_is_explicit_and_fail_closed(agent, expected):
    assert agent_can_write([agent], agent["name"]) is expected


def test_agent_can_write_returns_false_for_unknown_agent():
    assert agent_can_write([{"name": "builder", "capabilities": {"write": True}}], "missing") is False


def test_runtime_groups_preserve_raw_agent_entries():
    from agency.configuration import ConfigStore
    from agency.web.state import runtime_group

    raw_config = {
        "schema_version": 2,
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


def test_reload_then_save_preserves_explicit_agent_config(tmp_path, monkeypatch):
    from agency import app as app_module

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "agency": {
                    "title": "Agency",
                    "default_group": "team",
                    "ai_backend": "copilot",
                    "agent_library": str(tmp_path / "library"),
                    "compilation_cache": str(tmp_path / "cache"),
                    "memory_store": str(tmp_path / "memory"),
                },
                "memory": {"channels": {}},
                "groups": {
                    "team": {
                        "name": "Team",
                        "path": str(tmp_path / "agents"),
                        "default_integration": "copilot",
                        "agents": [
                            {
                                "name": "builder",
                                "blueprint": "builder",
                                "integration": "copilot",
                            },
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", config_path)
    app_module.refresh_services()

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["groups"]["team"]["agents"] == [
        {
            "name": "builder",
            "blueprint": "builder",
            "integration": "copilot",
        },
    ]
    assert app_module.get_group("team")["agents"] == ["builder"]


# Task 1: Regression tests for path preservation in normalize_agents

def test_normalize_preserves_path():
    from agency.config import normalize_agents
    result = normalize_agents(
        [{"name": "pm", "path": "/shared/agents/pm"}],
        "claude-code",
    )
    assert result[0]["path"] == "/shared/agents/pm"
    assert result[0]["integration"] == "claude-code"


def test_normalize_bare_string_has_no_path():
    from agency.config import normalize_agents
    result = normalize_agents(["product"], "claude-code")
    assert "path" not in result[0]


# Task 2: Tests for get_agent_dir()

def test_get_agent_dir_with_path_override(tmp_path):
    from agency.config import get_agent_dir
    external = tmp_path / "external" / "pm"
    external.mkdir(parents=True)
    g = {
        "path": tmp_path / "group",
        "agents_full": [{"name": "pm", "path": str(external), "integration": "claude-code"}],
    }
    assert get_agent_dir(g, "pm") == external


def test_get_agent_dir_without_path_override(tmp_path):
    from agency.config import get_agent_dir
    group_path = tmp_path / "group"
    g = {
        "path": group_path,
        "agents_full": [{"name": "product", "integration": "claude-code"}],
    }
    assert get_agent_dir(g, "product") == group_path / "product"


def test_get_agent_dir_unknown_agent(tmp_path):
    from agency.config import get_agent_dir
    group_path = tmp_path / "group"
    g = {
        "path": group_path,
        "agents_full": [],
    }
    assert get_agent_dir(g, "unknown") == group_path / "unknown"


def test_get_agent_dir_rejects_relative_path(tmp_path):
    from agency.config import get_agent_dir
    group_path = tmp_path / "group"
    g = {
        "path": group_path,
        "agents_full": [{"name": "sneaky", "path": "../etc/passwd", "integration": "claude-code"}],
    }
    assert get_agent_dir(g, "sneaky") == group_path / "sneaky"


# Task 3: Tests for get_allowed_roots()

def test_get_allowed_roots_includes_group_path(tmp_path):
    from agency.config import get_allowed_roots
    g = {
        "path": tmp_path / "group",
        "agents_full": [{"name": "product", "integration": "claude-code"}],
    }
    roots = get_allowed_roots(g)
    assert tmp_path / "group" in roots


def test_get_allowed_roots_includes_external_paths(tmp_path):
    from agency.config import get_allowed_roots
    external = tmp_path / "external" / "pm"
    g = {
        "path": tmp_path / "group",
        "agents_full": [
            {"name": "product", "integration": "claude-code"},
            {"name": "pm", "path": str(external), "integration": "claude-code"},
        ],
    }
    roots = get_allowed_roots(g)
    assert tmp_path / "group" in roots
    assert external in roots


# Task 4: Tests for validate_file_access() with multiple roots

def test_validate_file_access_allows_external_root(tmp_path):
    """validate_file_access should accept files under any allowed root."""
    from agency.app import validate_file_access
    external = tmp_path / "external" / "pm"
    external.mkdir(parents=True)
    test_file = external / "memory.md"
    test_file.touch()
    # Should not raise when external root is in allowed_roots
    validate_file_access(test_file, tmp_path / "group", allowed_roots=[external])


def test_validate_file_access_rejects_unallowed_path(tmp_path):
    """validate_file_access should reject files not under any allowed root."""
    from agency.app import validate_file_access
    from fastapi import HTTPException
    import pytest
    sneaky = tmp_path / "sneaky" / "file.md"
    sneaky.parent.mkdir(parents=True)
    sneaky.touch()
    with pytest.raises(HTTPException) as exc_info:
        validate_file_access(sneaky, tmp_path / "group", allowed_roots=[])
    assert exc_info.value.status_code == 403


# Task 5: Round-trip integration test for shared agent path

def test_normalize_agents_round_trip_with_path():
    """Ensure path field survives normalization and is accessible via agents_full."""
    from agency.config import normalize_agents, get_agent_dir
    from pathlib import Path

    raw = [
        "product",
        {"name": "pm", "path": "/shared/agents/pm", "integration": "codex"},
    ]
    normalized = normalize_agents(raw, "claude-code")

    g = {"path": Path("/groups/newsletter"), "agents_full": normalized}

    assert get_agent_dir(g, "product") == Path("/groups/newsletter/product")
    assert get_agent_dir(g, "pm") == Path("/shared/agents/pm")


# Task 1 (Agent Sandbox Root): Tests for get_sandbox_root()

def test_get_sandbox_root_absolute_string():
    from agency.config import get_sandbox_root, SandboxSpec
    g = {"path": "/groups/agents", "sandbox_root": "/repo/root"}
    assert get_sandbox_root(g) == SandboxSpec(roots=(Path("/repo/root"),), allowed_tools=())


def test_get_sandbox_root_relative_string_resolved_against_group_path():
    from agency.config import get_sandbox_root, SandboxSpec
    g = {"path": "/groups/agents", "sandbox_root": ".."}
    expected = (Path("/groups/agents") / "..").resolve()
    assert get_sandbox_root(g) == SandboxSpec(roots=(expected,), allowed_tools=())


def test_get_sandbox_root_list_preserves_order():
    from agency.config import get_sandbox_root, SandboxSpec
    g = {"path": "/groups/agents", "sandbox_root": ["/a", "rel"]}
    expected_rel = (Path("/groups/agents") / "rel").resolve()
    assert get_sandbox_root(g) == SandboxSpec(
        roots=(Path("/a"), expected_rel), allowed_tools=()
    )


def test_get_sandbox_root_missing_and_no_tools_returns_none():
    from agency.config import get_sandbox_root
    assert get_sandbox_root({"path": "/groups/agents"}) is None


def test_get_sandbox_root_blank_no_tools_returns_none():
    from agency.config import get_sandbox_root
    assert get_sandbox_root({"path": "/groups/agents", "sandbox_root": "   "}) is None


def test_get_sandbox_root_no_path_returns_none():
    from agency.config import get_sandbox_root
    assert get_sandbox_root({"sandbox_root": "relative/only"}) is None


def test_get_sandbox_root_tools_only():
    from agency.config import get_sandbox_root, SandboxSpec
    g = {"path": "/groups/agents", "allowed_tools": ["shell", "write"]}
    assert get_sandbox_root(g) == SandboxSpec(roots=(), allowed_tools=("shell", "write"))


def test_get_sandbox_root_roots_and_tools():
    from agency.config import get_sandbox_root, SandboxSpec
    g = {
        "path": "/groups/agents",
        "sandbox_root": "/repo/root",
        "allowed_tools": ["shell", "write"],
    }
    assert get_sandbox_root(g) == SandboxSpec(
        roots=(Path("/repo/root"),), allowed_tools=("shell", "write")
    )


def test_get_sandbox_root_list_drops_blank_entries():
    from agency.config import get_sandbox_root, SandboxSpec
    g = {"path": "/groups/agents", "sandbox_root": ["/a", "  "]}
    assert get_sandbox_root(g) == SandboxSpec(roots=(Path("/a"),), allowed_tools=())


# Task 2 (Official Dispatch CLI): Tests for save_config_path

import os
import yaml

from agency.config import save_config_path


def test_save_config_path_atomically_replaces_destination(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    replacements = []
    real_replace = os.replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("agency.config.os.replace", recording_replace)
    save_config_path(
        config_path,
        {"agency": {"dispatch": {"interval": 30}}, "groups": {}},
    )
    assert replacements[0][1] == config_path
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["agency"]["dispatch"]["interval"] == 30
