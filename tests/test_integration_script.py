import pytest
from agency.configuration import ValidationFailed
from agency.integrations.agency.script import ScriptIntegration
from agency.integrations import AgentIdentity
from agency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest, ResolvedToolPolicy


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


def test_validate_run_requires_runtime_placeholders(tmp_path):
    integration = ScriptIntegration({"command": "echo {prompt_file}"})
    request = IntegrationRunRequest(
        workspace_root=tmp_path / "workspace",
        launch_dir=tmp_path / "runtime",
        task_file=tmp_path / "runtime" / "task.md",
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
        "unsupported-skill-activation",
        "script-missing-runtime-placeholders",
    ]


def test_validate_run_rejects_obsolete_script_placeholders(tmp_path):
    integration = ScriptIntegration(
        {"command": "echo {runtime_dir} {workspace_dir} {agent_dir} {skill}"}
    )
    request = IntegrationRunRequest(
        workspace_root=tmp_path / "workspace",
        launch_dir=tmp_path / "runtime",
        task_file=tmp_path / "runtime" / "task.md",
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
    request.launch_dir.mkdir(parents=True)

    with pytest.raises(ValidationFailed):
        integration.run(request)


def test_with_config(integration):
    configured = integration.with_config({"command": "echo hello"})
    assert configured._config["command"] == "echo hello"
    assert configured is not integration  # New instance

def test_run_with_config(tmp_agent_dir, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do something")
    configured = ScriptIntegration({"command": "echo ran-{prompt_file}"})
    request = IntegrationRunRequest(
        workspace_root=tmp_agent_dir,
        launch_dir=tmp_agent_dir,
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
        enforce_validation=False,
    )
    result = configured.run(request)
    assert result.exit_code == 0
    assert "ran-" in result.stdout


def test_run_rejects_invalid_typed_request_before_script_launch(tmp_path):
    integration = ScriptIntegration({"command": "echo {prompt_file} {runtime_dir} {workspace_root} {skill}"})
    request = IntegrationRunRequest(
        workspace_root=tmp_path / "workspace",
        launch_dir=tmp_path / "runtime",
        task_file=tmp_path / "runtime" / "missing-task.md",
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

    request.launch_dir.mkdir(parents=True)

    with pytest.raises(ValidationFailed) as excinfo:
        integration.run(request)

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-skill-activation",
    ]
