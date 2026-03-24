import pytest
from agency.integrations.agency.sdk import SdkIntegration


@pytest.fixture
def integration():
    return SdkIntegration()

def test_metadata(integration):
    assert integration.name == "sdk"
    assert integration.supports_execution is False
    assert integration.supports_ai_backend is False
    assert integration.detect_priority == 999

def test_detect_with_agent_md(integration, tmp_agent_dir):
    (tmp_agent_dir / "agent.md").write_text("# Agent\n")
    assert integration.detect(tmp_agent_dir) is True

def test_detect_without_agent_md(integration, tmp_agent_dir):
    assert integration.detect(tmp_agent_dir) is False

def test_run_returns_error(integration, tmp_agent_dir, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do something")
    result = integration.run(tmp_agent_dir, prompt, 60)
    assert result.exit_code != 0
    assert "externally managed" in result.stderr.lower()
