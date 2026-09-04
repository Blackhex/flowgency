"""Contract tests: validate all registered integrations meet the BaseIntegration API."""
import inspect
import subprocess
from pathlib import Path

import pytest

from agency.configuration import ValidationFailed
from agency.configuration.issues import ValidationIssue
from agency.configuration.effective import resolve_effective_policy
from agency.integrations import (
    REGISTRY,
    AgentIdentity,
    BaseIntegration,
    FileChange,
    IntegrationError,
    RunResult
)
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ProjectorCapabilities,
    RuntimeCapabilities
)


AI_CLI_COMMANDS = {
    "copilot": "copilot",
    "claude-code": "claude",
    "gemini": "gemini",
    "codex": "codex",
    "aider": "aider",
    "goose": "goose",
    "opencode": "opencode",
    "pi": "pi",
}

UNCONFINED_CAPABILITIES = RuntimeCapabilities(
    permission_modes=frozenset({"unrestricted"})
)

AI_CLI_RUN_ARGUMENTS = {
    "claude-code": ["--dangerously-skip-permissions", "-p", "Run probe"],
    "gemini": ["-p", "Run probe"],
    "codex": ["exec", "--yolo", "Run probe"],
    "aider": ["--message-file", "{task_file}"],
    "goose": ["run", "Run probe"],
    "opencode": ["run", "Run probe"],
    "pi": ["-p", "Run probe"],
}


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
                timeout=1800
            ),
            skill=None,
            skill_arguments=()
        )
        result = integration.validate_run(request)
        assert all(isinstance(issue, ValidationIssue) for issue in result)

    def test_validate_runtime_policy_returns_validation_issues(self, integration):
        result = integration.validate_runtime_policy(
            EffectiveRuntimePolicy(
                timeout=1800
            )
        )
        assert all(isinstance(issue, ValidationIssue) for issue in result)


def test_registry_runtime_capabilities_surface_is_fail_closed():
    expected = {
        "copilot": RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"}),
            path_scopable_tools=frozenset({"write"}),
        ),
        "script": RuntimeCapabilities(
            permission_modes=frozenset({"unrestricted"})
        ),
        "sdk": RuntimeCapabilities(),
        "claude-code": UNCONFINED_CAPABILITIES,
        "gemini": UNCONFINED_CAPABILITIES,
        "codex": UNCONFINED_CAPABILITIES,
        "aider": UNCONFINED_CAPABILITIES,
        "goose": UNCONFINED_CAPABILITIES,
        "opencode": UNCONFINED_CAPABILITIES,
        "pi": UNCONFINED_CAPABILITIES,
    }

    assert {name: integration.declared_runtime_capabilities for name, integration in REGISTRY.items()} == expected

    for name, integration in REGISTRY.items():
        detected = integration.runtime_capabilities
        assert detected.permission_modes <= integration.declared_runtime_capabilities.permission_modes, (
            f"{name}: detected modes wider than declared"
        )
        assert detected.path_scopable_tools <= integration.declared_runtime_capabilities.path_scopable_tools, (
            f"{name}: detected path_scopable_tools wider than declared"
        )


def test_widening_detector_is_capped_to_declared():
    """A detector that returns capabilities wider than declared must be silently capped."""
    class WidenedIntegration(BaseIntegration):
        name = "test-widen"
        display_name = "Test Widen"
        declared_runtime_capabilities = RuntimeCapabilities(
            permission_modes=frozenset({"unrestricted"}),
        )

        def detect_runtime_capabilities(self):
            return RuntimeCapabilities(
                permission_modes=frozenset({"unrestricted", "restricted"}),
                path_scopable_tools=frozenset({"write"}),
            )

        def _capability_cache_key(self):
            return "fixed"

        def identity_filename(self):
            return ".instructions.md"

        def parse_identity(self, agent_dir):
            return None

        def write_identity(self, agent_dir, identity):
            pass

    integration = WidenedIntegration()
    caps = integration.runtime_capabilities
    assert caps.permission_modes <= integration.declared_runtime_capabilities.permission_modes
    assert caps.path_scopable_tools <= integration.declared_runtime_capabilities.path_scopable_tools


def test_builtin_ai_cli_integrations_declare_canonical_commands():
    declared = {
        name: integration.cli_command
        for name, integration in REGISTRY.items()
        if integration.cli_command is not None
    }

    assert declared == AI_CLI_COMMANDS
    assert REGISTRY["script"].cli_command is None
    assert REGISTRY["sdk"].cli_command is None


def test_builtin_ai_cli_runtime_capabilities_are_truthful(monkeypatch):
    copilot = REGISTRY["copilot"]

    # Stubbed both ways so the claim is pinned regardless of what this machine
    # happens to have installed.
    monkeypatch.setattr(type(copilot), "_cli_version", lambda self: "1.0.78-2")
    copilot.invalidate_capability_cache()
    try:
        assert copilot.runtime_capabilities == RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"}),
            path_scopable_tools=frozenset({"write"}),
        )
    finally:
        copilot.invalidate_capability_cache()

    monkeypatch.setattr(type(copilot), "_cli_version", lambda self: None)
    copilot.invalidate_capability_cache()
    try:
        assert copilot.runtime_capabilities == RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"})
        )
    finally:
        copilot.invalidate_capability_cache()

    for name in AI_CLI_COMMANDS.keys() - {"copilot"}:
        assert REGISTRY[name].runtime_capabilities == UNCONFINED_CAPABILITIES


