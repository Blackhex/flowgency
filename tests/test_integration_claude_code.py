import pytest
from pathlib import Path
from agency.integrations.agency.claude_code import ClaudeCodeIntegration
from agency.integrations import AgentIdentity


@pytest.fixture
def integration():
    return ClaudeCodeIntegration()


def test_metadata(integration):
    assert integration.name == "claude-code"
    assert integration.display_name == "Claude Code"
    assert integration.supports_execution is True
    assert integration.supports_ai_backend is True


def test_identity_filename(integration):
    assert integration.identity_filename() == "CLAUDE.md"


def test_detect_with_claude_md(integration, tmp_agent_dir):
    (tmp_agent_dir / "CLAUDE.md").write_text("# Agent\n")
    assert integration.detect(tmp_agent_dir) is True


def test_detect_without_claude_md(integration, tmp_agent_dir):
    assert integration.detect(tmp_agent_dir) is False


def test_parse_identity_with_frontmatter(integration, tmp_agent_dir):
    (tmp_agent_dir / "CLAUDE.md").write_text(
        "---\ndisplay_name: Product Manager\ntitle: PM\nemoji: \"📦\"\n---\n\n# Role\nDo stuff.\n"
    )
    identity = integration.parse_identity(tmp_agent_dir)
    assert identity is not None
    assert identity.display_name == "Product Manager"
    assert identity.title == "PM"
    assert identity.emoji == "📦"
    assert "# Role" in identity.body


def test_parse_identity_without_frontmatter(integration, tmp_agent_dir):
    (tmp_agent_dir / "CLAUDE.md").write_text("# Role\nDo stuff.\n")
    identity = integration.parse_identity(tmp_agent_dir)
    assert identity is not None
    assert identity.display_name is None
    assert "# Role" in identity.body


def test_parse_identity_missing_file(integration, tmp_agent_dir):
    identity = integration.parse_identity(tmp_agent_dir)
    assert identity is None


def test_write_identity_new_file(integration, tmp_agent_dir):
    identity = AgentIdentity(display_name="Bot", title="Helper", emoji="🤖", body="# Role\nHelp.")
    integration.write_identity(tmp_agent_dir, identity)
    content = (tmp_agent_dir / "CLAUDE.md").read_text()
    assert "display_name: Bot" in content
    assert "# Role" in content


def test_write_identity_preserves_extra_frontmatter(integration, tmp_agent_dir):
    (tmp_agent_dir / "CLAUDE.md").write_text(
        "---\ndisplay_name: Old\ncustom_field: keep_me\n---\n\nOld body.\n"
    )
    identity = AgentIdentity(display_name="New", title="T", emoji="", body="New body.")
    integration.write_identity(tmp_agent_dir, identity)
    content = (tmp_agent_dir / "CLAUDE.md").read_text()
    assert "display_name: New" in content
    assert "custom_field: keep_me" in content
    assert "New body." in content
