import pytest
from agency.integrations.script import ScriptIntegration
from agency.integrations import AgentIdentity


@pytest.fixture
def integration():
    return ScriptIntegration()


def test_metadata(integration):
    assert integration.name == "script"
    assert integration.supports_execution is True
    assert integration.supports_ai_backend is False

def test_detect_never(integration, tmp_agent_dir):
    (tmp_agent_dir / "agent.md").write_text("# Agent\n")
    assert integration.detect(tmp_agent_dir) is False

def test_identity_filename(integration):
    assert integration.identity_filename() == "agent.md"

def test_parse_identity_with_frontmatter(integration, tmp_agent_dir):
    (tmp_agent_dir / "agent.md").write_text(
        "---\ndisplay_name: Bot\ntitle: Helper\nemoji: \"🤖\"\n---\n\n# Role\nDo stuff.\n"
    )
    identity = integration.parse_identity(tmp_agent_dir)
    assert identity.display_name == "Bot"
    assert "# Role" in identity.body

def test_validate_config_missing_command(integration):
    errors = integration.validate_config({})
    assert any("command" in e.lower() for e in errors)

def test_validate_config_valid(integration):
    errors = integration.validate_config({"command": "echo {prompt_file}"})
    assert errors == []

def test_with_config(integration):
    configured = integration.with_config({"command": "echo hello"})
    assert configured._config["command"] == "echo hello"
    assert configured is not integration  # New instance

def test_run_with_config(tmp_agent_dir, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do something")
    configured = ScriptIntegration({"command": "echo ran-{prompt_file}"})
    result = configured.run(tmp_agent_dir, prompt, 60)
    assert result.exit_code == 0
    assert "ran-" in result.stdout