def test_public_executable_resolver_returns_launchable_path(tmp_path, monkeypatch):
    executable = tmp_path / "probe.exe"
    executable.write_bytes(b"")
    executable.chmod(0o755)

    class ProbeIntegration(BaseIntegration):
        name = "probe"
        display_name = "Probe"
        cli_command = "probe"

    monkeypatch.setattr(
        "agency.integrations.shutil.which",
        lambda command: str(executable) if command == "probe" else None
    )

    assert ProbeIntegration().resolve_executable() == str(executable.resolve())


def test_public_executable_resolver_returns_none_when_missing(monkeypatch):
    class ProbeIntegration(BaseIntegration):
        name = "probe"
        display_name = "Probe"
        cli_command = "probe"

    monkeypatch.setattr("agency.integrations.shutil.which", lambda command: None)

    assert ProbeIntegration().resolve_executable() is None
    with pytest.raises(IntegrationError, match="Probe CLI is unavailable"):
        ProbeIntegration().require_executable()


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


def test_builtin_ai_cli_projectors_declare_instruction_discovery():
    for name in AI_CLI_COMMANDS:
        assert REGISTRY[name].projector.capabilities.discovers_instructions is True
    assert REGISTRY["script"].projector.capabilities.discovers_instructions is False
    assert REGISTRY["sdk"].projector.capabilities.discovers_instructions is False


def test_default_projector_requires_explicit_instruction_discovery_flag():
    parameter = inspect.signature(BaseIntegration._default_projector).parameters[
        "discovers_instructions"
    ]
    assert parameter.default is inspect._empty


def test_decision_run_allows_null_skill():
    integration = REGISTRY["copilot"]
    request = IntegrationRunRequest(
        workspace_root=Path("workspace"),
        launch_dir=Path("launch"),
        task_file=Path("launch/task.md"),
        timeout=60,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=60
        ),
        skill=None,
        skill_arguments=()
    )
    assert integration.validate_run(request) == ()


def test_execution_integrations_enforce_validate_run_before_subprocess_or_prompt_read(tmp_path, monkeypatch):
    request = IntegrationRunRequest(
        workspace_root=tmp_path / "workspace",
        launch_dir=tmp_path / "launch",
        task_file=tmp_path / "launch" / "task.md",
        timeout=60,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=60
        ),
        skill=None,
        skill_arguments=()
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


@pytest.mark.parametrize(
    ("integration_name", "expected_tail"),
    AI_CLI_RUN_ARGUMENTS.items()
)
def test_ai_cli_run_uses_declared_command_and_validated_request(
    integration_name,
    expected_tail,
    tmp_path,
    monkeypatch
):
    integration = REGISTRY[integration_name]
    launch_dir = tmp_path / "launch"
    workspace_root = tmp_path / "workspace"
    task_file = tmp_path / "task.md"
    launch_dir.mkdir()
    workspace_root.mkdir()
    task_file.write_text("Run probe", encoding="utf-8")
    request = IntegrationRunRequest(
        workspace_root=workspace_root,
        launch_dir=launch_dir,
        task_file=task_file,
        timeout=30,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=30
        ),
        skill=None,
        skill_arguments=()
    )
    captured = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(arguments, 0, "ok", "")

    monkeypatch.setattr(
        integration,
        "resolve_executable",
        lambda: "C:/runtime/agent.exe"
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = integration.run(request)

    normalized_tail = [
        str(task_file) if value == "{task_file}" else value
        for value in expected_tail
    ]
    assert captured["arguments"] == [
        "C:/runtime/agent.exe",
        *normalized_tail,
    ]
    assert captured["kwargs"]["cwd"] == str(launch_dir)
    assert captured["kwargs"]["timeout"] == 30
    assert result.exit_code == 0


def test_non_executable_integrations_reject_before_any_result_is_fabricated(tmp_path):
    request = IntegrationRunRequest(
        workspace_root=tmp_path / "workspace",
        launch_dir=tmp_path / "launch",
        task_file=tmp_path / "launch" / "task.md",
        timeout=60,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=60
        ),
        skill=None,
        skill_arguments=()
    )

    with pytest.raises(ValidationFailed) as excinfo:
        REGISTRY["sdk"].run(request)

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-permission-mode",
        "integration-not-executable",
    ]


def test_runresult_changed_files_defaults_empty():
    r = RunResult(exit_code=0, stdout="", stderr="", duration_seconds=1.0)
    assert r.changed_files == []
    assert r.write_attempts == []


def test_filechange_fields():
    fc = FileChange(path="a.txt", status="modified", lines_added=2, lines_removed=1)
    assert fc.path == "a.txt"
    assert fc.status == "modified"
    assert fc.lines_added == 2
    assert fc.lines_removed == 1


def test_integration_rejects_policy_it_cannot_enforce(raw_config, config_paths):
    from agency.configuration import ValidationFailed, parse_config

    team = raw_config["teams"]["newsletter"]
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [{"path": "C:/repo", "tools": ["read"]}],
        },
    }
    agent = team["agents"][0]
    agent["name"] = "builder"
    agent["integration"] = "claude-code"

    parsed = parse_config(raw_config, config_paths["config_path"])

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(parsed.resolved, "newsletter", "builder")

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-permission-mode",
    ]
    assert [issue.scope for issue in excinfo.value.issues] == [
        "integrations.claude-code",
    ]
    assert [issue.field for issue in excinfo.value.issues] == [
        "runtime.permissions.mode",
    ]

