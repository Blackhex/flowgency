import pytest
from agency.integrations import AgentIdentity, detect_integration
from agency.integrations.agency.codex import CodexIntegration
from agency.integrations.agency.gemini import GeminiIntegration
from agency.integrations.agency.aider import AiderIntegration
from agency.integrations.agency.goose import GooseIntegration
from agency.integrations.agency.opencode import OpenCodeIntegration
from agency.integrations.agency.pi import PiIntegration


class TestCodex:
    @pytest.fixture
    def integration(self):
        return CodexIntegration()

    def test_metadata(self, integration):
        assert integration.name == "codex"
        assert integration.identity_filename() == "AGENTS.md"

    def test_detect(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Product Manager\nManage products.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# Product Manager" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Product Manager\ntitle: PM\nemoji: \"📦\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Product Manager"
        assert identity.title == "PM"

    def test_write_identity_creates_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Old body\n")
        identity = AgentIdentity(display_name="New", title="T", emoji="🤖", body="# New body")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New body" in (tmp_agent_dir / "AGENTS.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None


class TestGemini:
    @pytest.fixture
    def integration(self):
        return GeminiIntegration()

    def test_metadata(self, integration):
        assert integration.name == "gemini"
        assert integration.identity_filename() == "GEMINI.md"

    def test_detect(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "GEMINI.md").write_text("# Agent\n")
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "GEMINI.md").write_text("# Gemini Agent\nDo things.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# Gemini Agent" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "GEMINI.md").write_text("# Agent\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Gemini Bot\ntitle: GB\nemoji: \"🌟\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Gemini Bot"
        assert identity.title == "GB"

    def test_write_identity_creates_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "GEMINI.md").write_text("# Old\n")
        identity = AgentIdentity(display_name="New", title="T", emoji="🌟", body="# New body")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New body" in (tmp_agent_dir / "GEMINI.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None


class TestAider:
    @pytest.fixture
    def integration(self):
        return AiderIntegration()

    def test_metadata(self, integration):
        assert integration.name == "aider"
        assert integration.supports_ai_backend is False

    def test_detect(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".aider.conf.yml").write_text("read: CONVENTIONS.md\n")
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_identity_filename(self, integration):
        assert integration.identity_filename() == "CONVENTIONS.md"

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "CONVENTIONS.md").write_text("# Conventions\nFollow these.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# Conventions" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "CONVENTIONS.md").write_text("# Conventions\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Aider Bot\ntitle: AB\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Aider Bot"
        assert identity.title == "AB"

    def test_write_identity_creates_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "CONVENTIONS.md").write_text("# Old\n")
        identity = AgentIdentity(display_name="New", title="T", emoji="🔧", body="# New conventions")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New conventions" in (tmp_agent_dir / "CONVENTIONS.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None


class TestGoose:
    @pytest.fixture
    def integration(self):
        return GooseIntegration()

    def test_metadata(self, integration):
        assert integration.name == "goose"
        assert integration.identity_filename() == ".goosehints"

    def test_detect(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".goosehints").write_text("Some hints\n")
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".goosehints").write_text("Use Python 3.11+\nFollow PEP 8\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "Use Python 3.11+" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".goosehints").write_text("Some hints\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Goose Bot\ntitle: GB\nemoji: \"🪿\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Goose Bot"
        assert identity.title == "GB"

    def test_write_identity_creates_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".goosehints").write_text("Old hints\n")
        identity = AgentIdentity(display_name="New", title="T", emoji="🪿", body="New hints")
        integration.write_identity(tmp_agent_dir, identity)
        assert "New hints" in (tmp_agent_dir / ".goosehints").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None


class TestOpenCode:
    @pytest.fixture
    def integration(self):
        return OpenCodeIntegration()

    def test_metadata(self, integration):
        assert integration.name == "opencode"
        assert integration.identity_filename() == "AGENTS.md"
        assert integration.detect_priority == 8

    def test_detect_with_opencode_dir(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".opencode").mkdir()
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_detect_negative_agents_md_only(self, integration, tmp_agent_dir):
        """AGENTS.md alone should NOT trigger OpenCode — that's Codex."""
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".opencode").mkdir()
        (tmp_agent_dir / "AGENTS.md").write_text("# OpenCode Agent\nBuild things.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# OpenCode Agent" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".opencode").mkdir()
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: OpenCode Bot\ntitle: OC\nemoji: \"⚡\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "OpenCode Bot"
        assert identity.title == "OC"

    def test_write_identity_creates_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".opencode").mkdir()
        (tmp_agent_dir / "AGENTS.md").write_text("# Old body\n")
        identity = AgentIdentity(display_name="New", title="T", emoji="⚡", body="# New body")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New body" in (tmp_agent_dir / "AGENTS.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None


class TestPi:
    @pytest.fixture
    def integration(self):
        return PiIntegration()

    def test_metadata(self, integration):
        assert integration.name == "pi"
        assert integration.identity_filename() == "AGENTS.md"
        assert integration.detect_priority == 8

    def test_detect_with_pi_dir(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".pi").mkdir()
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_detect_negative_agents_md_only(self, integration, tmp_agent_dir):
        """AGENTS.md alone should NOT trigger Pi — that's Codex."""
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".pi").mkdir()
        (tmp_agent_dir / "AGENTS.md").write_text("# Pi Agent\nDo things.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# Pi Agent" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".pi").mkdir()
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Pi Bot\ntitle: PB\nemoji: \"🥧\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Pi Bot"
        assert identity.title == "PB"

    def test_write_identity_creates_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".pi").mkdir()
        (tmp_agent_dir / "AGENTS.md").write_text("# Old body\n")
        identity = AgentIdentity(display_name="New", title="T", emoji="🥧", body="# New body")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New body" in (tmp_agent_dir / "AGENTS.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None


class TestDetectionPriority:
    """Ensure OpenCode and Pi are detected before Codex when their config dirs exist."""

    def test_opencode_over_codex(self, tmp_agent_dir):
        """AGENTS.md + .opencode/ → OpenCode, not Codex."""
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        (tmp_agent_dir / ".opencode").mkdir()
        result = detect_integration(tmp_agent_dir)
        assert result is not None
        assert result.name == "opencode"

    def test_pi_over_codex(self, tmp_agent_dir):
        """AGENTS.md + .pi/ → Pi, not Codex."""
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        (tmp_agent_dir / ".pi").mkdir()
        result = detect_integration(tmp_agent_dir)
        assert result is not None
        assert result.name == "pi"

    def test_agents_md_only_is_codex(self, tmp_agent_dir):
        """AGENTS.md alone → Codex."""
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        result = detect_integration(tmp_agent_dir)
        assert result is not None
        assert result.name == "codex"
