import pytest
from agency.integrations.agency.sdk import SdkIntegration
from agency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest, ResolvedToolPolicy


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
    request = IntegrationRunRequest(
        workspace_dir=tmp_agent_dir,
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
    )
    result = integration.run(request)
    assert result.exit_code != 0
    assert "externally managed" in result.stderr.lower()


def test_validate_run_rejects_execution(tmp_path):
    integration = SdkIntegration()
    request = IntegrationRunRequest(
        workspace_dir=tmp_path / "workspace",
        launch_dir=tmp_path / "runtime",
        task_file=tmp_path / "runtime" / "task.md",
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

    issues = integration.validate_run(request)

    assert [issue.code for issue in issues] == [
        "unsupported-path-policy",
        "unsupported-tool-policy",
        "integration-not-executable",
    ]
