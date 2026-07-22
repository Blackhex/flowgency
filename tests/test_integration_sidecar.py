import pytest
from agency.configuration import ValidationFailed
from agency.integrations import AgentIdentity, detect_integration
from agency.integrations.agency.codex import CodexIntegration
from agency.integrations.agency.gemini import GeminiIntegration
from agency.integrations.agency.aider import AiderIntegration
from agency.integrations.agency.goose import GooseIntegration
from agency.integrations.agency.opencode import OpenCodeIntegration
from agency.integrations.agency.pi import PiIntegration
from agency.integrations.agency.copilot import CopilotIntegration
from agency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest, ResolvedToolPolicy


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

    def test_validate_run_rejects_skill_activation_until_cli_contract_is_verified(self, integration, tmp_path):
        request = IntegrationRunRequest(
            workspace_root=tmp_path / "workspace",
            launch_dir=tmp_path / "launch",
            task_file=tmp_path / "launch" / "task.md",
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill="daily-review",
            skill_arguments=(),
        )

        issues = integration.validate_run(request)

        assert [issue.code for issue in issues] == [
            "unsupported-path-policy",
            "unsupported-tool-policy",
            "unsupported-skill-activation",
        ]

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


class TestCopilot:
    @pytest.fixture
    def integration(self):
        return CopilotIntegration()

    def test_metadata(self, integration):
        assert integration.name == "copilot"
        assert integration.display_name == "GitHub Copilot"
        assert integration.supports_execution is True
        assert integration.supports_ai_backend is True

    def test_identity_filename(self, integration):
        assert integration.identity_filename() == "AGENTS.md"

    def test_detect_copilot_marker(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".copilot").mkdir()
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_github_marker(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".github").mkdir()
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Copilot Agent\nDo things.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# Copilot Agent" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        (tmp_agent_dir / "AGENTS.md").write_text("# Agent\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Copilot Bot\ntitle: CB\nemoji: \"🐙\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Copilot Bot"
        assert identity.title == "CB"

    def test_write_identity_creates_file_and_sidecar(self, integration, tmp_agent_dir):
        identity = AgentIdentity(display_name="New", title="T", emoji="🐙", body="# New body")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New body" in (tmp_agent_dir / "AGENTS.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_write_identity_creates_detection_marker(self, integration, tmp_agent_dir):
        identity = AgentIdentity(
            display_name="Copilot Bot",
            title="Builder",
            emoji="",
            body="# Copilot Bot\n",
        )

        integration.write_identity(tmp_agent_dir, identity)

        assert (tmp_agent_dir / ".copilot").is_dir()
        assert detect_integration(tmp_agent_dir).name == "copilot"

    def test_prepare_agent_dir_is_idempotent(self, integration, tmp_agent_dir):
        integration.prepare_agent_dir(tmp_agent_dir)
        integration.prepare_agent_dir(tmp_agent_dir)

        assert (tmp_agent_dir / ".copilot").is_dir()

    def test_write_identity_propagates_prepare_error_without_partial_files(
        self, integration, tmp_agent_dir, monkeypatch
    ):
        error = PermissionError("marker creation denied")

        def fail_preparation(agent_dir):
            raise error

        monkeypatch.setattr(integration, "prepare_agent_dir", fail_preparation)
        identity = AgentIdentity(
            display_name="Copilot Bot",
            title="Builder",
            emoji="",
            body="# Copilot Bot\n",
        )

        with pytest.raises(PermissionError) as exc_info:
            integration.write_identity(tmp_agent_dir, identity)

        assert exc_info.value is error
        assert not (tmp_agent_dir / "AGENTS.md").exists()
        assert not (tmp_agent_dir / ".agency-meta.yaml").exists()

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None

    def test_run_builds_command(self, integration, tmp_agent_dir, monkeypatch):
        import agency.integrations.agency.copilot as mod
        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        prompt_file = tmp_agent_dir / "prompt.md"
        prompt_file.write_text("Do the thing")
        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt_file,
            timeout=30,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=30,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        result = integration.run(request)
        assert result.exit_code == 0
        assert captured["cmd"][1] == "-p"
        assert "Do the thing" in captured["cmd"]
        assert "--autopilot" in captured["cmd"]
        assert "--experimental" in captured["cmd"]
        assert "--no-custom-instructions" not in captured["cmd"]
        assert captured["cwd"] == str(request.launch_dir)

    def test_run_rejects_invalid_typed_request_before_reading_prompt(self, integration, tmp_agent_dir):
        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=tmp_agent_dir / "runtime" / "missing-task.md",
            timeout=30,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=30,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )

        request.launch_dir.mkdir(parents=True)
        failure = ValidationFailed(())
        integration.require_valid_run = lambda run_request: (_ for _ in ()).throw(failure)

        with pytest.raises(ValidationFailed) as excinfo:
            integration.run(request)

        assert excinfo.value is failure

    def test_prompt_returns_stdout(self, integration, monkeypatch):
        import agency.integrations.agency.copilot as mod

        class FakeCompleted:
            returncode = 0
            stdout = "hello"
            stderr = ""

        def fake_run(cmd, **kwargs):
            fake_run.cmd = cmd
            return FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        out = integration.prompt("hi there", timeout=10)
        assert out == "hello"
        assert "--autopilot" in fake_run.cmd
        assert "--experimental" in fake_run.cmd
        assert "hi there" in fake_run.cmd

    def test_copilot_supports_sandbox_true(self, integration):
        assert integration.supports_sandbox is True

    def test_copilot_run_unset_sandbox_uses_allow_all_paths(self, tmp_agent_dir, monkeypatch):
        import agency.integrations.agency.copilot as copilot_mod

        prompt = tmp_agent_dir / "p.prompt"
        prompt.write_text("do the thing")

        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["cwd"] = kwargs.get("cwd")
            return FakeCompleted()

        monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: "copilot")

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        CopilotIntegration().run(request)

        args = captured["args"]
        # Unrestricted mode: both axes blanket-approved, --autopilot present.
        assert "--allow-all-paths" in args
        assert "--allow-all-tools" in args
        assert "--autopilot" in args
        assert "--add-dir" not in args
        assert "--allow-tool" not in args
        # Unrestricted mode runs from the agent dir
        assert captured["cwd"] == str(request.launch_dir)

    def test_copilot_run_none_and_empty_spec_equivalent(self, tmp_agent_dir, monkeypatch):
        import agency.integrations.agency.copilot as copilot_mod

        prompt = tmp_agent_dir / "p.prompt"
        prompt.write_text("do the thing")

        class FakeCompleted:
            returncode = 0
            stdout = "ok"
            stderr = ""

        results = []

        def fake_run(args, **kwargs):
            results.append((list(args), kwargs.get("cwd")))
            return FakeCompleted()

        monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: "copilot")

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )

        CopilotIntegration().run(request)
        CopilotIntegration().run(request)

        # None and an empty SandboxSpec produce identical unrestricted argv/cwd.
        assert results[0] == results[1]
        args, cwd = results[0]
        assert "--allow-all-paths" in args
        assert "--allow-all-tools" in args
        assert "--autopilot" in args
        assert cwd == str(request.launch_dir)

    def test_copilot_run_roots_only_blanket_tools(self, tmp_agent_dir, monkeypatch):
        import agency.integrations.agency.copilot as copilot_mod

        prompt = tmp_agent_dir / "p.prompt"
        prompt.write_text("do the thing")
        root = tmp_agent_dir / "repo"
        root.mkdir()

        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["cwd"] = kwargs.get("cwd")
            return FakeCompleted()

        monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: "copilot")

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="restricted",
                sandbox_roots=(root,),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        CopilotIntegration().run(request)

        args = captured["args"]
        # roots set => --add-dir per root, cwd anchored at first root.
        assert "--add-dir" in args
        assert str(root) in args
        assert captured["cwd"] == str(request.launch_dir)
        assert "--allow-all-paths" not in args
        # tools empty => blanket tools + --autopilot.
        assert "--allow-all-tools" in args
        assert "--autopilot" in args
        assert "--allow-tool" not in args

    def test_copilot_run_roots_and_tools_least_privilege(self, tmp_agent_dir, monkeypatch):
        import agency.integrations.agency.copilot as copilot_mod

        prompt = tmp_agent_dir / "p.prompt"
        prompt.write_text("do the thing")
        r1 = tmp_agent_dir / "repo"
        r2 = tmp_agent_dir / "cowork"
        r1.mkdir()
        r2.mkdir()

        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["cwd"] = kwargs.get("cwd")
            captured["kwargs"] = kwargs
            return FakeCompleted()

        monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: "copilot")

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="restricted",
                sandbox_roots=(r1, r2),
                tools=ResolvedToolPolicy("allowlist", ("shell", "write")),
            ),
            skill=None,
            skill_arguments=(),
        )
        CopilotIntegration().run(request)

        args = captured["args"]
        # Every root added explicitly; cwd anchored at the first root.
        assert "--add-dir" in args
        assert str(r1) in args
        assert str(r2) in args
        assert captured["cwd"] == str(request.launch_dir)
        # Every tool granted explicitly.
        assert "--allow-tool" in args
        assert "shell" in args
        assert "write" in args
        # Least-privilege: no blanket flags, no --autopilot.
        assert "--autopilot" not in args
        assert "--allow-all-paths" not in args
        assert "--allow-all-tools" not in args
        # Headless launch preserved.
        assert captured["kwargs"].get("stdin") is copilot_mod.subprocess.DEVNULL
        assert "creationflags" in captured["kwargs"]

    def test_copilot_resolve_real_cmd_skips_layered_windows_wrappers(self, monkeypatch):
        import agency.integrations.agency.copilot as copilot_mod

        wrapper = r"C:\wrap\copilot.BAT"
        npm_wrapper = r"C:\npm\copilot.CMD"
        real = r"C:\real\copilot.EXE"
        calls = []

        def fake_which(name, path=None):
            calls.append(name)
            return {
                "copilot": npm_wrapper,
                "copilot.exe": real,
            }.get(name)

        monkeypatch.setattr(copilot_mod.sys, "platform", "win32")
        monkeypatch.setattr(copilot_mod.shutil, "which", fake_which)

        assert CopilotIntegration._resolve_real_cmd(wrapper) == real
        assert calls == ["copilot.exe"]

    def test_copilot_resolve_real_cmd_noop_off_windows(self, monkeypatch):
        import agency.integrations.agency.copilot as copilot_mod

        monkeypatch.setattr(copilot_mod.sys, "platform", "linux")
        assert CopilotIntegration._resolve_real_cmd("copilot") == "copilot"

    def test_parse_jsonl_extracts_native_edits(self):
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        root = Path("C:/repo") if False else Path("/repo")
        lines = [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t1", "toolName": "create",
                      "arguments": {"path": str(root / "greeting.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t1", "success": True,
                      "toolTelemetry": {"properties": {"command": "create"},
                                        "metrics": {"linesAdded": 1, "linesRemoved": 0}}}},
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t2", "toolName": "edit",
                      "arguments": {"path": str(root / "existing.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t2", "success": True,
                      "toolTelemetry": {"properties": {"command": "edit"},
                                        "metrics": {"linesAdded": 1, "linesRemoved": 1}}}},
            {"type": "assistant.message", "data": {"content": "Done."}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        text, changes = CopilotIntegration._parse_jsonl_output(raw, root)
        assert "Done." in text
        by_path = {c.path: c for c in changes}
        assert by_path["greeting.txt"].status == "added"
        assert by_path["greeting.txt"].lines_added == 1
        assert by_path["existing.txt"].status == "modified"
        assert by_path["existing.txt"].lines_added == 1
        assert by_path["existing.txt"].lines_removed == 1

    def test_parse_jsonl_skips_readonly_view(self):
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        lines = [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "v1", "toolName": "view",
                      "arguments": {"path": "/repo/a.txt"}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "v1", "success": True,
                      "toolTelemetry": {"properties": {"command": "view"},
                                        "metrics": {}}}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        _text, changes = CopilotIntegration._parse_jsonl_output(raw, Path("/repo"))
        assert changes == []

    def test_parse_jsonl_malformed_falls_back(self):
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        raw = "this is not json\nok"
        text, changes = CopilotIntegration._parse_jsonl_output(raw, Path("/repo"))
        assert text == raw
        assert changes == []

    def test_run_emits_json_and_populates_changed_files(self, integration, tmp_agent_dir, monkeypatch):
        import json
        import agency.integrations.agency.copilot as mod

        jsonl = "\n".join(json.dumps(l) for l in [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t1", "toolName": "create",
                      "arguments": {"path": str(tmp_agent_dir / "new.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t1", "success": True,
                      "toolTelemetry": {"properties": {"command": "create"},
                                        "metrics": {"linesAdded": 3, "linesRemoved": 0}}}},
            {"type": "assistant.message", "data": {"content": "Created new.txt"}},
        ])

        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = jsonl
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        prompt_file = tmp_agent_dir / "prompt.md"
        prompt_file.write_text("Do the thing")
        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt_file,
            timeout=30,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=30,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        result = integration.run(request)

        assert "--output-format" in captured["cmd"]
        assert "json" in captured["cmd"]
        assert result.stdout == "Created new.txt"
        assert len(result.changed_files) == 1
        assert result.changed_files[0].path == "new.txt"
        assert result.changed_files[0].status == "added"
        assert result.changed_files[0].lines_added == 3

    def test_run_timeout_preserves_partial_output(self, integration, tmp_agent_dir, monkeypatch):
        import json
        import agency.integrations.agency.copilot as mod

        jsonl = json.dumps({
            "type": "assistant.message",
            "data": {"content": "Implemented the first part."},
        }).encode()

        def time_out(cmd, **kwargs):
            raise mod.subprocess.TimeoutExpired(
                cmd,
                kwargs["timeout"],
                output=jsonl,
                stderr=b"Work was still in progress.",
            )

        monkeypatch.setattr(mod.subprocess, "run", time_out)
        prompt_file = tmp_agent_dir / "prompt.md"
        prompt_file.write_text("Do the thing")

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt_file,
            timeout=30,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=30,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        result = integration.run(request)

        assert result.exit_code == 124
        assert result.stdout == "Implemented the first part."
        assert "Work was still in progress." in result.stderr
        assert "Timed out after 30 seconds." in result.stderr

    def test_parse_jsonl_result_event_fallback(self):
        """M2: result event with filesModified falls back when no per-tool edits parsed."""
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        root = Path("/repo")
        lines = [
            {"type": "result",
             "data": {"usage": {"codeChanges": {"filesModified": [str(root / "changed.py")]}}}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        text, changes = CopilotIntegration._parse_jsonl_output(raw, root)
        assert len(changes) == 1
        assert changes[0].path == "changed.py"
        assert changes[0].status == "modified"
        assert changes[0].lines_added == 0
        assert changes[0].lines_removed == 0

    def test_usage_summary_reads_session_shutdown_metrics(self, tmp_path, monkeypatch):
        import json
        from agency.integrations.agency.copilot import CopilotIntegration

        session_id = "12345678-abcd"
        state_dir = tmp_path / "session-state" / session_id
        state_dir.mkdir(parents=True)
        shutdown = {
            "type": "session.shutdown",
            "data": {
                "totalPremiumRequests": 62,
                "tokenDetails": {
                    "input": {"tokenCount": 100},
                    "cache_read": {"tokenCount": 568700},
                    "cache_write": {"tokenCount": 29200},
                    "output": {"tokenCount": 6100},
                },
                "modelMetrics": {
                    "model": {"usage": {"reasoningTokens": 3400}},
                },
                "codeChanges": {"linesAdded": 4, "linesRemoved": 2},
            },
        }
        (state_dir / "events.jsonl").write_text(json.dumps(shutdown) + "\n")
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
        raw = json.dumps({
            "type": "result",
            "sessionId": session_id,
            "usage": {"sessionDurationMs": 117000},
        })

        summary = CopilotIntegration._usage_summary(raw)

        assert summary == (
            "Changes    +4 -2\n"
            "AI Credits 62 (1m 57s)\n"
            "Tokens     \u2191 598.0k (568.7k cached, 29.2k written) "
            "\u2022 \u2193 6.1k (3.4k reasoning)\n"
            "Resume     copilot --resume=12345678-abcd"
        )

    def test_run_writes_usage_summary_to_stderr(
        self, integration, tmp_agent_dir, tmp_path, monkeypatch
    ):
        import json
        import agency.integrations.agency.copilot as mod

        session_id = "usage-session"
        state_dir = tmp_path / "session-state" / session_id
        state_dir.mkdir(parents=True)
        (state_dir / "events.jsonl").write_text(json.dumps({
            "type": "session.shutdown",
            "data": {
                "totalPremiumRequests": 5,
                "tokenDetails": {
                    "input": {"tokenCount": 10},
                    "cache_read": {"tokenCount": 20},
                    "cache_write": {"tokenCount": 30},
                    "output": {"tokenCount": 40},
                },
                "modelMetrics": {},
                "codeChanges": {"linesAdded": 0, "linesRemoved": 0},
            },
        }) + "\n")
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
        jsonl = "\n".join([
            json.dumps({"type": "assistant.message", "data": {"content": "Done."}}),
            json.dumps({
                "type": "result",
                "sessionId": session_id,
                "usage": {"sessionDurationMs": 1000},
            }),
        ])

        class FakeCompleted:
            returncode = 0
            stdout = jsonl
            stderr = ""

        monkeypatch.setattr(mod.subprocess, "run", lambda *args, **kwargs: FakeCompleted())
        prompt_file = tmp_agent_dir / "prompt.md"
        prompt_file.write_text("Do the thing")

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt_file,
            timeout=30,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=30,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        result = integration.run(request)

        assert "AI Credits 5 (1s)" in result.stderr
        assert "Tokens     \u2191 60" in result.stderr
        assert "Resume     copilot --resume=usage-session" in result.stderr

    def test_parse_jsonl_same_path_aggregation(self):
        """M3: create then edit on same path yields single FileChange with status=added and summed lines."""
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        root = Path("/repo")
        lines = [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t1", "toolName": "create",
                      "arguments": {"path": str(root / "file.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t1", "success": True,
                      "toolTelemetry": {"properties": {"command": "create"},
                                        "metrics": {"linesAdded": 5, "linesRemoved": 0}}}},
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t2", "toolName": "edit",
                      "arguments": {"path": str(root / "file.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t2", "success": True,
                      "toolTelemetry": {"properties": {"command": "edit"},
                                        "metrics": {"linesAdded": 2, "linesRemoved": 1}}}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        text, changes = CopilotIntegration._parse_jsonl_output(raw, root)
        assert len(changes) == 1
        assert changes[0].path == "file.txt"
        assert changes[0].status == "added"  # created-stays-added precedence
        assert changes[0].lines_added == 7   # 5 + 2
        assert changes[0].lines_removed == 1

    def test_parse_jsonl_shell_only_yields_no_changes(self):
        """M4: shell tool mutations are not tracked (limitation)."""
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        lines = [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "s1", "toolName": "shell",
                      "arguments": {"command": "echo hello > out.txt"}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "s1", "success": True,
                      "toolTelemetry": {"properties": {"command": "shell"},
                                        "metrics": {}}}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        text, changes = CopilotIntegration._parse_jsonl_output(raw, Path("/repo"))
        assert changes == []
