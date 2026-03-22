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
