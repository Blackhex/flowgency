"""Contract tests: validate all registered integrations meet the BaseIntegration API."""
import inspect
import subprocess
from pathlib import Path

import pytest

from agency.configuration import ValidationFailed
from agency.configuration.issues import ValidationIssue
from agency.configuration.effective import resolve_effective_policy
from agency.integrations import REGISTRY, AgentIdentity, BaseIntegration, FileChange, RunResult
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ProjectorCapabilities,
    ResolvedToolPolicy,
    RuntimeCapabilities,
)


def all_integration_names():
    return list(REGISTRY.keys())


@pytest.fixture(params=all_integration_names())
def integration(request):
    return REGISTRY[request.param]


class TestIntegrationContract:
    def test_has_name(self, integration):
        assert isinstance(integration.name, str)
        assert len(integration.name) > 0

    def test_has_display_name(self, integration):
        assert isinstance(integration.display_name, str)

    def test_identity_filename_returns_string(self, integration):
        result = integration.identity_filename()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_detect_accepts_path_returns_bool(self, integration, tmp_path):
        result = integration.detect(tmp_path)
        assert isinstance(result, bool)

    def test_parse_identity_accepts_path(self, integration, tmp_path):
        result = integration.parse_identity(tmp_path)
        assert result is None or isinstance(result, AgentIdentity)

    def test_supports_execution_is_bool(self, integration):
        assert isinstance(integration.supports_execution, bool)

    def test_supports_ai_backend_is_bool(self, integration):
        assert isinstance(integration.supports_ai_backend, bool)

    def test_detect_priority_is_int(self, integration):
        assert isinstance(integration.detect_priority, int)

    def test_run_callable_if_execution_supported(self, integration):
        if integration.supports_execution:
            assert callable(integration.run)

    def test_is_base_integration_subclass(self, integration):
        assert isinstance(integration, BaseIntegration)

    def test_runtime_capabilities_declared(self, integration):
        assert isinstance(integration.runtime_capabilities, RuntimeCapabilities)

    def test_projector_capabilities_declared(self, integration):
        assert isinstance(integration.projector.capabilities, ProjectorCapabilities)

    def test_validate_run_returns_validation_issues(self, integration, tmp_path):
        request = IntegrationRunRequest(
            workspace_root=tmp_path / "workspace",
            launch_dir=tmp_path / "launch",
            task_file=tmp_path / "task.md",
            timeout=1800,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=1800,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )
        result = integration.validate_run(request)
        assert all(isinstance(issue, ValidationIssue) for issue in result)

    def test_validate_runtime_policy_returns_validation_issues(self, integration):
        result = integration.validate_runtime_policy(
            EffectiveRuntimePolicy(
                timeout=1800,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            )
        )
        assert all(isinstance(issue, ValidationIssue) for issue in result)


def test_registry_runtime_capabilities_surface_is_fail_closed():
    for name, integration in REGISTRY.items():
        if name == "copilot":
            assert integration.runtime_capabilities.path_modes == frozenset({"restricted", "unrestricted"})
            assert integration.runtime_capabilities.tool_modes == frozenset({"all", "allowlist"})
        elif name == "script":
            assert integration.runtime_capabilities.path_modes == frozenset({"unrestricted"})
            assert integration.runtime_capabilities.tool_modes == frozenset({"all"})
        else:
            assert integration.runtime_capabilities.path_modes == frozenset()
            assert integration.runtime_capabilities.tool_modes == frozenset()


def test_all_execution_integrations_run_accepts_sandbox_root():
    """Every execution-capable integration.run must accept IntegrationRunRequest.

    The worker now calls typed run(request), so every execution-capable
    integration must expose that single-argument contract.
    """
    offenders = []
    for name, integration in REGISTRY.items():
        if not getattr(integration, "supports_execution", False):
            continue
        sig = inspect.signature(integration.run)
        params = list(sig.parameters.values())
        if len(params) != 1:
            offenders.append(name)
            continue
        if params[0].annotation is inspect._empty:
            offenders.append(name)
    assert offenders == [], f"integrations missing typed run(request) contract: {offenders}"


def test_registry_projector_skill_support_is_fail_closed_except_verified_integrations():
    expected = {
        "copilot": (True, True),
        "claude-code": (False, False),
        "gemini": (False, False),
    }
    for name, integration in REGISTRY.items():
        caps = integration.projector.capabilities
        if name in expected:
            assert (caps.discovers_skills, caps.activates_selected_skill) == expected[name]
        else:
            assert caps.discovers_skills is False
            assert caps.activates_selected_skill is False


def test_decision_run_allows_null_skill():
    integration = REGISTRY["copilot"]
    request = IntegrationRunRequest(
        workspace_root=Path("workspace"),
        launch_dir=Path("launch"),
        task_file=Path("launch/task.md"),
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
    assert integration.validate_run(request) == ()


def test_execution_integrations_enforce_validate_run_before_subprocess_or_prompt_read(tmp_path, monkeypatch):
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

    request.launch_dir.mkdir(parents=True)

    def fail_subprocess(*args, **kwargs):
        raise AssertionError("subprocess.run should not be reached for invalid typed runs")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)

    checked = []
    expected_skip = {"copilot"}
    for name, integration in REGISTRY.items():
        if not integration.supports_execution:
            continue
        if name in expected_skip:
            continue

        with pytest.raises(ValidationFailed):
            integration.run(request)
        checked.append(name)

    assert checked == [
        "claude-code",
        "codex",
        "gemini",
        "aider",
        "goose",
        "opencode",
        "pi",
        "script",
    ]


def test_non_executable_integrations_reject_before_any_result_is_fabricated(tmp_path):
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
        skill=None,
        skill_arguments=(),
    )

    with pytest.raises(ValidationFailed) as excinfo:
        REGISTRY["sdk"].run(request)

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-path-policy",
        "unsupported-tool-policy",
        "integration-not-executable",
    ]


def test_runresult_changed_files_defaults_empty():
    r = RunResult(exit_code=0, stdout="", stderr="", duration_seconds=1.0)
    assert r.changed_files == []


def test_filechange_fields():
    fc = FileChange(path="a.txt", status="modified", lines_added=2, lines_removed=1)
    assert fc.path == "a.txt"
    assert fc.status == "modified"
    assert fc.lines_added == 2
    assert fc.lines_removed == 1


def test_integration_rejects_policy_it_cannot_enforce(raw_config, config_paths):
    from agency.configuration import ValidationFailed, parse_config

    group = raw_config["groups"]["newsletter"]
    group["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": ["C:/repo"]},
        "tools": {"mode": "allowlist", "names": ["read"]},
    }
    agent = group["agents"][0]
    agent["name"] = "builder"
    agent["integration"] = "claude-code"

    parsed = parse_config(raw_config, config_paths["config_path"])

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(parsed.resolved, "newsletter", "builder")

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-path-policy",
        "unsupported-tool-policy",
    ]
    assert [issue.scope for issue in excinfo.value.issues] == [
        "integrations.claude-code",
        "integrations.claude-code",
    ]
    assert [issue.field for issue in excinfo.value.issues] == [
        "runtime.sandbox.mode",
        "runtime.tools.mode",
    ]

