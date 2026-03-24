import pytest


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
