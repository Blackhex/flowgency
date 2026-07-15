import inspect
import pytest
from pathlib import Path
from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError,
    REGISTRY, get_integration, detect_integration,
)


def test_run_result_dataclass():
    r = RunResult(exit_code=0, stdout="ok", stderr="", duration_seconds=1.5)
    assert r.exit_code == 0
    assert r.stdout == "ok"
    assert r.duration_seconds == 1.5


def test_agent_identity_dataclass():
    i = AgentIdentity(display_name="Bot", title="Helper", emoji="🤖", body="# Role\nDo stuff.")
    assert i.display_name == "Bot"
    assert i.body == "# Role\nDo stuff."


def test_integration_error():
    with pytest.raises(IntegrationError):
        raise IntegrationError("tool not found")


def test_registry_has_shipped_integrations():
    assert "claude-code" in REGISTRY
    assert "codex" in REGISTRY
    assert "gemini" in REGISTRY
    assert "aider" in REGISTRY
    assert "goose" in REGISTRY
    assert "script" in REGISTRY
    assert "sdk" in REGISTRY


def test_get_integration_found():
    integration = get_integration("claude-code")
    assert integration.name == "claude-code"


def test_get_integration_not_found():
    with pytest.raises(KeyError):
        get_integration("nonexistent")


def test_detect_integration_claude_code(tmp_agent_dir):
    (tmp_agent_dir / "CLAUDE.md").write_text("# Agent\n")
    result = detect_integration(tmp_agent_dir)
    assert result is not None
    assert result.name == "claude-code"


def test_detect_integration_codex(tmp_agent_dir):
    (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
    result = detect_integration(tmp_agent_dir)
    assert result is not None
    assert result.name == "codex"


def test_detect_integration_sdk_fallback(tmp_agent_dir):
    (tmp_agent_dir / "agent.md").write_text("# Agent\n")
    result = detect_integration(tmp_agent_dir)
    assert result is not None
    assert result.name == "sdk"


def test_detect_integration_none(tmp_agent_dir):
    result = detect_integration(tmp_agent_dir)
    assert result is None


def test_detect_priority_order(tmp_agent_dir):
    """If a dir has both CLAUDE.md and agent.md, claude-code wins (lower priority number)."""
    (tmp_agent_dir / "CLAUDE.md").write_text("# Agent\n")
    (tmp_agent_dir / "agent.md").write_text("# Agent\n")
    result = detect_integration(tmp_agent_dir)
    assert result.name == "claude-code"


def test_base_integration_defaults():
    class TestIntegration(BaseIntegration):
        name = "test"
        display_name = "Test"
    ti = TestIntegration()
    assert ti.supports_execution is True
    assert ti.supports_ai_backend is False
    assert ti.detect_priority == 100
    assert ti.default_config() == {}
    assert ti.validate_config({}) == []


def test_load_integrations_from_config():
    """Config-driven loading populates the registry."""
    from agency.integrations import REGISTRY
    assert len(REGISTRY) >= 7


def test_integrations_yaml_exists():
    """integrations.yaml config file exists."""
    from agency.integrations import INTEGRATIONS_DIR
    config_path = INTEGRATIONS_DIR / "integrations.yaml"
    assert config_path.exists()


def test_base_integration_supports_sandbox_defaults_false():
    assert BaseIntegration.supports_sandbox is False


def test_base_run_accepts_typed_request():
    sig = inspect.signature(BaseIntegration.run)
    params = list(sig.parameters.values())
    assert len(params) == 2
    assert params[0].name == "self"
    assert params[1].name == "request"
